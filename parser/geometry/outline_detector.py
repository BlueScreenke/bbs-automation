"""
parser/geometry/outline_detector.py

Step 6-pre — detects each beam's outline rectangle directly from vector
geometry, then assigns the nearest "MBM n" text label to it. Replaces the
old label-first approach (dimension_extractor.get_beam_boxes(), now
retired) that computed a beam's zone purely from where its label sat
relative to its neighbours' labels, without ever looking at what was
actually drawn.

Why this replaced the label-first approach
--------------------------------------------
Confirmed against Page_1_beams.pdf: any drawing row with only one "MBM n"
label defaulted its old box to the FULL PAGE WIDTH (no neighbour to split
the x-boundary against) — this affected 8 of 17 beams on that page, not
a rare edge case. Two concrete failures traced back to this:

  - MBM 09's old y-band [785.1, 853.3] (computed from label midpoints)
    sat entirely *below* its real outline at y=[714.6, 749.8] — the real
    outline was never even a candidate, because the old approach only
    ever searched *inside* the box the label math handed it. As a
    result MBM 09's own callouts (2T20-51(B1), 2T16-55(B1), ...) were
    captured under MBM 08's zone, while MBM 09's box instead swallowed
    MBM 10's and MBM 11's callout text verbatim.
  - MBM 12 failed the same way (old band [920.3, 1003.6] vs. real
    outline [865.0, 900.6]).

Reordering to geometry-first (find real outline rectangles, then match
the nearest label to each) fixes both at the source instead of patching
around a box that already excluded the truth.

Colour-agnostic by design
--------------------------
Page_1_beams.pdf happens to stroke beam outlines in a distinct green,
and bar_detector.py's classification work still benefits from that as a
confidence signal where available. This module does not require it —
outlines are found by SHAPE alone (a pair of long horizontal edges a
plausible beam-depth apart, with strong x-overlap), so a future drawing
with no consistent outline colour still works.

The x-contiguity pitfall (and why individual-edge pairing matters)
---------------------------------------------------------------------
A naive version of this approach — cluster all same-y lines page-wide,
then pair whole y-clusters by depth — was tried first and produced a
worse bug than the one being fixed: row 1's three separate beams
(MBM 01/02/03) share the same outline y (drawn at a consistent
elevation), so a whole-cluster union merged all three into one fake
outline spanning their combined x-range, re-creating cross-beam
contamination in a new form.

Fix: within each y-level, first split lines into distinct edge-groups by
x-contiguity (X_FUSE_GAP, see below) *before* any pairing happens.
Pairing then happens between individual edge-groups, never whole
y-clusters. Validated: this resolves all 17 beams on Page_1_beams.pdf to
distinct, non-colliding outlines, including MBM 09 and MBM 12.

X_FUSE_GAP is measured, not guessed
--------------------------------------
Every inter-segment gap on Page_1_beams.pdf was measured page-wide. The
distribution is cleanly bimodal with an empty band in between:
  - 0-30pt: duplicate/double-stroked edges of the same physical line,
    and small real seams within one continuous edge.
  - 90pt+: genuinely distinct beams/columns at the same drawn elevation.
  - 30-90pt: zero observed gaps.
50pt sits in the middle of that empty band. A future drawing with
tighter real spacing between beams would need this re-measured (the
same gap-histogram approach used here works generically) rather than
assuming 50pt is universal.

Fallback tiers
--------------
  1. "paired_edges" (primary) — both a top and bottom outline edge found
     via the process above. The common case; resolved all 17/17 beams
     on the validation drawing.
  2. "single_edge_section_box" — only one edge found near the label.
     Depth is borrowed from the nearest small section-detail rectangle
     (the "A-A" box drawn beside most elevations), sized distinctly
     from both the main outline and small annotation markers, in
     whatever colour it happens to be drawn. Orientation (is the found
     edge the top or bottom?) is decided by which side has more
     bar-line-length horizontal content — the wrong side is cut off by
     the true boundary and shows near-zero content.
  3. "label_midpoint" (last resort) — the original label-position math:
     split the boundary between two labels' x/y midpoints. Used only
     when a label has no nearby outline geometry at all. Least
     reliable of the three, kept only as a safety net.

Public surface
--------------
    detect_beam_boxes(words, primitives, page_width, page_height,
                       page_no=1, sidebar_x_limit=None) -> list[dict]
"""

from __future__ import annotations

import re
from typing import Optional

from parser.geometry.bar_detector import (
    _find_diagonal_ticks,
    _find_small_triangles,
    _is_annotation_terminated,
)
from parser.geometry.primitives import LineSegment, PagePrimitives


# ── Tuneable constants ────────────────────────────────────────────────────────

# A candidate outline edge must be at least this long (excludes bar
# segments, dimension lines, small annotations) but no longer than this
# (excludes page borders / title-block frames).
MIN_OUTLINE_LINE_LENGTH = 100.0
MAX_OUTLINE_LINE_LENGTH = 700.0

# Plausible beam depth range, in points. Derived from observed ~600mm-
# deep beams at this drawing's scale (~34pt) with margin either side for
# other sections (e.g. 250x700mm, observed at ~41.6pt) and other
# drawings' scales.
MIN_BEAM_DEPTH = 15.0
MAX_BEAM_DEPTH = 80.0

# Group candidate edges within this y-tolerance as "the same edge level"
# (absorbs rounding / near-duplicate strokes at the same nominal edge).
Y_CLUSTER_TOL = 2.0

# See module docstring "X_FUSE_GAP is measured, not guessed" — gaps
# under this split the same physical edge (duplicate stroke / seam);
# gaps over this are a genuinely different beam/column at the same
# drawn elevation. Measured on Page_1_beams.pdf: clean empty band
# between 30pt and 90pt: this sits in the middle of it.
X_FUSE_GAP = 50.0

# How much two paired edge-groups' x-ranges must overlap (as a fraction
# of the shorter one) to be considered top/bottom of the same rectangle.
# Tightened vs. the whole-cluster-union approach's 0.5, since pairing
# now happens between individual pre-fused edge-groups rather than
# merged clusters, so a spurious partial overlap is less likely to be
# a real match.
MIN_X_OVERLAP_FRACTION = 0.6

# Small outward pad applied to the *tight* outline bounds (used for bar-
# line geometry search), so a bar line sitting exactly at the drawn edge
# isn't excluded by an off-by-a-point rounding difference.
BOX_PAD = 5.0

# Callout text in this drawing convention is not always inside the
# outline's own depth band — a callout's leader arrow commonly runs from
# a label positioned above/below the beam to the bar line it refers to
# (this is why bar_matcher.py exists at all). A word-capture zone using
# only the tight outline would miss these labels. TEXT_Y_MARGIN widens
# the *text*-capture band beyond the tight outline; it's bounded well
# under the smallest observed row-to-row gap (~90-105pt on the
# validation drawing), so it can't reach into a neighbouring beam's row.
TEXT_Y_MARGIN = 50.0
TEXT_X_MARGIN = 20.0

# How far in x a label may sit from its outline's own x-range and still
# be considered "this label names this outline".
LABEL_X_MARGIN = 30.0

# How far below an outline's bottom edge a label may sit and still be
# matched to it. Generous enough for the largest observed gap on the
# validation drawing (~44pt) with margin, but bounded so a label can't
# bind to a completely unrelated outline several rows away.
LABEL_MAX_Y_GAP = 150.0

# Tier 2 (single edge + section box) tuning — ported from
# bar_detector.py's section-box fallback, but colour-agnostic here.
SECTION_BOX_MIN_WIDTH  = 5.0
SECTION_BOX_MAX_WIDTH  = 40.0
SECTION_BOX_MIN_HEIGHT = 15.0
SECTION_BOX_MAX_HEIGHT = 70.0
MIN_BAR_LINE_LENGTH     = 15.0

# Tier 3 (label-midpoint fallback) — ported unchanged from the retired
# dimension_extractor.get_beam_boxes().
ROW_Y_TOLERANCE   = 15
X_BOUNDARY_MARGIN = 50

_SIDEBAR_MARKER_RE = re.compile(r'^NOTES?:?$', re.I)


# ── Public API ────────────────────────────────────────────────────────────────

def detect_beam_boxes(
    words:           list[dict],
    primitives:      PagePrimitives,
    page_width:       float,
    page_height:      float,
    page_no:          int = 1,
    sidebar_x_limit:  Optional[float] = None,
) -> list[dict]:
    """
    Detect a bounding box for every beam elevation on the page, anchored
    to real outline geometry wherever possible.

    Args:
        words:           pdfplumber word dicts for the page.
        primitives:      Full-page geometry (pdf_line_extractor output).
        page_width:      Page width in points.
        page_height:     Page height in points.
        page_no:         1-indexed page number (stored in each box).
        sidebar_x_limit: x-coordinate a title-block/notes sidebar starts
                          at, if any — candidate search never crosses it.
                          If omitted, this is estimated from the words
                          themselves (see _estimate_sidebar_x_limit);
                          pass it explicitly if that heuristic is wrong
                          for a given drawing template.

    Returns:
        List of dicts, each with: id, page, x_left, x_right, y_top,
        y_bot, label_x, label_y, outline_source (one of "paired_edges",
        "single_edge_section_box", "label_midpoint" — for traceability).
    """
    if sidebar_x_limit is None:
        sidebar_x_limit = _estimate_sidebar_x_limit(words, page_width)

    anchors = _find_mbm_anchors(words)

    ticks     = _find_diagonal_ticks(primitives.lines)
    triangles = _find_small_triangles(primitives.polylines)
    edges     = _individuated_edges(primitives, sidebar_x_limit, ticks, triangles)
    outlines  = _pair_edges_into_outlines(edges)
    section_boxes = _find_section_boxes_any_colour(primitives.polylines)

    boxes: list[dict] = []
    used_outline_idx: set[int] = set()

    matched, unmatched = _assign_labels_to_outlines(anchors, outlines, used_outline_idx)
    for anchor, outline in matched:
        boxes.append(_box_from_outline(anchor, outline, page_no, "paired_edges"))

    still_unmatched: list[dict] = []
    for anchor in unmatched:
        result = _try_single_edge_section_box(
            anchor, edges, section_boxes, primitives, sidebar_x_limit,
        )
        if result is not None:
            boxes.append(_box_from_single_edge(anchor, result, page_no))
        else:
            still_unmatched.append(anchor)

    if still_unmatched:
        rows = _cluster_into_rows(anchors)
        fallback_boxes = _fallback_boxes_from_labels(rows, page_width, page_no)
        still_unmatched_ids = {(a['id'], a['x'], a['y']) for a in still_unmatched}
        for fb in fallback_boxes:
            key = (fb['id'], fb['label_x'], fb['label_y'])
            if key in still_unmatched_ids:
                fb['outline_source'] = "label_midpoint"
                boxes.append(fb)

    boxes.sort(key=lambda b: (b['label_y'], b['label_x']))
    return boxes


# ── Tier 1: paired outline edges ──────────────────────────────────────────────

def _individuated_edges(
    primitives:      PagePrimitives,
    x_right_cap:     float,
    ticks:           list[LineSegment],
    triangles:       list,
) -> list[dict]:
    """
    Every distinct physical outline edge on the page, as {y, x_left,
    x_right}. "Distinct" means: grouped by y-level, then split within
    each y-level by x-contiguity (see module docstring) so that separate
    beams sharing a drawn elevation are never merged into one.
    """
    candidates = _gather_candidate_lines(primitives, x_right_cap)
    non_annotation = [l for l in candidates if not _is_annotation_terminated(l, ticks, triangles)]

    y_clusters = _cluster_by_y(non_annotation)

    edges: list[dict] = []
    for y, lines in y_clusters:
        for group in _fuse_by_x_contiguity(lines, X_FUSE_GAP):
            edges.append({
                "y":       y,
                "x_left":  min(l.x_left for l in group),
                "x_right": max(l.x_right for l in group),
            })
    return edges


def _gather_candidate_lines(primitives: PagePrimitives, x_right_cap: float) -> list[LineSegment]:
    """Long, plausible-length horizontal edges page-wide — both loose
    LineSegments and edges of grouped Polylines (an outline can be drawn
    either way)."""
    result = [
        l for l in primitives.lines
        if l.is_horizontal
        and MIN_OUTLINE_LINE_LENGTH <= l.length <= MAX_OUTLINE_LINE_LENGTH
        and l.x_right <= x_right_cap
    ]
    for p in primitives.polylines:
        pts = p.points + ([p.points[0]] if p.closed else [])
        for a, b in zip(pts, pts[1:]):
            length = abs(b.x - a.x)
            if abs(b.y - a.y) < 2.0 and MIN_OUTLINE_LINE_LENGTH <= length <= MAX_OUTLINE_LINE_LENGTH:
                if max(a.x, b.x) <= x_right_cap:
                    result.append(LineSegment(start=a, end=b, stroke=p.stroke))
    return result


def _cluster_by_y(lines: list[LineSegment]) -> list[tuple[float, list[LineSegment]]]:
    """Group lines into distinct y-levels, sorted top to bottom."""
    ordered = sorted(lines, key=lambda l: l.mid_y)
    clusters: list[tuple[float, list[LineSegment]]] = []
    for l in ordered:
        if clusters and l.mid_y - clusters[-1][0] <= Y_CLUSTER_TOL:
            clusters[-1][1].append(l)
        else:
            clusters.append((l.mid_y, [l]))
    return clusters


def _fuse_by_x_contiguity(lines: list[LineSegment], gap_tol: float) -> list[list[LineSegment]]:
    """Within one y-level, split into distinct edge-groups by x-gap. See
    module docstring for the empirical derivation of gap_tol."""
    ordered = sorted(lines, key=lambda l: l.x_left)
    groups: list[list[LineSegment]] = []
    for l in ordered:
        if groups:
            last_right = max(x.x_right for x in groups[-1])
            if l.x_left - last_right <= gap_tol:
                groups[-1].append(l)
                continue
        groups.append([l])
    return groups


def _pair_edges_into_outlines(edges: list[dict]) -> list[dict]:
    """Depth-plausible, strongly x-overlapping pairs of individuated
    edges become candidate outline rectangles. Uses the union of the two
    edges' x-ranges (both are already trusted, individuated edges — not
    a merged cluster — so union is the physically correct rectangle even
    if one edge overshoots the other slightly at a corner)."""
    outlines = []
    for i, e1 in enumerate(edges):
        for j, e2 in enumerate(edges):
            if i == j:
                continue
            depth = e2["y"] - e1["y"]
            if not (MIN_BEAM_DEPTH <= depth <= MAX_BEAM_DEPTH):
                continue
            overlap = min(e1["x_right"], e2["x_right"]) - max(e1["x_left"], e2["x_left"])
            shorter = min(e1["x_right"] - e1["x_left"], e2["x_right"] - e2["x_left"])
            if shorter <= 0 or overlap / shorter < MIN_X_OVERLAP_FRACTION:
                continue
            outlines.append({
                "y_top":   e1["y"],
                "y_bot":   e2["y"],
                "x_left":  min(e1["x_left"], e2["x_left"]),
                "x_right": max(e1["x_right"], e2["x_right"]),
                "depth":   depth,
            })
    return outlines


def _assign_labels_to_outlines(
    anchors:  list[dict],
    outlines: list[dict],
    used_idx: set[int],
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    """
    Match each label to its nearest outline: the label must sit at or
    below the outline's bottom edge (within LABEL_MAX_Y_GAP) and within
    LABEL_X_MARGIN of its x-range — this is the "label sits just below
    its outline" convention confirmed on the validation drawing.

    Each outline may be claimed by at most one label (first-come by
    smallest gap, processed in ascending gap order overall) — this is
    what prevents the whole-cluster-merge failure mode from recurring
    even if two labels' search windows happen to overlap.
    """
    scored: list[tuple[float, int, dict]] = []
    for a_idx, a in enumerate(anchors):
        for o_idx, o in enumerate(outlines):
            gap = a['y'] - o['y_bot']
            if not (0 <= gap <= LABEL_MAX_Y_GAP):
                continue
            if not (o['x_left'] - LABEL_X_MARGIN <= a['x'] <= o['x_right'] + LABEL_X_MARGIN):
                continue
            scored.append((gap, a_idx, o_idx))

    scored.sort(key=lambda t: t[0])
    claimed_anchor: set[int] = set()
    claimed_outline: set[int] = set()
    matched: list[tuple[dict, dict]] = []

    for gap, a_idx, o_idx in scored:
        if a_idx in claimed_anchor or o_idx in claimed_outline:
            continue
        claimed_anchor.add(a_idx)
        claimed_outline.add(o_idx)
        used_idx.add(o_idx)
        matched.append((anchors[a_idx], outlines[o_idx]))

    unmatched = [a for i, a in enumerate(anchors) if i not in claimed_anchor]
    return matched, unmatched


def _box_from_outline(anchor: dict, outline: dict, page_no: int, source: str) -> dict:
    """
    Builds two nested ranges into one box dict:
      - outline_y_top/outline_y_bot, x_left/x_right: the tight, trusted
        outline bounds — bar_detector.py uses these directly for bar-
        line geometry search instead of re-deriving them.
      - y_top/y_bot: the wider text-capture band (see TEXT_Y_MARGIN) —
        parser.py's word-in-box filtering uses these so leader-arrow-
        connected callout labels outside the tight outline aren't missed.
    x_left/x_right double as both the outline's and the text zone's x
    bounds (padded by TEXT_X_MARGIN), since callouts in this drawing sit
    within the beam's own x-span, not offset from it the way labels are
    offset in y.
    """
    return {
        "id":              anchor["id"],
        "page":            page_no,
        "x_left":          outline["x_left"] - TEXT_X_MARGIN,
        "x_right":         outline["x_right"] + TEXT_X_MARGIN,
        "y_top":           outline["y_top"] - TEXT_Y_MARGIN,
        "y_bot":           outline["y_bot"] + TEXT_Y_MARGIN,
        "outline_y_top":   outline["y_top"] - BOX_PAD,
        "outline_y_bottom":   outline["y_bot"] + BOX_PAD,
        "label_x":         anchor["x"],
        "label_y":         anchor["y"],
        "outline_source":      source,
    }


# ── Tier 2: single edge + section-box depth ───────────────────────────────────

def _try_single_edge_section_box(
    anchor:          dict,
    edges:           list[dict],
    section_boxes:   list[tuple[float, float, float, float]],
    primitives:      PagePrimitives,
    sidebar_x_limit: float,
) -> Optional[dict]:
    """
    For a label with no paired-edge match: find the single nearest
    qualifying edge above it, borrow depth from the nearest section-
    detail rectangle, and decide orientation by which side of the known
    edge has more bar-line-length horizontal content (the wrong side is
    cut off by the true boundary and shows near-zero content).
    """
    candidates = [
        e for e in edges
        if 0 <= anchor['y'] - e['y'] <= LABEL_MAX_Y_GAP
        and e['x_left'] - LABEL_X_MARGIN <= anchor['x'] <= e['x_right'] + LABEL_X_MARGIN
    ]
    if not candidates:
        return None
    known = min(candidates, key=lambda e: anchor['y'] - e['y'])

    anchor_x = (known['x_left'] + known['x_right']) / 2
    box_match = _nearest_section_box(section_boxes, anchor_x, known['y'])
    if box_match is None:
        return None
    depth = box_match[3] - box_match[1]

    known_y = known['y']
    below = [
        l for l in primitives.lines
        if l.is_horizontal and l.length >= MIN_BAR_LINE_LENGTH
        and known['x_left'] <= l.mid_x <= known['x_right']
        and known_y < l.mid_y <= known_y + depth
    ]
    above = [
        l for l in primitives.lines
        if l.is_horizontal and l.length >= MIN_BAR_LINE_LENGTH
        and known['x_left'] <= l.mid_x <= known['x_right']
        and known_y - depth <= l.mid_y < known_y
    ]

    if len(below) >= len(above):
        y_top, y_bot = known_y, known_y + depth
    else:
        y_top, y_bot = known_y - depth, known_y

    return {"y_top": y_top, "y_bot": y_bot, "x_left": known['x_left'], "x_right": known['x_right']}


def _find_section_boxes_any_colour(polylines) -> list[tuple[float, float, float, float]]:
    """Small rectangles sized like the "A-A" cross-section detail, in
    whatever colour they happen to be drawn (colour-agnostic version of
    bar_detector._find_section_boxes)."""
    result = []
    for p in polylines:
        bb = p.bounding_box
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if SECTION_BOX_MIN_WIDTH <= w <= SECTION_BOX_MAX_WIDTH and SECTION_BOX_MIN_HEIGHT <= h <= SECTION_BOX_MAX_HEIGHT:
            result.append(bb)
    return result


def _nearest_section_box(
    section_boxes: list[tuple[float, float, float, float]],
    anchor_x:      float,
    anchor_y:      float,
) -> Optional[tuple[float, float, float, float]]:
    if not section_boxes:
        return None
    return min(
        section_boxes,
        key=lambda bb: ((bb[0] + bb[2]) / 2 - anchor_x) ** 2 + ((bb[1] + bb[3]) / 2 - anchor_y) ** 2,
    )


def _box_from_single_edge(anchor: dict, outline: dict, page_no: int) -> dict:
    return {
        "id":              anchor["id"],
        "page":            page_no,
        "x_left":          outline["x_left"] - TEXT_X_MARGIN,
        "x_right":         outline["x_right"] + TEXT_X_MARGIN,
        "y_top":           outline["y_top"] - TEXT_Y_MARGIN,
        "y_bot":           outline["y_bot"] + TEXT_Y_MARGIN,
        "outline_y_top":   outline["y_top"] - BOX_PAD,
        "outline_y_bottom":   outline["y_bot"] + BOX_PAD,
        "label_x":         anchor["x"],
        "label_y":         anchor["y"],
        "outline_source":      "single_edge_section_box",
    }


# ── Tier 3: label-midpoint fallback (ported from retired get_beam_boxes) ──────

def _find_mbm_anchors(words: list[dict]) -> list[dict]:
    anchors = []
    for w in words:
        if w['text'] == 'MBM':
            siblings = sorted(
                [x for x in words if abs(x['top'] - w['top']) < 6 and x['x0'] > w['x0']],
                key=lambda x: x['x0'],
            )
            name = siblings[0]['text'] if siblings else '?'
            anchors.append({'id': f"MBM {name}", 'x': w['x0'], 'y': w['top']})
    anchors.sort(key=lambda a: (a['y'], a['x']))
    return anchors


def _cluster_into_rows(anchors: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for anchor in anchors:
        placed = False
        for row in rows:
            if abs(anchor['y'] - row[0]['y']) < ROW_Y_TOLERANCE:
                row.append(anchor)
                placed = True
                break
        if not placed:
            rows.append([anchor])
    return rows


def _fallback_boxes_from_labels(
    rows:       list[list[dict]],
    page_width: float,
    page_no:    int,
) -> list[dict]:
    """
    Original label-midpoint box math — last-resort fallback only, for
    labels with no findable outline geometry at all. No outline_y_top/
    outline_y_bot is set (None): bar_detector.py falls back to its own
    internal colour-tiered outline search for exactly this case, rather
    than trusting a box with no real geometric basis.
    """
    boxes = []
    for r_idx, row in enumerate(rows):
        row = sorted(row, key=lambda a: a['x'])
        y_row   = row[0]['y']
        y_above = rows[r_idx-1][0]['y'] if r_idx > 0 else 0
        y_below = rows[r_idx+1][0]['y'] if r_idx+1 < len(rows) else y_row + 200
        y_top   = (y_row + y_above) / 2
        y_bot   = (y_row + y_below) / 2

        for b_idx, beam in enumerate(row):
            x_left = (
                (row[b_idx-1]['x'] + beam['x']) / 2 + X_BOUNDARY_MARGIN
                if b_idx > 0 else 0
            )
            x_right = (
                (beam['x'] + row[b_idx+1]['x']) / 2 - X_BOUNDARY_MARGIN
                if b_idx+1 < len(row) else page_width
            )
            boxes.append({
                'id':      beam['id'],
                'page':    page_no,
                'x_left':  x_left,
                'x_right': x_right,
                'y_top':   y_top,
                'y_bot':   y_bot,
                'label_x': beam['x'],
                'label_y': beam['y'],
                'outline_y_top': None,
                'outline_y_bottom': None,
            })
    return boxes


# ── Sidebar auto-detection ────────────────────────────────────────────────────

def _estimate_sidebar_x_limit(words: list[dict], page_width: float) -> float:
    """
    Estimate where a title-block/notes sidebar starts, so outline search
    never crosses into it. Looks for a "NOTES:" (or similar) label and
    uses its x0 as the boundary. Falls back to the page width (no cap)
    if nothing matching is found — degrading to "search the whole page"
    is safer than guessing a wrong cutoff.
    """
    for w in words:
        if _SIDEBAR_MARKER_RE.match(w['text'].strip()):
            return max(0.0, w['x0'] - 20.0)
    return page_width


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import pdfplumber
    from parser.geometry.pdf_line_extractor import extract_page_primitives

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        page_w, page_h = page.width, page.height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)

    for b in boxes:
        print(f"{b['id']:10} source={b['outline_source']:24} "
              f"x=[{b['x_left']:.1f},{b['x_right']:.1f}] y=[{b['y_top']:.1f},{b['y_bot']:.1f}]")