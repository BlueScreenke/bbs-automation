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
from parser.geometry.endpoint_classifier import classify_all, ClassifiedBar
from parser.pdf.length_calculator import calculate_bar_lengths, BarLengthResult
from parser.geometry.shape_resolver import resolve_shapes
from parser.pdf.models import ParsedBarData

ZONE_DIM_Y_MARGIN = 2000.0

# length_calculator.py's own BOUNDARY_MATCH_TOL/CHAIN_GAP_TOL (15/30) were
# calibrated against PDF's own point-unit coordinates, where the local
# scale is roughly 18-20mm/pt — i.e. a real-world equivalent of roughly
# 270-600mm. DXF coordinates are already real millimetres (confirmed:
# a 1640-unit coordinate delta matches a drawn/measured 1640mm
# dimension exactly), so reusing 15/30 unchanged as literal millimetres
# was far tighter than intended and starved the dimension-chain search:
# confirmed directly (Serenity_beams.dxf) only 47 of 507 main bars got a
# full dimension-chain match with the PDF defaults, rising to 183 with
# these real-mm-equivalent values — and for double-hooked (shape 21)
# bars specifically, which per the project owner almost always run the
# beam's own overall dimensioned span, 0 of 52 matched a chain at all
# with the PDF defaults, rising to 46 of 52 with these.
DWG_BOUNDARY_MATCH_TOL = 300.0
DWG_CHAIN_GAP_TOL = 700.0


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
    length_results = calculate_bar_lengths(
        classified, zone.beam_label, zone_dims, fallback_scale=1.0,
        boundary_match_tol=DWG_BOUNDARY_MATCH_TOL, chain_gap_tol=DWG_CHAIN_GAP_TOL,
    )
    _apply_whole_span_override(classified, length_results, zone_dims)
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

        # A/B/C/D dimension cells and hook lookup keys — this is what
        # export/excel_exporter.py actually reads to populate the BBS
        # sheet's own A-D columns; the PDF path (parser.py) sets these
        # the same way. Missing this was a real bug this session: shape
        # and length were being copied, but the dimension cells
        # themselves were left at their None default, so every bar's
        # A-D columns rendered blank in the exported Excel regardless
        # of shape or resolved length.
        dim_a, dim_b = sr.dimensions.get("A"), sr.dimensions.get("B")
        dim_c, dim_d = sr.dimensions.get("C"), sr.dimensions.get("D")
        if dim_a is not None:
            bar.dim_a_mm, bar.dim_a_lookup_key = dim_a.value, dim_a.lookup_key
        if dim_b is not None:
            bar.dim_b_mm = dim_b.value
        if dim_c is not None:
            bar.dim_c_mm, bar.dim_c_lookup_key = dim_c.value, dim_c.lookup_key
        if dim_d is not None:
            bar.dim_d_mm = dim_d.value

        out.append(bar)
    return out


def _dims_in_zone(dwg: DwgDrawing, zone: BeamZone) -> list[dict]:
    # Dimensions are owned per zone by detect_beam_zones() (exclusive
    # assignment) — no separate margin-based capture here.
    return [
        {"value": d.value, "y": d.y, "x_left": d.x_left, "x_right": d.x_right}
        for d in zone.dimensions
    ]


# How much of the overall-span dimension's own width a hooked bar's raw
# geometric length must reach before the override applies — excludes a
# short curtailed/support bar near a column that happens to have one
# hook but was never meant to run the beam's full length.
WHOLE_SPAN_COVERAGE_MIN = 0.6


def _apply_whole_span_override(
    classified_bars: list[ClassifiedBar],
    length_results:  list[BarLengthResult],
    zone_dims:       list[dict],
) -> None:
    """
    Per Brian's own domain guidance (Session 6): site engineers verify a
    BBS against the drawing's own printed dimension lines, not against a
    geometry-derived figure — even one that is numerically accurate (as
    DWG geometry generally is). For a hooked main bar that runs close to
    a beam's full length, the drawing very often prints only the OVERALL
    beam-span dimension, not a dimension bracketing that specific bar's
    own two ends (confirmed real case: Serenity_beams FBM 1's hooked/
    lapped marks 4/5 — their own geometry doesn't chain-match any single
    dimension boundary, off by ~2.6m at one end, yet the zone's own
    "7000" overall-span dimension clearly is the one meant to apply).

    This runs AFTER length_calculator.calculate_bar_lengths() and only
    touches bars it left as "geometry-only fallback" (no dimension
    matched at all) — a bar that already resolved via a real dimension
    chain, partial or full, is never second-guessed here. Scope is
    deliberately narrow:
      - only hooked bars (hook_count > 0) — Brian confirmed straight
        (shape 00) bars' geometry-derived lengths are already accurate
        and should NOT be touched by this override;
      - only when the bar's own raw geometric length already covers most
        (WHOLE_SPAN_COVERAGE_MIN) of the candidate span dimension's own
        width, so a short curtailed bar near a column that happens to
        have a hook isn't wrongly stretched to the whole beam's span;
      - the "overall span" is taken as the single LARGEST dimension
        value in the zone (mirrors the PDF path's own SPAN_DIM_THRESHOLD_MM
        convention: the main span is the largest printed number on a
        beam's own row, distinct from smaller curtailment/support dims).

    This is intentionally NOT ported into the shared length_calculator.py
    — it is a DWG-specific override, kept out of the module both PDF and
    DWG call, so Beams_bondo.pdf's own already-verified numbers are
    completely unaffected.
    """
    if not zone_dims:
        return
    span_dim = max(zone_dims, key=lambda d: d["value"])

    for cb, lr in zip(classified_bars, length_results):
        if lr.is_stirrup or lr.note is None or not lr.note.startswith("geometry-only fallback"):
            continue
        if cb.hook_count == 0:
            continue
        pb = cb.matched_bar.physical_bar
        if pb is None or pb.length < span_dim["value"] * WHOLE_SPAN_COVERAGE_MIN:
            continue

        new_straight = round(span_dim["value"] + lr.lap_length_total_mm, 1)
        lr.note = (
            f"whole-span dimension override: {span_dim['value']:.1f}mm "
            f"(largest zone dimension) +{lr.lap_length_total_mm:.1f}mm lap "
            f"— {lr.note}"
        )
        lr.straight_length_mm = new_straight
