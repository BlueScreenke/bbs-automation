"""
parser/geometry/bar_matcher.py

Matches parsed bar callouts (text, from parser.pdf.parser) to detected
PhysicalBar objects (geometry, from parser.geometry.bar_detector) within
one beam.

Changed this session: matches to PhysicalBar, not raw LineSegment
------------------------------------------------------------------------
bar_detector.py now assembles raw line fragments into complete
PhysicalBar objects (full merged span, true hook/lap/straight
classification already resolved at both ends — see bar_detector.py's
own docstring for the empirical basis). This module's matching logic
itself is UNCHANGED and deliberately left alone: every tolerance below
(SHAFT_TEXT_MATCH_TOL, SHAFT_LINE_Y_TOL, LEARNED_Y_CLUSTER_TOL) was
tuned against real geometry and is delicate — a wrong "confident" match
is worse than an honest unmatched one, and these numbers are trusted.
The only change is what happens AFTER a raw fragment is found: it is
now looked up in geometry.physical_bars to find which complete bar it
belongs to, and MatchedBar carries that PhysicalBar (not just the one
fragment an arrow happened to point at) so every downstream module gets
the bar's full assembled geometry, not a possibly-partial fragment.

Why this is a separate step from bar_detector.py
--------------------------------------------------
bar_detector.py knows *where* bars are and how their own ends are
shaped, but not which numeric mark or position label each one belongs
to — that information only exists in the parsed callout text. This
module is the bridge: for each individual callout *occurrence* (there
can be more than one per numeric mark — see MULTI-OCCURRENCE MARKS
below), find the specific PhysicalBar it refers to.

Matching strategy, in order (unchanged from previous version)
-----------------------------------------------------------------
1. Leader-arrow trace (primary). Most callouts have a small leader-arrow
   (a short open polyline: shaft + arrowhead) running from the text down
   or up to its bar line's exact endpoint. Where found, this gives an
   exact, unambiguous match — reuses bar_detector._find_leader_shafts,
   which already identifies these (open polylines that touch the beam's
   depth band and extend beyond it toward the text).

2. Learned-y fallback. Position labels (T1, T2, B1, B2, ...) are physical
   layers at a fixed depth for the whole length of one beam — every
   occurrence of "T2" in a given beam sits at the same y (confirmed
   empirically: MBM 04 has five separate T2-labelled marks, all at
   y=267.52). So whatever the arrow pass confidently matches teaches us
   each label's true y *for this specific beam*, rather than assuming a
   generic layer order holds. Anything left unmatched after step 1 is
   resolved by looking up its own label's learned y, then picking the
   nearest fragment at that y by x-proximity.

3. Ordinal fallback. Only used if a position label was
   never confidently matched anywhere in this beam (so no learned y
   exists for it). Falls back to the standard drafting convention
   T1 < T2 < ... < B3 < B2 < B1 (top to bottom) against the full sorted
   fragment list.

4. Leader-arrow retry, wider margin (last resort). A small number of
   real leader-arrow shafts fall just short of bar_detector's own
   LEADER_SHAFT_Y_MARGIN (misses of 0.16-2.56pt observed on the real
   drawing — genuine shafts, not noise). Loosening that margin globally
   was tried and reverted (see RETRY_SHAFT_Y_MARGIN below) because it
   changes which shapes qualify as shafts page-wide and creates new
   ambiguity elsewhere. Scoping the same wider search to only the
   handful of callouts still unresolved after passes 1-3 gets the same
   recall without that risk — a callout already matched never reaches
   this pass, so it can't regress.

Multi-occurrence marks (laps and column-spans)
------------------------------------------------
The same numeric mark can legitimately appear more than once in one
beam. This module does NOT merge same-mark occurrences into one total
— each occurrence becomes its own MatchedBar, per the project decision
to keep them separate for now and catch inconsistencies rather than
silently sum them. is_duplicate_mark flags this for downstream steps.
Note this is now somewhat redundant with bar_detector's own lap-partner
linking on PhysicalBar — is_duplicate_mark is a callout-text-level
signal (same mark string appears twice), while PhysicalBar.is_lapped is
a geometry-level signal (this bar's own end is a confirmed lap); they
usually agree but are checked independently and can catch different
things.

Public surface
--------------
    match_bars_to_geometry(parsed_bars, geometry, primitives) -> list[MatchedBar]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from parser.geometry.bar_detector import BeamBarGeometry, PhysicalBar, _find_leader_shafts
from parser.geometry.primitives import LineSegment, PagePrimitives, Point
from parser.pdf.models import ParsedBarData


# ── Tuneable constants (UNCHANGED — delicate, validated against real geometry) ─

# How close a leader-shaft's text-side end must be to a callout's (x0, top)
# to count as "this shaft belongs to this callout". Generous because x0/top
# is the token's top-left corner, not necessarily the exact point the arrow
# springs from.
SHAFT_TEXT_MATCH_TOL = 40.0

# How close a leader-shaft's bar-side end must be to a candidate fragment's
# y to count as landing on it.
SHAFT_LINE_Y_TOL = 4.0

# When learning a position label's y from confident arrow matches, group
# matches within this y-tolerance as "the same layer" (small — layers are
# a fixed depth, this only absorbs rounding).
LEARNED_Y_CLUSTER_TOL = 2.0

# Last-resort retry margin for pass 3 (see module docstring "Pass 3").
# NOTE ON DIRECTION: bar_detector's extends_past check requires the
# shaft to clear the outline edge by MORE than the margin (bb[1] <
# y_top - margin) — so a SMALLER margin is the more permissive
# direction here, not larger (the opposite of touches_band, which
# widens permissively as margin grows). Measured against the real
# drawing: every currently-unmatched callout's real leader shaft clears
# the outline edge by 1.84-2.56pt (genuine shafts, not noise) — just
# under bar_detector's own default margin of 3.0. A margin between 0
# and 1.84 here would catch all of them via the same clause.
# A GLOBAL reduction in bar_detector.py was not attempted after the
# opposite (a global increase, tried and reverted) already showed how
# sensitive shaft qualification is page-wide — even the correct
# direction risks similar collateral changes elsewhere. Scoping this to
# only the callouts passes 1-2 already failed on avoids that risk
# entirely, since a callout matched earlier never reaches this pass.
RETRY_SHAFT_Y_MARGIN = 1.5

# Ordinal fallback ranks, used only when a label was never confidently
# matched anywhere in the beam. Lower rank = nearer the outline's top.
_ORDINAL_MAX_LAYER = 9


# ── Public result type ────────────────────────────────────────────────────────

@dataclass
class MatchedBar:
    """One callout occurrence, matched (or not) to its complete PhysicalBar."""
    beam_id:           str
    numeric_mark:      str
    position:          Optional[str]
    diameter:          Optional[int]
    quantity:          Optional[int]
    x0:                float
    top:               float
    physical_bar:      Optional[PhysicalBar]
    matched_fragment:  Optional[LineSegment]   # the specific raw fragment the arrow/learned-y landed on, kept for traceability
    match_method:      str   # "leader_arrow" | "learned_y" | "ordinal" | "leader_arrow_retry" | "unmatched"
    is_duplicate_mark: bool  # True if this numeric_mark has more than one occurrence in this beam
    source:            ParsedBarData

    def summary(self) -> str:
        bar_desc = (
            f"x=[{self.physical_bar.x_left:.1f},{self.physical_bar.x_right:.1f}] y={self.physical_bar.y:.1f} "
            f"({self.physical_bar.left_end.kind}/{self.physical_bar.right_end.kind})"
            if self.physical_bar else "NONE"
        )
        return (
            f"MatchedBar(mark={self.numeric_mark!r}, position={self.position!r}, "
            f"bar={bar_desc}, method={self.match_method!r}, dup={self.is_duplicate_mark})"
        )


# ── Public API ────────────────────────────────────────────────────────────────

def match_bars_to_geometry(
    parsed_bars: list[ParsedBarData],
    geometry:    BeamBarGeometry,
    primitives:  PagePrimitives,
) -> list[MatchedBar]:
    """
    Match every callout occurrence in `parsed_bars` (all belonging to the
    same beam as `geometry`) to a PhysicalBar in `geometry.physical_bars`.

    Args:
        parsed_bars: ParsedBarData occurrences for this beam (may contain
                     more than one entry per numeric_mark — see module
                     docstring).
        geometry:    This beam's BeamBarGeometry from bar_detector.detect_bars().
        primitives:  Full-page primitives (for leader-arrow shaft geometry).

    Returns:
        One MatchedBar per input ParsedBarData occurrence, same order.
    """
    if not geometry.has_outline:
        # No reliable outline for this zone — nothing safe to match
        # against. Return everything unmatched rather than guess.
        return [_unmatched(p, geometry.beam_id) for p in parsed_bars]

    frag_owner = _build_fragment_owner_map(geometry.physical_bars)

    mark_counts: dict[str, int] = {}
    for p in parsed_bars:
        if p.numeric_mark:
            mark_counts[p.numeric_mark] = mark_counts.get(p.numeric_mark, 0) + 1

    shafts = _find_leader_shafts(primitives.polylines, geometry.box, geometry.outline_y_top, geometry.outline_y_bottom)
    distinct_labels = sorted({p.position for p in parsed_bars if p.position})

    results_by_index: dict[int, MatchedBar] = {}
    unresolved: list[tuple[int, ParsedBarData]] = []

    # Pass 1: leader-arrow trace.
    for i, p in enumerate(parsed_bars):
        line = _match_via_leader_arrow(p, shafts, geometry.bar_lines, geometry.outline_y_top, geometry.outline_y_bottom)
        if line is not None:
            results_by_index[i] = _make_match(p, geometry.beam_id, line, frag_owner, "leader_arrow", mark_counts)
        else:
            unresolved.append((i, p))

    # Learn each position label's y from what pass 1 confidently found.
    learned_y = _learn_position_y(list(results_by_index.values()))

    # Pass 2: learned-y fallback, then ordinal fallback.
    still_unresolved: list[tuple[int, ParsedBarData]] = []
    for i, p in unresolved:
        line = _match_via_learned_y(p, learned_y, geometry.bar_lines)
        method = "learned_y"
        if line is None:
            line = _match_via_ordinal(p, geometry.bar_lines, distinct_labels)
            method = "ordinal"
        if line is not None:
            results_by_index[i] = _make_match(p, geometry.beam_id, line, frag_owner, method, mark_counts)
        else:
            still_unresolved.append((i, p))

    # Pass 3: leader-arrow retry with a wider shaft-margin, scoped ONLY
    # to callouts passes 1-2 already failed on (see RETRY_SHAFT_Y_MARGIN
    # — this cannot regress an already-successful match, since anything
    # matched in pass 1 or 2 never reaches here).
    if still_unresolved:
        loose_shafts = _find_leader_shafts(
            primitives.polylines, geometry.box, geometry.outline_y_top, geometry.outline_y_bottom,
            margin=RETRY_SHAFT_Y_MARGIN,
        )
        for i, p in still_unresolved:
            line = _match_via_leader_arrow(p, loose_shafts, geometry.bar_lines, geometry.outline_y_top, geometry.outline_y_bottom)
            if line is not None:
                results_by_index[i] = _make_match(p, geometry.beam_id, line, frag_owner, "leader_arrow_retry", mark_counts)
            else:
                results_by_index[i] = _make_match(p, geometry.beam_id, None, frag_owner, "unmatched", mark_counts)

    # Rebuild in original input order — the documented contract this
    # function must honour, since callers (e.g. parser.py's
    # _attach_geometry_classification) zip the result back against
    # parsed_bars positionally. A prior version of this function built
    # `results` by appending each pass's successes in turn instead of by
    # index, which silently broke that contract: any single pass-1
    # failure pushed every bar after it out of position when zipped
    # against the original list. Confirmed as a real bug on
    # Page_1_beams.pdf (MBM 04): mark 25 failing pass 1 caused mark 22
    # and mark 24's classifications to be read from the wrong MatchedBar
    # entirely, degrading a working match into a false "unmatched".
    return [results_by_index[i] for i in range(len(parsed_bars))]


# ── Fragment -> PhysicalBar lookup ────────────────────────────────────────────

def _build_fragment_owner_map(physical_bars: list[PhysicalBar]) -> dict[int, PhysicalBar]:
    """
    Maps id(fragment) -> owning PhysicalBar. Keyed by object identity
    (id()), not value equality — LineSegment is a plain @dataclass, so
    two distinct fragments that happen to share identical coordinates
    would compare equal under value equality and could resolve to the
    wrong owner. Object identity is unambiguous since every fragment in
    physical_bars is a segment from the exact same bar_lines list
    bar_detector built them from.
    """
    owner: dict[int, PhysicalBar] = {}
    for pb in physical_bars:
        for frag in pb.segments:
            owner[id(frag)] = pb
    return owner


# ── Pass 1: leader-arrow trace ────────────────────────────────────────────────

def _match_via_leader_arrow(
    parsed:     ParsedBarData,
    shafts:     list,
    bar_lines:  list[LineSegment],
    y_top:      float,
    y_bot:      float,
) -> Optional[LineSegment]:
    if parsed.x0 is None or parsed.top is None:
        return None

    text_point = Point(parsed.x0, parsed.top)
    best_shaft, best_dist = None, SHAFT_TEXT_MATCH_TOL
    for shaft in shafts:
        text_end, _bar_end = _shaft_endpoints(shaft, y_top, y_bot)
        if text_end is None:
            continue
        d = text_point.distance_to(text_end)
        if d < best_dist:
            best_shaft, best_dist = shaft, d

    if best_shaft is None:
        return None

    _text_end, bar_end = _shaft_endpoints(best_shaft, y_top, y_bot)
    if bar_end is None:
        return None

    return _nearest_line_to_point(bar_end, bar_lines, y_tol=SHAFT_LINE_Y_TOL)


def _shaft_endpoints(shaft, y_top: float, y_bot: float) -> tuple[Optional[Point], Optional[Point]]:
    """
    A leader shaft's two ends are points[0] and points[-1], in whichever
    order PyMuPDF happened to record the path — not reliably "text end
    first". Determine which is which using the beam's own outline
    y-range: the text end lies outside [y_top, y_bot] (it reaches toward
    a label above/below the beam depth); the bar end lies at or within
    it. If neither or both lie outside (ambiguous shaft), return None.
    """
    pts = shaft.points
    if len(pts) < 2:
        return None, None
    first, last = pts[0], pts[-1]
    first_outside = first.y < y_top or first.y > y_bot
    last_outside  = last.y < y_top or last.y > y_bot
    if first_outside and not last_outside:
        return first, last
    if last_outside and not first_outside:
        return last, first
    return None, None


def _nearest_line_to_point(
    point:     Point,
    bar_lines: list[LineSegment],
    y_tol:     float,
) -> Optional[LineSegment]:
    candidates = [l for l in bar_lines if abs(l.mid_y - point.y) <= y_tol and l.x_left - 5 <= point.x <= l.x_right + 5]
    if not candidates:
        return None
    return min(candidates, key=lambda l: abs(l.mid_y - point.y))


# ── Pass 2: learned-y and ordinal fallback ────────────────────────────────────

def _learn_position_y(matches: list[MatchedBar]) -> dict[str, float]:
    """Average y per position label, from confident (leader_arrow) matches only."""
    by_label: dict[str, list[float]] = {}
    for m in matches:
        if m.match_method == "leader_arrow" and m.position and m.matched_fragment is not None:
            by_label.setdefault(m.position, []).append(m.matched_fragment.mid_y)
    return {label: sum(ys) / len(ys) for label, ys in by_label.items()}


def _match_via_learned_y(
    parsed:     ParsedBarData,
    learned_y:  dict[str, float],
    bar_lines:  list[LineSegment],
) -> Optional[LineSegment]:
    if not parsed.position or parsed.position not in learned_y:
        return None
    target_y = learned_y[parsed.position]
    candidates = [l for l in bar_lines if abs(l.mid_y - target_y) <= LEARNED_Y_CLUSTER_TOL]
    if not candidates:
        return None
    if parsed.x0 is not None:
        return min(candidates, key=lambda l: abs(l.mid_x - parsed.x0))
    return candidates[0]


def _match_via_ordinal(
    parsed:          ParsedBarData,
    bar_lines:       list[LineSegment],
    distinct_labels: list[str],
) -> Optional[LineSegment]:
    """
    Last-resort fallback, used only when a position label was never
    confidently matched anywhere in this beam (so learned_y has nothing
    for it). Maps position-label rank order to fragment y-cluster rank
    order — NOT a blind index into bar_lines, which has no necessary
    relationship to a label's numeric suffix (a beam with 2 fragments
    and a "T9" label is not fragment index 9).

    Deliberately conservative: if the number of distinct position labels
    used in this beam doesn't match the number of distinct y-clusters
    found in its geometry, the mapping is ambiguous and this returns
    None (leaving the bar unmatched) rather than guessing — a wrong
    "confident" match is worse than an honest unmatched one.
    """
    rank = _ordinal_rank(parsed.position)
    if rank is None or not bar_lines or parsed.position not in distinct_labels:
        return None

    clusters = _y_clusters(bar_lines)
    if len(clusters) != len(distinct_labels):
        return None

    sorted_labels = sorted(distinct_labels, key=lambda p: _ordinal_rank(p) if _ordinal_rank(p) is not None else 0)
    try:
        label_index = sorted_labels.index(parsed.position)
    except ValueError:
        return None

    cluster_y = clusters[label_index]
    candidates = [l for l in bar_lines if abs(l.mid_y - cluster_y) <= LEARNED_Y_CLUSTER_TOL]
    if not candidates:
        return None
    if parsed.x0 is not None:
        return min(candidates, key=lambda l: abs(l.mid_x - parsed.x0))
    return candidates[0]


def _y_clusters(bar_lines: list[LineSegment]) -> list[float]:
    """Distinct y-values among bar_lines, clustered within tolerance, sorted top-to-bottom."""
    ys = sorted(l.mid_y for l in bar_lines)
    clusters: list[float] = []
    for y in ys:
        if not clusters or y - clusters[-1] > LEARNED_Y_CLUSTER_TOL:
            clusters.append(y)
    return clusters


def _ordinal_rank(position: Optional[str]) -> Optional[int]:
    if not position or len(position) < 2 or position[0] not in ("T", "B"):
        return None
    try:
        n = int(position[1:])
    except ValueError:
        return None
    if position[0] == "T":
        return n
    return _ORDINAL_MAX_LAYER - n


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_match(
    parsed:      ParsedBarData,
    beam_id:     str,
    line:        Optional[LineSegment],
    frag_owner:  dict[int, PhysicalBar],
    method:      str,
    mark_counts: dict[str, int],
) -> MatchedBar:
    is_dup = bool(parsed.numeric_mark) and mark_counts.get(parsed.numeric_mark, 0) > 1
    physical_bar = frag_owner.get(id(line)) if line is not None else None
    return MatchedBar(
        beam_id=beam_id,
        numeric_mark=parsed.numeric_mark or "",
        position=parsed.position,
        diameter=parsed.diameter,
        quantity=parsed.quantity,
        x0=parsed.x0 if parsed.x0 is not None else 0.0,
        top=parsed.top if parsed.top is not None else 0.0,
        physical_bar=physical_bar,
        matched_fragment=line,
        match_method=method if physical_bar is not None else "unmatched",
        is_duplicate_mark=is_dup,
        source=parsed,
    )


def _unmatched(parsed: ParsedBarData, beam_id: str) -> MatchedBar:
    return _make_match(parsed, beam_id, None, {}, "unmatched", {})


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import re

    import pdfplumber

    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes
    from parser.geometry.bar_detector import detect_bars
    from parser.pdf.dimension_extractor import extract_dimension_matches

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"

    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
        page_w, page_h = pdf.pages[0].width, pdf.pages[0].height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)
    dim_matches = extract_dimension_matches(path)
    geometries = {g.beam_id: g for g in detect_bars(prims, boxes, dim_matches)}

    target_beam = sys.argv[2] if len(sys.argv) > 2 else "MBM 01"
    if target_beam not in geometries:
        print(f"Beam {target_beam!r} not found.")
        sys.exit(1)

    # Re-derive callout occurrences for the target beam directly from
    # words + its box (mirrors parser.py's own zone-filtering, minus the
    # patterns.py-based field parsing this smoke test doesn't have).
    box = geometries[target_beam].box
    pattern = re.compile(r'^(\d+)T(\d+)-(\d+)(?:\((\w+)\))?$')
    test_bars = []
    for w in words:
        if not (box['x_left'] <= w['x0'] <= box['x_right'] and box['y_top'] <= w['top'] <= box['y_bot']):
            continue
        m = pattern.fullmatch(w['text'])
        if not m:
            continue
        qty, dia, mark, pos = m.groups()
        test_bars.append(ParsedBarData(
            source="PDF", raw_text=w['text'], diameter=int(dia), length=None,
            spacing=None, quantity=int(qty), numeric_mark=mark, position=pos,
            confidence=1.0, page=1, beam_id=target_beam, beam_label=target_beam,
            x0=w['x0'], top=w['top'],
        ))

    print(f"Found {len(test_bars)} callout occurrences in {target_beam}:")
    for m in match_bars_to_geometry(test_bars, geometries[target_beam], prims):
        print(" ", m.summary())