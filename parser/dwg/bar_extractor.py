"""
parser/dwg/bar_extractor.py

Step D3 — turns each DXF bar polyline into a PhysicalBar (reusing
parser.geometry.bar_detector's own PhysicalBar/BarEnd types directly,
so endpoint_classifier.py, length_calculator.py and shape_resolver.py
downstream need no changes at all to consume DWG-derived bars).

Why one polyline == one PhysicalBar here, unlike the PDF path
------------------------------------------------------------------
bar_detector.py's whole fragment-assembly/junction-shift machinery
exists because a PDF gives each bar as several disconnected path
fragments with no drawn signal for "these belong to one bar" beyond
inference. Confirmed on Serenity_beams.dxf: a "Rebar Line"/"Top Rebar
Line" LWPOLYLINE is already one complete, continuous entity per bar,
hook to hook — no fragment reassembly is needed at all.

Hook / lap classification (first pass — needs validation against the
rendered drawing before being trusted the way the PDF path's
equivalent heuristics are)
------------------------------------------------------------------------
- HOOK: the polyline's terminal segment (the one touching the bar's own
  x-extreme) has a vertical rise/fall at least HOOK_LEG_MIN_LENGTH —
  i.e. the end doesn't stay flat, matching the same "hook = vertical
  leg at the end" convention already confirmed on the PDF path.
- LAP: a "Rebar Kink" entity (short line, layer role KINK) has an
  endpoint within KINK_SEARCH_TOL of the bar's own endpoint. Lap length
  is not resolved from geometry here (mirrors the PDF path) — it is
  left for endpoint_classifier.py's existing 50xdiameter default once a
  diameter is matched, unless a native DIMENSION is found bracketing
  the junction (not yet wired in this first pass).
- Otherwise: straight end.

This is a first pass. It has NOT yet been validated bar-by-bar against
the rendered drawing the way every PDF-path heuristic was (see
bar_detector.py's docstring for the level of empirical grounding that
took). Treat hook/lap counts from this module as provisional.

Public surface
--------------
    extract_physical_bars(dwg, zone, position) -> list[PhysicalBar]
      position: "top" | "bottom"
"""

from __future__ import annotations

from parser.dwg.beam_zones import BeamZone
from parser.dwg.dxf_reader import DwgDrawing, RawPolyline
from parser.geometry.bar_detector import BarEnd, PhysicalBar
from parser.geometry.primitives import LineSegment, Point

HOOK_LEG_MIN_LENGTH = 15.0   # vertical rise/fall at an end to call it a hook (mm)
KINK_SEARCH_TOL = 60.0       # how close a Rebar Kink entity must sit to a bar endpoint (mm)
ZONE_Y_MARGIN = 300.0
# A bar polyline's own x-extent occasionally runs slightly past the
# beam outline's nominal edge (confirmed on Serenity_beams.dxf, FBM 6:
# up to ~255 units past x_left) — a bar's drawn hook/kink can overshoot
# the outline box a little. 5.0 was too tight and silently dropped real
# bars from their own zone; 400 clears the observed overshoot with
# margin while staying well under inter-beam spacing.
ZONE_X_MARGIN = 400.0


def extract_physical_bars(dwg: DwgDrawing, zone: BeamZone, position: str) -> list[PhysicalBar]:
    polylines = dwg.bar_lines_top if position == "top" else dwg.bar_lines_bottom
    in_zone = [
        p for p in polylines
        if zone.contains_x(p.x_left, ZONE_X_MARGIN) and zone.contains_x(p.x_right, ZONE_X_MARGIN)
        and zone.contains_y(p.mid_y, ZONE_Y_MARGIN)
    ]

    bars: list[PhysicalBar] = []
    for i, poly in enumerate(in_zone):
        segments = [
            LineSegment(start=poly.points[j], end=poly.points[j + 1])
            for j in range(len(poly.points) - 1)
        ]
        pid = f"{zone.beam_id}:{position}#{i}"
        rep_y = max(segments, key=lambda s: s.length).mid_y

        left_point = min(poly.points, key=lambda p: p.x)
        right_point = max(poly.points, key=lambda p: p.x)

        bar = PhysicalBar(
            id=pid, beam_id=zone.beam_id, y=rep_y, segments=segments,
            left_end=_resolve_end(poly, left_point, dwg),
            right_end=_resolve_end(poly, right_point, dwg),
        )
        bars.append(bar)
    return bars


def _resolve_end(poly: RawPolyline, point: Point, dwg: DwgDrawing) -> BarEnd:
    if _has_vertical_leg_at(poly, point):
        return BarEnd(kind="hook", point=point, detail="vertical leg at polyline end")
    kink = _nearest_kink(point, dwg.kinks)
    if kink is not None:
        return BarEnd(kind="lap", point=point, lap_length=None, lap_source=None,
                      detail="Rebar Kink entity found near this end")
    return BarEnd(kind="straight", point=point, detail="no hook/kink marker found near this end")


def _has_vertical_leg_at(poly: RawPolyline, point: Point) -> bool:
    idx = next(i for i, p in enumerate(poly.points) if p is point or (p.x == point.x and p.y == point.y))
    # look at the segment(s) touching this vertex
    neighbours = []
    if idx > 0:
        neighbours.append(poly.points[idx - 1])
    if idx < len(poly.points) - 1:
        neighbours.append(poly.points[idx + 1])
    for n in neighbours:
        if abs(n.y - point.y) >= HOOK_LEG_MIN_LENGTH:
            return True
    return False


def _nearest_kink(point: Point, kinks: list[RawPolyline]) -> RawPolyline | None:
    for k in kinks:
        for p in k.points:
            if point.distance_to(p) <= KINK_SEARCH_TOL:
                return k
    return None
