"""
parser/dwg/dwg_matcher.py

Step D4 — matches each bar/link label's text to the PhysicalBar it
names, within one beam zone. Reuses parser.geometry.bar_matcher's own
MatchedBar type so downstream (endpoint_classifier.classify_all) needs
no changes.

Why matching is simpler than the PDF path, and what's still first-pass
------------------------------------------------------------------------
Top/bottom position is already known from which label layer the text
sits on (BAR_LABEL_TOP vs BAR_LABEL_BOTTOM) — the PDF path has no such
signal and must infer it entirely from bracket suffixes like "(T1)".
That removes one whole axis of ambiguity for free.

What's genuinely still ambiguous: several bars can share the same
label layer within one beam (e.g. two distinct T1 bars at different x
positions), so this module still needs to decide WHICH bar a given
label names. Two passes, in order:
  1. Leader-arrow trace (same idea as the PDF path's primary pass):
     a "Leader Line" shaft with one end near the label's own insertion
     point and the other end near a bar's own endpoint.
  2. Nearest-bar fallback: the closest bar (by y, then x) of the
     correct position role in this zone. This is a simpler stand-in for
     the PDF path's learned-y/ordinal/retry fallback chain — it has not
     yet been validated against real ambiguous cases the way that
     chain was, and should be treated as provisional until checked
     against the rendered drawing on a beam with several same-layer
     bars.

Public surface
--------------
    match_labels_to_bars(dwg, zone, physical_bars_top, physical_bars_bottom) -> list[MatchedBar]
"""

from __future__ import annotations

import re
from typing import Optional

from parser.dwg.beam_zones import BeamZone
from parser.dwg.dxf_reader import DwgDrawing, RawPolyline, TextLabel
# NOTE: parser.pdf.models must be imported before parser.geometry.bar_matcher
# here. Entering the import graph via parser.geometry.bar_matcher first
# (before the parser.pdf package has been touched at all) triggers a
# circular import: bar_matcher.py itself does `from parser.pdf.models
# import ParsedBarData`, which runs parser/pdf/__init__.py, which imports
# parser.pdf.parser, which imports parser.geometry.bar_matcher back —
# while it's still mid-way through its own first import pass. Touching
# parser.pdf.models first lets that whole chain resolve cleanly before
# bar_matcher is imported here.
from parser.pdf.models import ParsedBarData
from parser.geometry.bar_matcher import MatchedBar
from parser.geometry.bar_detector import PhysicalBar
from parser.geometry.primitives import Point
from parser.pdf.patterns import (
    parse_diameter, parse_spacing, parse_quantity, parse_numeric_mark, parse_position,
)
from parser.pdf.filter import is_bar_callout_token

SHAFT_TEXT_TOL = 400.0
SHAFT_BAR_TOL  = 60.0
MIN_SHAFT_LENGTH = 30.0
# Confirmed on Serenity_beams.dxf (FBM 1, "2T20-4(T1)"): a label's own y
# can sit ~470-515 units from its true bar's y (labels are placed for
# readability, not pinned to the bar's exact drawn y). 400 silently
# missed real matches; 700 clears the observed cases with margin.
NEAREST_Y_TOL  = 700.0
X_CONTAINMENT_MARGIN = 300.0



def match_labels_to_bars(
    dwg: DwgDrawing,
    zone: BeamZone,
    bars_top: list[PhysicalBar],
    bars_bottom: list[PhysicalBar],
) -> list[MatchedBar]:
    results: list[MatchedBar] = []

    for label in zone.bar_labels_top:
        if not is_bar_callout_token(label.text):
            continue  # e.g. "a-b-b-a", "c-c" — section cross-reference codes, not callouts
        if parse_spacing(label.text) is not None:
            # A stirrup/link callout occasionally sits on the main
            # rebar-label layer rather than "Link Label" (confirmed:
            # Serenity_beams.dxf's "NxMT8-spacing-length(T)" tokens on
            # several curved/angled beams) — route by content, not by
            # which raw layer it came from, so it still gets the
            # no-geometry-needed stirrup path instead of failing main-
            # bar matching for lack of nearby bar geometry.
            results.append(_match_link(label, zone))
            continue
        results.append(_match_one(label, bars_top, dwg.leader_shafts, zone, "top"))
    for label in zone.bar_labels_bottom:
        if not is_bar_callout_token(label.text):
            continue
        if parse_spacing(label.text) is not None:
            results.append(_match_link(label, zone))
            continue
        results.append(_match_one(label, bars_bottom, dwg.leader_shafts, zone, "bottom"))
    for label in zone.link_labels:
        if not is_bar_callout_token(label.text):
            continue
        results.append(_match_link(label, zone))

    deduped = _dedupe_legend_references(results)

    mark_counts: dict[str, int] = {}
    for m in deduped:
        if m.numeric_mark:
            mark_counts[m.numeric_mark] = mark_counts.get(m.numeric_mark, 0) + 1
    for m in deduped:
        m.is_duplicate_mark = mark_counts.get(m.numeric_mark, 0) > 1

    return deduped


_LEGEND_RE = re.compile(r"^[A-Za-z]=")


def _is_legend(match: MatchedBar) -> bool:
    """Cross-section legend form ("a=T16-2(B1)", "c=T8-300-1")."""
    return bool(_LEGEND_RE.match(match.source.raw_text.strip()))


def _dedupe_legend_references(matches: list[MatchedBar]) -> list[MatchedBar]:
    """
    Removes cross-section legend labels that merely re-name a bar the
    beam already counts.

    Main bars — within a (numeric_mark, position) group of >1 occurrence:
      * if at least one occurrence is a confident "leader_arrow" match,
        every occurrence that is NOT a leader_arrow match is dropped (it
        has no independent geometric evidence of naming its own bar);
      * otherwise, a weak LEGEND occurrence is dropped when the group
        also holds a counted (non-legend) callout — the legend is just
        that callout's cross-section reference;
      * occurrences that are ALL confident and resolve to different
        PhysicalBars are all kept (a genuine lap, e.g. Beams_bondo MBM
        27); a group with no confident and no counted callout is left
        entirely for is_duplicate_mark review — this never guesses.

    Stirrups — a bare legend stirrup ("c=T8-300-1", default quantity 1)
    is dropped when the same beam also has a COUNTED stirrup ("10x1T8-
    300-1") with the same numeric mark and diameter: the legend names
    the section-view shape of that same stirrup and would otherwise add
    a phantom bar. A beam whose only stirrup callout is a bare legend
    keeps it. Counted stirrups that repeat (several spacing groups) are
    never touched.
    """
    dropped_ids: set[int] = set()

    groups: dict[tuple[str, Optional[str]], list[MatchedBar]] = {}
    for m in matches:
        if not m.numeric_mark or m.source.spacing is not None:
            continue
        groups.setdefault((m.numeric_mark, m.position), []).append(m)
    for group in groups.values():
        if len(group) < 2:
            continue
        confident = [m for m in group if m.match_method == "leader_arrow"]
        weak = [m for m in group if m.match_method != "leader_arrow"]
        if confident:
            dropped_ids.update(id(m) for m in weak)
        else:
            counted = [m for m in group if not _is_legend(m)]
            if counted:
                dropped_ids.update(id(m) for m in weak if _is_legend(m))

    stirrups = [m for m in matches if m.source.spacing is not None]
    for s in stirrups:
        if not _is_legend(s):
            continue
        has_counted_sibling = any(
            (not _is_legend(o)) and o.numeric_mark == s.numeric_mark and o.diameter == s.diameter
            for o in stirrups if o is not s
        )
        if has_counted_sibling:
            dropped_ids.add(id(s))

    return [m for m in matches if id(m) not in dropped_ids]


def _match_link(label: TextLabel, zone: BeamZone) -> MatchedBar:
    """
    Stirrup/link labels don't need geometry matching at all: per the
    project's own established convention (mirrors the PDF path),
    length_calculator.py computes a stirrup's length purely from the
    beam's own width/depth (parsed from beam_label) — it never reads
    the stirrup's matched_bar.physical_bar. physical_bar=None here is
    therefore not a matching failure; match_method says so explicitly
    rather than reporting "unmatched", which would wrongly suggest a
    real link line was searched for and not found.
    """
    parsed = _parse_label(label, zone)
    return MatchedBar(
        beam_id=zone.beam_id,
        numeric_mark=parsed.numeric_mark or "",
        position=parsed.position,
        diameter=parsed.diameter,
        quantity=parsed.quantity,
        x0=label.x, top=label.y,
        physical_bar=None,
        matched_fragment=None,
        match_method="stirrup_no_geometry_needed",
        is_duplicate_mark=False,
        source=parsed,
    )


def _match_one(
    label: TextLabel,
    candidate_bars: list[PhysicalBar],
    leader_shafts: list[RawPolyline],
    zone: BeamZone,
    position_role: str,
) -> MatchedBar:
    parsed = _parse_label(label, zone)

    bar, method = _via_leader_shaft(label, candidate_bars, leader_shafts, zone)
    if bar is None:
        bar, method = _via_nearest(label, candidate_bars)

    return MatchedBar(
        beam_id=zone.beam_id,
        numeric_mark=parsed.numeric_mark or "",
        position=parsed.position,
        diameter=parsed.diameter,
        quantity=parsed.quantity,
        x0=label.x, top=label.y,
        physical_bar=bar,
        matched_fragment=None,
        match_method=method if bar is not None else "unmatched",
        is_duplicate_mark=False,   # set by caller once all matches are known
        source=parsed,
    )


def _parse_label(label: TextLabel, zone: BeamZone) -> ParsedBarData:
    token = label.text
    diameter = parse_diameter(token)
    numeric_mark = parse_numeric_mark(token)
    quantity = parse_quantity(token)
    spacing = parse_spacing(token)
    position = parse_position(token)

    confidence = 0.0
    if diameter:     confidence += 0.4
    if quantity:      confidence += 0.3
    if numeric_mark:  confidence += 0.2
    if position:      confidence += 0.1

    return ParsedBarData(
        source="DWG", raw_text=token, diameter=diameter, length=None,
        spacing=spacing, quantity=quantity, numeric_mark=numeric_mark,
        position=position, confidence=confidence, page=0,
        beam_id=zone.beam_id, beam_label=zone.beam_label, x0=label.x, top=label.y,
    )


def _via_leader_shaft(
    label: TextLabel,
    bars: list[PhysicalBar],
    shafts: list[RawPolyline],
    zone: BeamZone,
) -> tuple[Optional[PhysicalBar], str]:
    text_point = Point(label.x, label.y)
    best_shaft, best_dist = None, SHAFT_TEXT_TOL
    for shaft in shafts:
        if shaft.points[0].distance_to(shaft.points[-1]) < MIN_SHAFT_LENGTH:
            continue  # arrowhead triangle, not a real shaft — see module docstring
        for end in (shaft.points[0], shaft.points[-1]):
            d = text_point.distance_to(end)
            if d < best_dist:
                best_shaft, best_dist = shaft, d
    if best_shaft is None:
        return None, "unmatched"

    bar_end = _bar_side_endpoint(best_shaft, zone, text_point)
    if bar_end is None:
        return None, "unmatched"
    bar = _nearest_bar_to_point(bar_end, bars, SHAFT_BAR_TOL)
    return (bar, "leader_arrow") if bar is not None else (None, "unmatched")


def _bar_side_endpoint(shaft: RawPolyline, zone: BeamZone, text_point: Point) -> Optional[Point]:
    """
    Two confirmed dogleg-shaft shapes on this drawing, needing two
    different disambiguation rules:
      - FBM 1 "T16-3(T1)": one end sits OUTSIDE the beam's own y-range
        (reaching up toward the label) and the other clearly inside —
        mirrors the PDF path's own convention (bar_matcher._shaft_endpoints):
        the outside end is the text side, the inside end is the bar side.
      - FBM 1 "2T16-2(B1)": BOTH ends sit inside the beam's own y-range
        (the callout is drawn within the beam's own depth band, not
        reaching above/below it as PDF's convention assumes) — there,
        "outside vs inside" is ambiguous, but simple distance to the
        label's own position correctly separates them (confirmed: the
        true text-side corner is unambiguously the nearer one here).
    Try the range test first since it's the more reliable signal when it
    applies; fall back to nearest-to-label only when both ends are on
    the same side of the beam's own range.
    """
    first, last = shaft.points[0], shaft.points[-1]
    first_inside = zone.contains_y(first.y)
    last_inside = zone.contains_y(last.y)
    if first_inside and not last_inside:
        return first
    if last_inside and not first_inside:
        return last
    # both inside or both outside — fall back to nearest-to-label
    return max((first, last), key=lambda p: text_point.distance_to(p))


def _via_nearest(label: TextLabel, bars: list[PhysicalBar]) -> tuple[Optional[PhysicalBar], str]:
    """
    Confirmed real mismatch (FBM 1, "T16-3(T1)"): several bars can share
    one position layer (T1) at very nearly the same y (same nominal
    depth) but occupy different x-spans along the beam — matching by y
    alone can land on the wrong one even when the label's own x sits
    squarely over its true bar. Prioritise x-containment (does the
    label's x fall within the candidate's own span, plus a margin) the
    same way the PDF path's matching does; only fall back to pure
    y-proximity when no candidate's span contains the label's x at all.
    """
    candidates = [b for b in bars if abs(b.y - label.y) <= NEAREST_Y_TOL]
    if not candidates:
        return None, "unmatched"

    containing = [b for b in candidates if b.x_left - X_CONTAINMENT_MARGIN <= label.x <= b.x_right + X_CONTAINMENT_MARGIN]
    pool = containing if containing else candidates
    nearest = min(pool, key=lambda b: abs(b.y - label.y))
    return nearest, "nearest_fallback"


def _nearest_bar_to_point(point: Point, bars: list[PhysicalBar], y_tol: float, x_margin: float = 30.0) -> Optional[PhysicalBar]:
    """
    Finds the bar whose own drawn line the point sits on (or very near),
    not merely near one of its endpoints — a leader shaft commonly
    points at a spot along a bar's length, not its tip. Mirrors the PDF
    path's own bar_matcher._nearest_line_to_point: match primarily by
    y-proximity, requiring the point's x to fall within the segment's
    own x-range (plus a small margin).
    """
    best, best_dist = None, y_tol
    for bar in bars:
        for seg in bar.segments:
            if seg.x_left - x_margin <= point.x <= seg.x_right + x_margin:
                d = abs(seg.mid_y - point.y)
                if d < best_dist:
                    best, best_dist = bar, d
    return best
