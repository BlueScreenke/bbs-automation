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

SHAFT_TEXT_TOL = 250.0
SHAFT_BAR_TOL  = 60.0
# Confirmed on Serenity_beams.dxf (FBM 1, "2T20-4(T1)"): a label's own y
# can sit ~470-515 units from its true bar's y (labels are placed for
# readability, not pinned to the bar's exact drawn y). 400 silently
# missed real matches; 700 clears the observed cases with margin.
NEAREST_Y_TOL  = 700.0

# How far outside a beam's own outline a label may sit and still count
# as belonging to it. Confirmed necessary: this drafting software draws
# a small "A-A" cross-section legend box immediately beside (not
# inside) the main elevation's own outline rectangle, and its own
# legend labels (e.g. "a=T16-2(B1)") sit past the outline's x_right by
# ~1100-1200 units on Serenity_beams.dxf. 2000 clears that with margin
# while staying safely under the smallest measured gap between two
# same-row adjacent beam zones (~2775 units) so it can't bleed into a
# neighbouring beam's own labels.
ZONE_LABEL_X_MARGIN = 2000.0


def match_labels_to_bars(
    dwg: DwgDrawing,
    zone: BeamZone,
    bars_top: list[PhysicalBar],
    bars_bottom: list[PhysicalBar],
) -> list[MatchedBar]:
    results: list[MatchedBar] = []

    for label in _labels_in_zone(dwg.bar_labels_top, zone):
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
    for label in _labels_in_zone(dwg.bar_labels_bottom, zone):
        if not is_bar_callout_token(label.text):
            continue
        if parse_spacing(label.text) is not None:
            results.append(_match_link(label, zone))
            continue
        results.append(_match_one(label, bars_bottom, dwg.leader_shafts, zone, "bottom"))
    for label in _labels_in_zone(dwg.link_labels, zone):
        if not is_bar_callout_token(label.text):
            continue
        results.append(_match_link(label, zone))

    mark_counts: dict[str, int] = {}
    for m in results:
        if m.numeric_mark:
            mark_counts[m.numeric_mark] = mark_counts.get(m.numeric_mark, 0) + 1
    for m in results:
        m.is_duplicate_mark = mark_counts.get(m.numeric_mark, 0) > 1

    return results


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


def _labels_in_zone(labels: list[TextLabel], zone: BeamZone) -> list[TextLabel]:
    return [l for l in labels if zone.contains_x(l.x, ZONE_LABEL_X_MARGIN) and zone.contains_y(l.y, 1500.0)]


def _match_one(
    label: TextLabel,
    candidate_bars: list[PhysicalBar],
    leader_shafts: list[RawPolyline],
    zone: BeamZone,
    position_role: str,
) -> MatchedBar:
    parsed = _parse_label(label, zone)

    bar, method = _via_leader_shaft(label, candidate_bars, leader_shafts)
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
) -> tuple[Optional[PhysicalBar], str]:
    text_point = Point(label.x, label.y)
    best_shaft, best_dist = None, SHAFT_TEXT_TOL
    for shaft in shafts:
        for end in (shaft.points[0], shaft.points[-1]):
            d = text_point.distance_to(end)
            if d < best_dist:
                best_shaft, best_dist = shaft, d
    if best_shaft is None:
        return None, "unmatched"

    # the far end of the shaft (whichever endpoint is NOT near the label)
    far_end = max(
        (best_shaft.points[0], best_shaft.points[-1]),
        key=lambda p: text_point.distance_to(p),
    )
    bar = _nearest_bar_to_point(far_end, bars, SHAFT_BAR_TOL)
    return (bar, "leader_arrow") if bar is not None else (None, "unmatched")


def _via_nearest(label: TextLabel, bars: list[PhysicalBar]) -> tuple[Optional[PhysicalBar], str]:
    candidates = [b for b in bars if abs(b.y - label.y) <= NEAREST_Y_TOL]
    if not candidates:
        return None, "unmatched"
    nearest = min(candidates, key=lambda b: (abs(b.y - label.y), min(abs(b.x_left - label.x), abs(b.x_right - label.x))))
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
