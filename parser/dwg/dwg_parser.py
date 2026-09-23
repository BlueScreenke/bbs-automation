"""
parser/dwg/dwg_parser.py

Step D5 — orchestrates the DWG pipeline per beam zone, mirroring
parser.pdf.parser.parse_pdf()'s own structure so the two paths produce
directly comparable ParsedBarData output. Reuses
endpoint_classifier.classify_all(), length_calculator.calculate_bar_lengths()
and shape_resolver.resolve_shapes() unchanged — only geometry extraction
and label matching differ from the PDF path.

Public surface
--------------
    parse_dwg(dxf_path) -> list[ParsedBarData]
"""

from __future__ import annotations

from parser.dwg.beam_zones import BeamZone, detect_beam_zones
from parser.dwg.bar_extractor import extract_physical_bars
from parser.dwg.dwg_matcher import match_labels_to_bars
from parser.dwg.dxf_reader import DwgDrawing, load_dwg_drawing
from parser.geometry.endpoint_classifier import classify_all
from parser.pdf.length_calculator import calculate_bar_lengths
from parser.geometry.shape_resolver import resolve_shapes
from parser.pdf.models import ParsedBarData

ZONE_DIM_Y_MARGIN = 2000.0


def parse_dwg(dxf_path: str) -> list[ParsedBarData]:
    dwg = load_dwg_drawing(dxf_path)
    zones = detect_beam_zones(dwg)

    all_bars: list[ParsedBarData] = []
    for zone in zones:
        bars = _process_zone(dwg, zone)
        all_bars.extend(bars)
    return all_bars


def _process_zone(dwg: DwgDrawing, zone: BeamZone) -> list[ParsedBarData]:
    bars_top = extract_physical_bars(dwg, zone, "top")
    bars_bottom = extract_physical_bars(dwg, zone, "bottom")

    matched = match_labels_to_bars(dwg, zone, bars_top, bars_bottom)
    if not matched:
        return []

    classified = classify_all(matched)

    zone_dims = _dims_in_zone(dwg, zone)
    length_results = calculate_bar_lengths(classified, zone.beam_label, zone_dims, fallback_scale=1.0)
    shape_results = resolve_shapes(classified, length_results)

    out: list[ParsedBarData] = []
    for m, lr, sr in zip(matched, length_results, shape_results):
        bar = m.source
        bar.match_method = m.match_method
        bar.shape_code = sr.shape_code
        if lr.is_stirrup:
            legs = (lr.stirrup_leg_width_mm, lr.stirrup_leg_depth_mm)
            bar.length = sum(legs) + 2 * lr.stirrup_tail_mm if all(v is not None for v in legs) else None
        else:
            bar.length = lr.straight_length_mm
        out.append(bar)
    return out


def _dims_in_zone(dwg: DwgDrawing, zone: BeamZone) -> list[dict]:
    return [
        {"value": d.value, "y": d.y, "x_left": d.x_left, "x_right": d.x_right}
        for d in dwg.dimensions
        if zone.contains_x((d.x_left + d.x_right) / 2, 5.0)
        and zone.contains_y(d.y, ZONE_DIM_Y_MARGIN)
    ]
