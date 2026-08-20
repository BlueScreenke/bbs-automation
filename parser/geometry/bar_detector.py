"""
parser/geometry/bar_detector.py

Step 6B — identifies reinforcing bar lines within each beam elevation zone
and assembles them into complete PhysicalBar objects: full merged
coordinate spans, with true hook/lap/straight classification already
resolved at both ends. Every downstream module (bar_matcher, length
calculation, shape resolution) consumes PhysicalBar — none of them
re-derive "is this a true end or does it continue" themselves anymore.

Why assembly moved here (redesign, this session)
----------------------------------------------------
This module used to hand out raw bar_lines: LineSegment fragments,
broken at every column crossing (separate PDF path objects, even where
physically one continuous bar) and at every lap point. A separate
module (endpoint_classifier.py) then had to reverse-engineer, per
matched callout, whether a given fragment's end was a true open end or
just an artifact of the PDF's own path structure. That worked, but
meant every consumer re-ran the same geometry search independently.
Since the underlying signal (a short connector line at the junction,
classified by its y-shift — see below) is purely geometric and has
nothing to do with which callout matched which fragment, it belongs
here, computed once, upstream of matching.

Empirical basis (measured against the real Page_1_beams.pdf)
----------------------------------------------------------------
HOOK signature — confirmed on T16, T20, T25 at multiple beams:
    A tiny CubicBezier (chord ~1.7-2.2pt) landing within ~2pt of the
    bar's own endpoint, feeding a vertical LineSegment leg ~28-34pt
    long (about the beam's own depth). Identical across diameters —
    hooks are never drawn to true BS8666 length, only marked
    symbolically. Hook *length* always comes from
    length_calculator.py's diameter-based allowance table, never from
    this geometry.

JUNCTION signature (connector + y-shift) — this is the key correction
from the previous version of this module. A short connector line
touching a fragment's endpoint, whose far end lands near another
fragment's own endpoint, was initially read as "this is one continuous
bar, split into two PDF paths by the column crossing over it." That
reading is WRONG for most cases on this drawing. Measuring the y-shift
between the connector's far end and the partner fragment's endpoint
across every such junction on the page shows a clean bimodal split:
    - ~0.0-0.04pt (indistinguishable from float noise): a true,
      lapless, seamless continuation — confirmed visually (MBM 03,
      clean pass-through at a column, no tick mark drawn at all).
    - ~0.86-1.3pt: a small, deliberate vertical offset. Confirmed
      directly (project domain knowledge): this is how the drawing
      marks a LAP — e.g. MBM 04's marks 18/22/24 and 23/17 are
      genuinely separate bars, each lapping the next, not one bar
      administratively relabelled. The slight shift is the drawn
      signal distinguishing "two bars, lapped" from "one bar,
      continuing."
Only a ~0 shift merges two fragments into one PhysicalBar. A lap-range
shift keeps them as two separate PhysicalBar objects, linked via
lap-partner metadata on the adjoining ends.

GAP-STYLE lap (no connector at all) — a second, rarer lap signature:
a genuinely empty gap (nothing drawn at all, page-wide search
confirmed) between one bar's end and another bar_line's endpoint at the
same y, within a plausible distance (e.g. MBM 07 marks 45/46). Checked
only after the connector search fails to find anything, since most laps
on this drawing use the shift signature, not a bare gap.

CURTAILMENT ends — a third, non-lap, non-hook, non-merge signature: a
vertical line at the endpoint running outside the beam's own outline
depth band, toward a bend/curtailment-distance dimension printed above
or below the beam (e.g. MBM 04 mark 21's outer ends). No hook, no lap —
a bar simply terminating mid-span at a calculated cut-off point,
anchored by straight development length. Distinguished from a hook leg
by staying OUTSIDE the outline depth (a hook leg stays inside it).

Classification order per true (post-merge) end
--------------------------------------------------
    1. hook          — bezier + vertical leg signature.
    2. lap            — connector + lap-range shift, OR a genuine empty
                        gap to another bar_line at the same y. Lap
                        length = a dimension found tightly bracketing
                        the junction if drawn on the page, else None
                        (the 50 x diameter default needs a diameter,
                        which this module — deliberately format- and
                        callout-agnostic — does not have; that default
                        is applied downstream once a callout with a
                        known diameter is matched to this end).
    3. straight       — a curtailment marker, or no marker of any kind
                        at all (verified: MBM 05 mark 36, a plain short
                        bottom bar with nothing drawn at either end).

Dimension-line artifacts excluded from bar candidacy
--------------------------------------------------------
Confirmed real case (MBM 04, between the B1 and B2 layers): a
horizontal line reading "1690"/"1490" dimension values passes every
other bar-candidate filter (black, horizontal, >=15pt, within the
outline's y-band) because it's terminated by vertical witness lines
rather than this drawing's more common diagonal-tick or filled-triangle
dimension markers (which the existing tick/triangle exclusion already
catches). Direct inspection: no bezier at either end (rules out a
hook), and the value's own text sits tightly centred under the
candidate's own midpoint (within a few pt) — unlike a genuine curtailed
bar's own cutoff-distance dimension, which sits offset from the bar's
own centre, measuring distance *from a column*, not labelling the bar
itself. That tight-centring is the specific, narrow signal used here
(via the optional `words` argument) — deliberately not a general
dimension-line detector, since an earlier general version tried in
dimension_extractor.py over-matched (135 spurious candidates page-wide,
including genuine bars) by relying on geometry alone.

Public surface
--------------
    detect_bars(primitives, beam_boxes, dimension_matches=None, words=None) -> list[BeamBarGeometry]

BeamBarGeometry now carries `physical_bars: list[PhysicalBar]` as its
primary output. `bar_lines` (the raw, pre-assembly fragments) is kept
for debugging/traceability only — downstream modules should use
physical_bars.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from parser.geometry.primitives import (
    Colour,
    CubicBezier,
    LineSegment,
    PagePrimitives,
    Point,
    Polyline,
)


# ── Tuneable constants ────────────────────────────────────────────────────────

MIN_BAR_LINE_LENGTH = 15.0

OUTLINE_COLOUR_RGB          = (0.0, 0.741, 0.369)
COLOUR_MATCH_TOL            = 0.02
MIN_OUTLINE_SEGMENT_LENGTH  = 80.0
SECTION_BOX_MIN_WIDTH       = 5.0
SECTION_BOX_MAX_WIDTH       = 40.0
SECTION_BOX_MIN_HEIGHT      = 15.0
SECTION_BOX_MAX_HEIGHT      = 70.0
VERTICALS_Y_SEARCH_PAD      = 30.0
ORIENTATION_SEARCH_PAD      = 0.0

OUTLINE_Y_TOLERANCE = 3.0

ANNOTATION_MARKER_MAX_SIZE   = 12.0
ANNOTATION_MARKER_SEARCH_TOL = 8.0
ANNOTATION_MARKER_Y_TOLERANCE = 2.5

LEADER_SHAFT_Y_MARGIN = 3.0
LEADER_SHAFT_MAX_SIZE = 50.0

HOOK_BEZIER_SEARCH_TOL = 3.0
HOOK_BEZIER_MAX_CHORD  = 6.0
HOOK_LEG_SEARCH_TOL    = 4.0
HOOK_LEG_MIN_LENGTH    = 10.0
HOOK_LEG_MAX_LENGTH    = 60.0

CONNECTOR_TOUCH_TOL         = 4.0
CONNECTOR_PARTNER_TOL       = 3.0
CONNECTOR_MAX_LENGTH        = 150.0
TRUE_CONTINUATION_MAX_SHIFT = 0.3
LAP_SHIFT_MIN                = 0.5
LAP_SHIFT_MAX                = 3.0

CURTAIL_NEAR_TOL       = 8.0
CURTAIL_MIN_LENGTH     = 6.0
CURTAIL_OUTLINE_MARGIN = 2.0

LAP_Y_TOL           = 0.5
LAP_SEARCH_MAX_X    = 150.0
LAP_GAP_CHECK_Y_TOL = 3.0

LAP_DIM_X_FIT_MARGIN = 15.0
LAP_DIM_Y_MARGIN     = 15.0

# Dimension-line-artifact exclusion (see module docstring). Deliberately
# tight: only fires when a numeric word is centred almost exactly under
# the candidate's own midpoint, which a genuine bar's own (off-centre,
# column-relative) curtailment dimension never is.
DIM_ARTIFACT_CENTRE_TOL = 10.0
DIM_ARTIFACT_Y_MARGIN   = 25.0

# De-duplication of near-identical raw fragments — the source PDF
# occasionally draws the exact same physical line twice (confirmed:
# MBM 03 y=113.62, two fragments sharing x_right=1559.0, left edges
# only 11.3pt apart, y identical to 3 decimals — a duplicate stroke,
# not two bars). Left unhandled, the duplicate becomes its own spurious
# PhysicalBar and falsely "laps" against the real one next to it.
DEDUP_Y_TOL            = 0.1   # duplicates share y to float precision; distinct rows are never this close
DEDUP_OVERLAP_FRACTION = 0.8   # overlap must cover most of the shorter fragment to count as a duplicate, not a genuine partial physical overlap (e.g. a curtailed bar ending inside a longer one)


# ── Public result types ───────────────────────────────────────────────────────

@dataclass
class BarEnd:
    """Classification of one true (post-merge) end of a PhysicalBar."""
    kind:            str
    point:           Point
    lap_partner_id:  Optional[str] = None
    lap_length:      Optional[float] = None
    lap_source:      Optional[str] = None
    detail:          Optional[str] = None

    @property
    def is_hook(self) -> bool:
        return self.kind == "hook"

    @property
    def is_lap(self) -> bool:
        return self.kind == "lap"


@dataclass
class PhysicalBar:
    """
    One complete, physically continuous run of reinforcement, assembled
    from one or more raw LineSegment fragments joined by confirmed
    zero-shift connectors. This is the unit every downstream module
    (bar_matcher, length calculation, shape resolution) should consume.
    """
    id:         str
    beam_id:    str
    y:          float
    segments:   list[LineSegment]
    left_end:   BarEnd
    right_end:  BarEnd

    @property
    def x_left(self) -> float:
        return min(s.x_left for s in self.segments)

    @property
    def x_right(self) -> float:
        return max(s.x_right for s in self.segments)

    @property
    def length(self) -> float:
        return self.x_right - self.x_left

    @property
    def hook_count(self) -> int:
        return sum(1 for e in (self.left_end, self.right_end) if e.is_hook)

    @property
    def is_lapped(self) -> bool:
        return self.left_end.is_lap or self.right_end.is_lap

    def summary(self) -> str:
        return (
            f"PhysicalBar({self.id!r}, y={self.y:.2f}, x=[{self.x_left:.1f},{self.x_right:.1f}], "
            f"segs={len(self.segments)}, left={self.left_end.kind!r}, right={self.right_end.kind!r})"
        )


@dataclass
class BeamBarGeometry:
    """
    Detected bar geometry for one beam zone (one entry from the beam
    box list — note a single beam_id may appear more than once if it
    wraps across drawing rows).
    """
    beam_id:          str
    page:             int
    box:              dict
    outline_y_top:    Optional[float] = None
    outline_y_bottom: Optional[float] = None
    outline_source:   str = "none"
    physical_bars:    list[PhysicalBar] = field(default_factory=list)
    bar_lines:        list[LineSegment] = field(default_factory=list)
    excluded_lines:   list[LineSegment] = field(default_factory=list)

    @property
    def has_outline(self) -> bool:
        return self.outline_y_top is not None and self.outline_y_bottom is not None

    def summary(self) -> str:
        return (
            f"BeamBarGeometry({self.beam_id!r}, physical_bars={len(self.physical_bars)}, "
            f"raw_fragments={len(self.bar_lines)}, excluded={len(self.excluded_lines)}, "
            f"outline=[{self.outline_y_top}, {self.outline_y_bottom}], source={self.outline_source!r})"
        )


# ── Public API ────────────────────────────────────────────────────────────────

def detect_bars(
    primitives:        PagePrimitives,
    beam_boxes:        list[dict],
    dimension_matches: Optional[list[dict]] = None,
    words:             Optional[list[dict]] = None,
) -> list[BeamBarGeometry]:
    """
    Identify reinforcement bar lines within each beam zone and assemble
    them into complete PhysicalBar objects.

    Args:
        primitives:        Page-wide vector geometry.
        beam_boxes:        Beam zones from outline_detector.detect_beam_boxes().
        dimension_matches: Optional — page-wide dimension values from
                            dimension_extractor.extract_dimension_matches(),
                            used both for lap-length lookup and (with
                            `words`) dimension-line-artifact exclusion.
        words:             Optional — pdfplumber word dicts for the page.
                            Used only for the narrow dimension-line-
                            artifact exclusion (see module docstring);
                            when omitted, that exclusion is skipped and
                            such artifacts fall through as ordinary
                            candidates like any other release of this
                            module before it.
    """
    dimension_matches = dimension_matches or []
    numeric_words = [
        w for w in (words or [])
        if re.match(r'^\d{3,6}(\.\d+)?$', w['text'])
    ]
    results: list[BeamBarGeometry] = []

    all_tick_marks = _find_diagonal_ticks(primitives.lines)
    all_triangles  = _find_small_triangles(primitives.polylines)

    for box_idx, box in enumerate(beam_boxes):
        geometry = BeamBarGeometry(
            beam_id=box['id'],
            page=box.get('page', 1),
            box=box,
            outline_y_top=box.get('outline_y_top'),
            outline_y_bottom=box.get('outline_y_bottom'),
            outline_source=box.get('outline_source', 'none'),
        )

        if not geometry.has_outline:
            fallback = _colour_fallback_outline(primitives, box)
            if fallback is None:
                results.append(geometry)
                continue
            geometry.outline_y_top, geometry.outline_y_bottom = fallback
            geometry.outline_source = "colour_fallback"

        y_top, y_bot = geometry.outline_y_top, geometry.outline_y_bottom

        leader_shafts = _find_leader_shafts(primitives.polylines, box, y_top, y_bot)
        valid_triangles = [
            t for t in all_triangles
            if not _belongs_to_leader_shaft(t, leader_shafts)
        ]

        black_candidates = [
            l for l in primitives.lines
            if l.is_horizontal and _is_black(l.stroke)
            and box['x_left'] <= l.mid_x <= box['x_right']
            and l.length >= MIN_BAR_LINE_LENGTH
            and (y_top - OUTLINE_Y_TOLERANCE) <= l.mid_y <= (y_bot + OUTLINE_Y_TOLERANCE)
        ]

        for line in black_candidates:
            if _is_annotation_terminated(line, all_tick_marks, valid_triangles):
                geometry.excluded_lines.append(line)
            elif numeric_words and _is_dimension_line_artifact(line, numeric_words, primitives):
                geometry.excluded_lines.append(line)
            else:
                geometry.bar_lines.append(line)

        geometry.bar_lines.sort(key=lambda l: l.mid_y)
        geometry.bar_lines = _dedupe_fragments(geometry.bar_lines)

        geometry.physical_bars = _assemble_physical_bars(
            geometry.beam_id, box_idx, geometry.bar_lines, primitives,
            y_top, y_bot, dimension_matches,
        )

        results.append(geometry)

    return results


def _is_dimension_line_artifact(
    line:           LineSegment,
    numeric_words:  list[dict],
    primitives:     PagePrimitives,
) -> bool:
    """
    True if `line` is very likely a dimension line's own drawn segment,
    not reinforcement — confirmed real case: MBM 04's "1690"/"1490"
    values, which use vertical-witness termination this module's
    tick/triangle exclusion doesn't recognise (see module docstring).

    Deliberately narrow to avoid the false-positive risk a broader
    geometry-only version had (a genuine curtailed-both-ends bar shares
    the same coarse "no bezier, vertical at each end" signature): only
    excludes when a numeric word is centred almost exactly under this
    candidate's own midpoint. A bar's own curtailment dimension
    measures distance *from a column* and is never centred under the
    bar's own span the way a dimension line's label is centred under
    its own line.
    """
    if _check_hook(line.start, primitives) is not None:
        return False
    if _check_hook(line.end, primitives) is not None:
        return False

    centre_x = line.mid_x
    for w in numeric_words:
        word_centre = (float(w['x0']) + float(w['x1'])) / 2 if 'x1' in w else float(w['x0'])
        if abs(word_centre - centre_x) <= DIM_ARTIFACT_CENTRE_TOL and abs(float(w['top']) - line.mid_y) <= DIM_ARTIFACT_Y_MARGIN:
            return True
    return False


def _dedupe_fragments(bar_lines: list[LineSegment]) -> list[LineSegment]:
    """
    Drop fragments that are near-duplicates of another fragment at
    (essentially) the same y — the same physical line drawn twice in
    the source PDF (see DEDUP_* constants). Keeps the longer of each
    duplicate pair. Genuine partial overlaps (e.g. a curtailed bar
    ending partway inside a longer, separate bar) have far lower
    overlap fraction than true duplicates and are left alone.
    """
    n = len(bar_lines)
    dropped: set[int] = set()
    for i in range(n):
        if i in dropped:
            continue
        a = bar_lines[i]
        for j in range(i + 1, n):
            if j in dropped:
                continue
            b = bar_lines[j]
            if abs(a.mid_y - b.mid_y) > DEDUP_Y_TOL:
                continue
            overlap = min(a.x_right, b.x_right) - max(a.x_left, b.x_left)
            if overlap <= 0:
                continue
            shorter = min(a.length, b.length)
            if overlap / shorter >= DEDUP_OVERLAP_FRACTION:
                dropped.add(j if a.length >= b.length else i)
    return [l for idx, l in enumerate(bar_lines) if idx not in dropped]


# ── Assembly: fragments -> PhysicalBar ────────────────────────────────────────

def _assemble_physical_bars(
    beam_id:            str,
    box_idx:            int,
    bar_lines:           list[LineSegment],
    primitives:           PagePrimitives,
    outline_y_top:        Optional[float],
    outline_y_bottom:     Optional[float],
    dimension_matches:    list[dict],
) -> list[PhysicalBar]:
    if not bar_lines:
        return []

    n = len(bar_lines)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    lap_links: dict[tuple[int, str], tuple[BarEnd, int]] = {}

    frag_endpoints = [(l.x_left, l.mid_y, i, 'L') for i, l in enumerate(bar_lines)] + \
                     [(l.x_right, l.mid_y, i, 'R') for i, l in enumerate(bar_lines)]

    def nearest_owner(pt: Point) -> Optional[tuple[float, int, str]]:
        best, best_dist = None, CONNECTOR_TOUCH_TOL
        for fx, fy, fi, fside in frag_endpoints:
            d = pt.distance_to(Point(fx, fy))
            if d < best_dist:
                best, best_dist = (fy, fi, fside), d
        return best

    for cl in primitives.lines:
        if cl.length > CONNECTOR_MAX_LENGTH:
            continue
        owner_a = nearest_owner(cl.start)
        owner_b = nearest_owner(cl.end)
        if owner_a is None or owner_b is None:
            continue
        ay, ai, aside = owner_a
        by, bi, bside = owner_b
        if ai == bi:
            continue

        shift = abs(ay - by)
        point_a = Point(bar_lines[ai].x_left if aside == 'L' else bar_lines[ai].x_right, ay)
        point_b = Point(bar_lines[bi].x_left if bside == 'L' else bar_lines[bi].x_right, by)

        if shift <= TRUE_CONTINUATION_MAX_SHIFT:
            union(ai, bi)
        elif LAP_SHIFT_MIN <= shift <= LAP_SHIFT_MAX:
            dim = _find_lap_dimension(
                min(point_a.x, point_b.x) - 1.0, max(point_a.x, point_b.x) + 1.0,
                (ay + by) / 2, dimension_matches,
            )
            detail = f"connector shift={shift:.2f}pt" + (f", dimension {dim:.1f}mm found" if dim is not None else ", no drawn dimension")
            lap_links[(ai, aside)] = (BarEnd(kind="lap", point=point_a, lap_length=dim,
                                              lap_source="drawn" if dim is not None else None, detail=detail), bi)
            lap_links[(bi, bside)] = (BarEnd(kind="lap", point=point_b, lap_length=dim,
                                              lap_source="drawn" if dim is not None else None, detail=detail), ai)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    physical_bars: list[PhysicalBar] = []

    for group_idx, (_, idxs) in enumerate(sorted(groups.items(), key=lambda kv: min(bar_lines[i].x_left for i in kv[1]))):
        segs = sorted((bar_lines[i] for i in idxs), key=lambda s: s.x_left)
        rep_y = max(segs, key=lambda s: s.length).mid_y
        # box_idx disambiguates a beam_id that appears in more than one
        # BeamBarGeometry zone (confirmed real case: the drawing's own
        # "MBM 05" duplicate-label quirk produces two separate box
        # entries sharing beam_id="MBM 05" — without box_idx, their
        # physical bars would collide on id "MBM 05#0", "MBM 05#1", ...
        # and any page-wide {id: bar} lookup downstream would silently
        # drop one of them.
        pid = f"{beam_id}@{box_idx}#{group_idx}"

        physical_bars.append(PhysicalBar(
            id=pid, beam_id=beam_id, y=rep_y, segments=segs,
            left_end=None, right_end=None,
        ))

    frag_to_bar_idx = {}
    for bidx, pb in enumerate(physical_bars):
        for i, l in enumerate(bar_lines):
            if l in pb.segments:
                frag_to_bar_idx[i] = bidx

    for pb in physical_bars:
        leftmost_i  = min((i for i, l in enumerate(bar_lines) if l in pb.segments), key=lambda i: bar_lines[i].x_left)
        rightmost_i = max((i for i, l in enumerate(bar_lines) if l in pb.segments), key=lambda i: bar_lines[i].x_right)

        pb.left_end  = _resolve_end(leftmost_i, 'L', bar_lines, lap_links, primitives, outline_y_top, outline_y_bottom,
                                     dimension_matches, frag_to_bar_idx, physical_bars, -1)
        pb.right_end = _resolve_end(rightmost_i, 'R', bar_lines, lap_links, primitives, outline_y_top, outline_y_bottom,
                                     dimension_matches, frag_to_bar_idx, physical_bars, +1)

    _enforce_reciprocal_laps(physical_bars, bar_lines, lap_links, primitives, outline_y_top, outline_y_bottom,
                              dimension_matches, frag_to_bar_idx)

    return physical_bars


def _enforce_reciprocal_laps(
    physical_bars:      list[PhysicalBar],
    bar_lines:           list[LineSegment],
    lap_links:            dict[tuple[int, str], tuple[BarEnd, int]],
    primitives:           PagePrimitives,
    outline_y_top:        Optional[float],
    outline_y_bottom:     Optional[float],
    dimension_matches:    list[dict],
    frag_to_bar_idx:      dict[int, int],
) -> None:
    """
    A lap is a relationship between two ends — it should only stand if
    BOTH sides agree. Two ways this can go wrong, both observed on the
    real drawing:
      - A connector-shift lap link and an independent hook signature
        can land within a few pt of each other (MBM 04#7/#15): the
        hook side resolves to "hook" (checked first, and correctly so
        — the bezier+leg signature is real) and never even looks at
        the lap_links entry naming it as a partner, leaving the other
        side's lap claim one-directional and almost certainly spurious
        (the connector search likely matched the hook's own geometry,
        not a genuine lap junction).
      - A gap-style lap match is one-directional by construction (only
        the searching end's lap_partner_id gets set) — if the partner
        bar has its own curtailment marker on that same end (a real,
        independently-confirmed "this bar is deliberately cut off
        here" signal), the searching end's "lap" claim is more likely
        a false positive than a real, unmarked lap partner.

    Any lap end without a reciprocal lap on the partner's corresponding
    end gets re-resolved with lap classification (both connector-shift
    and gap-style) excluded, falling through to curtailment or plain
    straight instead — never left as an unconfirmed, one-sided "lap".
    """
    bar_by_id = {pb.id: pb for pb in physical_bars}

    for pb in physical_bars:
        for side, end in (('L', pb.left_end), ('R', pb.right_end)):
            if not end.is_lap or end.lap_partner_id is None:
                continue
            partner = bar_by_id.get(end.lap_partner_id)
            reciprocal = partner is not None and (
                partner.left_end.lap_partner_id == pb.id or partner.right_end.lap_partner_id == pb.id
            )
            if reciprocal:
                continue

            frag_idx = next(i for i, l in enumerate(bar_lines) if l in pb.segments and (
                (side == 'L' and l.x_left == end.point.x) or (side == 'R' and l.x_right == end.point.x)
            ))
            direction = -1 if side == 'L' else 1
            replacement = _resolve_end(
                frag_idx, side, bar_lines, {}, primitives, outline_y_top, outline_y_bottom,
                dimension_matches, frag_to_bar_idx, physical_bars, direction,
                skip_gap_lap=True,
            )
            if side == 'L':
                pb.left_end = replacement
            else:
                pb.right_end = replacement


def _resolve_end(
    frag_idx:            int,
    side:                str,
    bar_lines:            list[LineSegment],
    lap_links:            dict[tuple[int, str], tuple[BarEnd, int]],
    primitives:           PagePrimitives,
    outline_y_top:        Optional[float],
    outline_y_bottom:     Optional[float],
    dimension_matches:    list[dict],
    frag_to_bar_idx:      dict[int, int],
    physical_bars:        list[PhysicalBar],
    direction:            int,
    skip_gap_lap:         bool = False,
) -> BarEnd:
    line = bar_lines[frag_idx]
    point = Point(line.x_left if side == 'L' else line.x_right, line.mid_y)

    hook = _check_hook(point, primitives)
    if hook is not None:
        return hook

    if (frag_idx, side) in lap_links:
        end, partner_frag_idx = lap_links[(frag_idx, side)]
        partner_bar_idx = frag_to_bar_idx.get(partner_frag_idx)
        if partner_bar_idx is not None:
            end.lap_partner_id = physical_bars[partner_bar_idx].id
        return end

    curtailment = _check_curtailment(point, outline_y_top, outline_y_bottom, primitives)
    if curtailment is not None:
        return curtailment

    if skip_gap_lap:
        return BarEnd(kind="straight", point=point, detail="lap claim not reciprocated by partner — reclassified as plain end")

    gap_lap, partner_frag_idx = _check_gap_lap(point, direction, line, bar_lines, primitives, dimension_matches)
    if gap_lap is not None:
        if partner_frag_idx is not None:
            partner_bar_idx = frag_to_bar_idx.get(partner_frag_idx)
            if partner_bar_idx is not None:
                gap_lap.lap_partner_id = physical_bars[partner_bar_idx].id
        return gap_lap

    return BarEnd(kind="straight", point=point, detail="no hook/lap/curtailment marker found — plain end")


# ── Hook detection ──────────────────────────────────────────────────────────

def _check_hook(point: Point, primitives: PagePrimitives) -> Optional[BarEnd]:
    bez = _find_bezier_near(point, primitives.beziers, HOOK_BEZIER_SEARCH_TOL, HOOK_BEZIER_MAX_CHORD)
    if bez is None:
        return None
    far_end = bez.p3 if bez.p0.distance_to(point) < bez.p3.distance_to(point) else bez.p0
    leg = _find_vertical_leg(far_end, primitives.lines, HOOK_LEG_SEARCH_TOL, HOOK_LEG_MIN_LENGTH, HOOK_LEG_MAX_LENGTH)
    if leg is None:
        return None
    return BarEnd(kind="hook", point=point,
                  detail=f"bezier chord={bez.p0.distance_to(bez.p3):.1f}pt, leg len={leg.length:.1f}pt")


def _find_bezier_near(point: Point, beziers: list[CubicBezier], tol: float, max_chord: float) -> Optional[CubicBezier]:
    best, best_dist = None, tol
    for b in beziers:
        if b.p0.distance_to(b.p3) > max_chord:
            continue
        for ep in (b.p0, b.p3):
            d = ep.distance_to(point)
            if d < best_dist:
                best, best_dist = b, d
    return best


def _find_vertical_leg(point: Point, lines: list[LineSegment], tol: float, min_len: float, max_len: float) -> Optional[LineSegment]:
    best, best_dist = None, tol
    for l in lines:
        if not l.is_vertical or not (min_len <= l.length <= max_len):
            continue
        for ep in (l.start, l.end):
            d = ep.distance_to(point)
            if d < best_dist:
                best, best_dist = l, d
    return best


# ── Curtailment detection ─────────────────────────────────────────────────────

def _check_curtailment(
    point:              Point,
    outline_y_top:      Optional[float],
    outline_y_bottom:   Optional[float],
    primitives:         PagePrimitives,
) -> Optional[BarEnd]:
    if outline_y_top is None or outline_y_bottom is None:
        return None
    for l in primitives.lines:
        if not l.is_vertical or l.length < CURTAIL_MIN_LENGTH:
            continue
        for near_ep, far_ep in ((l.start, l.end), (l.end, l.start)):
            if near_ep.distance_to(point) > CURTAIL_NEAR_TOL:
                continue
            outside_top = far_ep.y < outline_y_top - CURTAIL_OUTLINE_MARGIN
            outside_bot = far_ep.y > outline_y_bottom + CURTAIL_OUTLINE_MARGIN
            if outside_top or outside_bot:
                return BarEnd(kind="straight", point=point,
                              detail=f"curtailment marker len={l.length:.1f}pt extends {'above' if outside_top else 'below'} outline")
    return None


# ── Gap-style lap detection (rarer; used only after connector search fails) ──

def _check_gap_lap(
    point:              Point,
    direction:          int,
    line:               LineSegment,
    bar_lines:          list[LineSegment],
    primitives:         PagePrimitives,
    dimension_matches:  list[dict],
) -> tuple[Optional[BarEnd], Optional[int]]:
    best, best_dist, best_idx = None, LAP_SEARCH_MAX_X, None
    for idx, l in enumerate(bar_lines):
        if l is line or abs(l.mid_y - line.mid_y) > LAP_Y_TOL:
            continue
        for ep in (l.start, l.end):
            delta = (ep.x - point.x) * direction
            if 0 < delta < best_dist:
                best, best_dist, best_idx = ep, delta, idx
    if best is None:
        return None, None

    gap_left, gap_right = min(point.x, best.x), max(point.x, best.x)
    if _gap_has_geometry(gap_left, gap_right, line.mid_y, primitives):
        return None, None

    dim = _find_lap_dimension(gap_left, gap_right, line.mid_y, dimension_matches)
    end = BarEnd(kind="lap", point=point, lap_length=dim, lap_source="drawn" if dim is not None else None,
                 detail=f"empty gap=[{gap_left:.1f},{gap_right:.1f}]" + (f", dimension {dim:.1f}mm found" if dim is not None else ", no drawn dimension"))
    return end, best_idx


def _gap_has_geometry(x_left: float, x_right: float, y: float, primitives: PagePrimitives) -> bool:
    inner_left, inner_right = x_left + 1.0, x_right - 1.0
    if inner_right <= inner_left:
        return False
    for l in primitives.lines:
        if inner_left <= l.mid_x <= inner_right and abs(l.mid_y - y) <= LAP_GAP_CHECK_Y_TOL:
            return True
    for b in primitives.beziers:
        mx, my = (b.p0.x + b.p3.x) / 2, (b.p0.y + b.p3.y) / 2
        if inner_left <= mx <= inner_right and abs(my - y) <= LAP_GAP_CHECK_Y_TOL:
            return True
    return False


def _find_lap_dimension(x_left: float, x_right: float, y: float, dimension_matches: list[dict]) -> Optional[float]:
    candidates = [
        m for m in dimension_matches
        if m['x_left'] >= x_left - LAP_DIM_X_FIT_MARGIN
        and m['x_right'] <= x_right + LAP_DIM_X_FIT_MARGIN
        and abs(m['y'] - y) <= LAP_DIM_Y_MARGIN
    ]
    if not candidates:
        return None
    gap_width = x_right - x_left
    return min(candidates, key=lambda m: abs((m['x_right'] - m['x_left']) - gap_width))['value']


# ── Internal: colour check ─────────────────────────────────────────────────────

def _is_black(colour: Optional[Colour]) -> bool:
    return colour is not None and colour.is_black()


# ── Internal: annotation-marker exclusion ─────────────────────────────────────

def _find_diagonal_ticks(lines: list[LineSegment]) -> list[LineSegment]:
    return [
        l for l in lines
        if not l.is_horizontal and not l.is_vertical
        and l.length <= ANNOTATION_MARKER_MAX_SIZE
    ]


def _find_small_triangles(polylines: list[Polyline]) -> list[Polyline]:
    return [
        p for p in polylines
        if p.width <= ANNOTATION_MARKER_MAX_SIZE and p.height <= ANNOTATION_MARKER_MAX_SIZE
    ]


def _find_leader_shafts(
    polylines: list[Polyline],
    box:       dict,
    y_top:     float,
    y_bot:     float,
    margin:    float = LEADER_SHAFT_Y_MARGIN,
) -> list[Polyline]:
    """
    margin defaults to the module constant, so every existing call site
    (this module's own detect_bars, and bar_matcher's primary matching
    pass) is 100% unchanged. bar_matcher additionally uses this with an
    explicit wider margin, but ONLY as a last-resort retry scoped to
    callouts its primary pass already failed to match — never as a
    global default — after a wider default here was found to change
    which shafts qualify page-wide and create new ambiguity between
    competing candidates near unrelated labels (regression confirmed:
    6 unmatched became 54 with a global bump from 3.0 to 4.0).
    """
    result = []
    for p in polylines:
        if p.closed or p.segment_count < 2:
            continue
        bb = p.bounding_box
        width, height = bb[2] - bb[0], bb[3] - bb[1]
        if width > LEADER_SHAFT_MAX_SIZE or height > LEADER_SHAFT_MAX_SIZE:
            continue
        cx = (bb[0] + bb[2]) / 2
        if not (box['x_left'] <= cx <= box['x_right']):
            continue
        touches_band  = bb[1] <= y_bot + margin and bb[3] >= y_top - margin
        extends_past  = bb[1] < y_top - margin or bb[3] > y_bot + margin
        if touches_band and extends_past:
            result.append(p)
    return result



def _belongs_to_leader_shaft(triangle: Polyline, shafts: list[Polyline]) -> bool:
    tbb = triangle.bounding_box
    tcx, tcy = (tbb[0] + tbb[2]) / 2, (tbb[1] + tbb[3]) / 2
    for shaft in shafts:
        sbb = shaft.bounding_box
        pad = ANNOTATION_MARKER_SEARCH_TOL
        if (sbb[0] - pad) <= tcx <= (sbb[2] + pad) and (sbb[1] - pad) <= tcy <= (sbb[3] + pad):
            return True
    return False


def _is_annotation_terminated(
    line:      LineSegment,
    ticks:     list[LineSegment],
    triangles: list[Polyline],
) -> bool:
    endpoints = (line.start, line.end)

    for tick in ticks:
        tick_mid_y = (tick.start.y + tick.end.y) / 2
        if abs(tick_mid_y - line.mid_y) > ANNOTATION_MARKER_Y_TOLERANCE:
            continue
        for ep in endpoints:
            if _near(ep, tick.start, ANNOTATION_MARKER_SEARCH_TOL) or _near(ep, tick.end, ANNOTATION_MARKER_SEARCH_TOL):
                return True

    for tri in triangles:
        bb = tri.bounding_box
        width  = bb[2] - bb[0]
        height = bb[3] - bb[1]
        if width < height:
            continue
        tri_centre_y = (bb[1] + bb[3]) / 2
        if abs(tri_centre_y - line.mid_y) > ANNOTATION_MARKER_Y_TOLERANCE:
            continue
        tri_centre = Point((bb[0] + bb[2]) / 2, tri_centre_y)
        for ep in endpoints:
            if _near(ep, tri_centre, ANNOTATION_MARKER_SEARCH_TOL):
                return True

    return False


def _near(a: Point, b: Point, tol: float) -> bool:
    return a.distance_to(b) <= tol


# ── Last-resort colour fallback ────────────────────────────────────────────────

def _colour_fallback_outline(
    primitives: PagePrimitives,
    box:        dict,
) -> Optional[tuple[float, float]]:
    zone_h_lines = [
        l for l in primitives.lines
        if l.is_horizontal
        and box['x_left'] <= l.mid_x <= box['x_right']
        and box['y_top']  <= l.mid_y <= box['y_bot']
    ]
    zone_polys = [
        p for p in primitives.polylines
        if box['x_left'] <= (p.bounding_box[0] + p.bounding_box[2]) / 2 <= box['x_right']
        and box['y_top'] <= (p.bounding_box[1] + p.bounding_box[3]) / 2 <= box['y_bot']
    ]

    outline_ys = _find_outline_colour_ys(zone_h_lines, zone_polys)
    if len(outline_ys) >= 2:
        return outline_ys[0], outline_ys[-1]

    section_boxes = _find_section_boxes_colour(primitives.polylines)

    if len(outline_ys) == 1:
        known_y = outline_ys[0]
        colour_lines = [l for l in zone_h_lines if _is_outline_colour(l.stroke)]
        anchor_x = (
            sum(l.mid_x for l in colour_lines) / len(colour_lines)
            if colour_lines else (box['x_left'] + box['x_right']) / 2
        )
        box_match = _nearest_bbox(section_boxes, anchor_x, known_y)
        if box_match is None:
            return None
        depth = box_match[3] - box_match[1]
        below = [l for l in primitives.lines if l.is_horizontal and _is_black(l.stroke)
                 and l.length >= MIN_BAR_LINE_LENGTH
                 and box['x_left'] <= l.mid_x <= box['x_right']
                 and box['y_top'] - ORIENTATION_SEARCH_PAD <= l.mid_y <= box['y_bot'] + ORIENTATION_SEARCH_PAD
                 and known_y < l.mid_y <= known_y + depth]
        above = [l for l in primitives.lines if l.is_horizontal and _is_black(l.stroke)
                 and l.length >= MIN_BAR_LINE_LENGTH
                 and box['x_left'] <= l.mid_x <= box['x_right']
                 and box['y_top'] - ORIENTATION_SEARCH_PAD <= l.mid_y <= box['y_bot'] + ORIENTATION_SEARCH_PAD
                 and known_y - depth <= l.mid_y < known_y]
        return (known_y, known_y + depth) if len(below) >= len(above) else (known_y - depth, known_y)

    verticals_range = _find_outline_from_verticals_colour(primitives.lines, box)
    if verticals_range is not None:
        return verticals_range

    anchor_x = (box['x_left'] + box['x_right']) / 2
    anchor_y = (box['y_top'] + box['y_bot']) / 2
    box_match = _nearest_bbox(section_boxes, anchor_x, anchor_y)
    if box_match is None:
        return None
    return box_match[1], box_match[3]


def _find_outline_colour_ys(
    zone_h_lines: list[LineSegment],
    zone_polys:   list[Polyline],
) -> list[float]:
    outline_ys: list[float] = []
    for l in zone_h_lines:
        if _is_outline_colour(l.stroke) and l.length >= MIN_OUTLINE_SEGMENT_LENGTH:
            outline_ys.append(l.mid_y)
    for p in zone_polys:
        if not _is_outline_colour(p.stroke):
            continue
        pts = p.points + ([p.points[0]] if p.closed else [])
        for a, b in zip(pts, pts[1:]):
            if abs(b.y - a.y) < 2.0 and abs(b.x - a.x) >= MIN_OUTLINE_SEGMENT_LENGTH:
                outline_ys.append((a.y + b.y) / 2)
    if not outline_ys:
        return []
    deduped: list[float] = []
    for y in sorted(outline_ys):
        if not deduped or abs(y - deduped[-1]) > 1.0:
            deduped.append(y)
    return deduped


def _find_outline_from_verticals_colour(
    lines: list[LineSegment],
    box:   dict,
) -> Optional[tuple[float, float]]:
    candidates = [
        l for l in lines
        if l.is_vertical and _is_outline_colour(l.stroke)
        and box['x_left'] <= l.mid_x <= box['x_right']
        and (box['y_top'] - VERTICALS_Y_SEARCH_PAD) <= l.mid_y <= (box['y_bot'] + VERTICALS_Y_SEARCH_PAD)
        and SECTION_BOX_MIN_HEIGHT <= l.length <= SECTION_BOX_MAX_HEIGHT
    ]
    if len(candidates) < 2:
        return None
    height_counts: dict[float, list[LineSegment]] = {}
    for l in candidates:
        height_counts.setdefault(round(l.length, 1), []).append(l)
    _best_height, best_group = max(height_counts.items(), key=lambda kv: len(kv[1]))
    if len(best_group) < 2:
        return None
    return min(l.y_top for l in best_group), max(l.y_bot for l in best_group)


def _find_section_boxes_colour(polylines: list[Polyline]) -> list[tuple[float, float, float, float]]:
    result = []
    for p in polylines:
        if not _is_outline_colour(p.stroke):
            continue
        bb = p.bounding_box
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if SECTION_BOX_MIN_WIDTH <= w <= SECTION_BOX_MAX_WIDTH and SECTION_BOX_MIN_HEIGHT <= h <= SECTION_BOX_MAX_HEIGHT:
            result.append(bb)
    return result


def _nearest_bbox(
    boxes:    list[tuple[float, float, float, float]],
    anchor_x: float,
    anchor_y: float,
) -> Optional[tuple[float, float, float, float]]:
    if not boxes:
        return None
    return min(boxes, key=lambda bb: ((bb[0]+bb[2])/2 - anchor_x)**2 + ((bb[1]+bb[3])/2 - anchor_y)**2)


def _is_outline_colour(colour: Optional[Colour]) -> bool:
    if colour is None:
        return False
    r0, g0, b0 = OUTLINE_COLOUR_RGB
    return (
        abs(colour.r - r0) < COLOUR_MATCH_TOL
        and abs(colour.g - g0) < COLOUR_MATCH_TOL
        and abs(colour.b - b0) < COLOUR_MATCH_TOL
    )


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    sys.path.insert(0, ".")
    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes
    from parser.pdf.dimension_extractor import extract_dimension_matches
    import pdfplumber

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        page_w, page_h = page.width, page.height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)
    dim_matches = extract_dimension_matches(path)

    for geometry in detect_bars(prims, boxes, dim_matches, words):
        print(geometry.summary())
        for pb in geometry.physical_bars:
            print(f"    {pb.summary()}")
            print(f"      left : {pb.left_end}")
            print(f"      right: {pb.right_end}")