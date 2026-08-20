"""
parser/pdf/length_calculator.py

Step 6D (part 1) — turns real geometry into millimetre numbers.

Scope, per explicit project decision (this session)
--------------------------------------------------------
This module is the ONLY place in the pipeline that turns geometry into
a length in millimetres. It knows nothing about BS8666 shape codes —
that decision (and the A/B/C/D/E layout) belongs entirely to
shape_resolver.py, which consumes this module's output rather than
re-deriving any of it. The two modules must not duplicate each other's
job: this one measures, shape_resolver.py arranges.

Rewritten this session — three real changes
------------------------------------------------
1. Main-bar length no longer comes from a beam-wide `max_span` text
   value applied identically to every bar mark in the beam (the old,
   explicitly-flagged-as-approximate approach). It now comes from each
   bar's own real PhysicalBar span — the actual assembled geometry
   bar_detector.py already built and validated. This is a direct
   accuracy improvement: two different bar marks in the same beam can
   (and do) have genuinely different lengths, which a shared max_span
   could never represent.

2. Hook length is no longer computed here at all (project decision).
   Cover is still deducted at every non-lap end (hook or plain), but
   the hook's own added length is deliberately left for the Excel
   export step to resolve via a lookup table the person can edit
   without touching this code — see shape_resolver.py. This module's
   `straight_length_mm` is therefore the bar's known-in-Python portion
   only; for a hooked bar this is NOT the final cutting length (that
   final sum happens in Excel, mirroring how the reference workbook
   Thome_Beam_BBS_Typical_floors.xlsm already does it: Total Length =
   SUM(A:E), computed by a spreadsheet formula, not by this pipeline).

3. Stirrups are computed from beam cross-section (width/depth) and a
   fixed 100mm tail on each leg — per project decision, NOT from
   diameter, and NOT from PhysicalBar geometry (stirrup/link geometry
   matching remains an unbuilt gap; this formula doesn't need it):
       A (width leg)  = 2 x (beam_width - 2 x cover)
       B (depth leg)  = 2 x (beam_depth - 2 x cover)
       C, D (tails)   = 100mm each, fixed, regardless of diameter
   Confirmed against the reference workbook's own worked example (a
   200x600mm beam, 25mm cover): A = 2x(200-50) = 300, B = 2x(600-50) =
   1100, matching the sheet's own A=300, B=1100 for that beam exactly.

Main-bar straight-length formula
-------------------------------------
    straight_length_mm = physical_length_mm
                          - cover_mm x (count of non-lap ends)
                          + sum(lap_length_mm for each lap end)

physical_length_mm is the bar's real drawn span (PhysicalBar.length, in
points) converted to millimetres via the page's own derived scale (see
derive_scale_mm_per_pt below) — never assumed, always measured from the
drawing's own dimension callouts, consistent with every other constant
in this codebase.

Cover is deducted at hook and plain/curtailment ends (both are support-
face conditions in this formula) but not at lap ends, which are mid-
span splices — instead a lap end's own resolved lap length (already
computed by endpoint_classifier.py, either a drawn dimension or the
50 x diameter default) extends the bar. This treats "hook end" and
"plain end" the same way for the cover deduction, which is the one
assumption inherited from the pre-geometry version of this module that
real geometry hasn't yet let us refine further — flagged here
deliberately (see project's "flag uncertainty" principle) rather than
presented as settled. Worth revisiting if a specific bar's computed
length looks wrong on inspection.

Points-to-millimetres scale
--------------------------------
derive_scale_mm_per_pt() measures the drawing's own scale from its
dimension callouts (each one already carries both a printed mm value
and its own drawn pt-width — see dimension_extractor.py). Only span-
sized dimensions (>= SPAN_DIM_THRESHOLD_MM) are used, since a small
dimension's drawn pt-width carries much more relative rounding error.
The median across all qualifying matches is used (robust to any single
mismatched dimension line) — computed once, page-wide, since a single
PDF page is drawn at one consistent scale throughout.

Public surface
--------------
    calculate_bar_lengths(classified_bars, beam_label, scale_mm_per_pt) -> list[BarLengthResult]
    derive_scale_mm_per_pt(dimension_matches) -> Optional[float]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from parser.geometry.endpoint_classifier import ClassifiedBar


# ── Constants ─────────────────────────────────────────────────────────────────

COVER_BEAM_MM = 25          # uniform across all beams (confirmed, not just this drawing)

STIRRUP_LEG_MM = 100        # fixed tail length per leg, regardless of diameter

# Dimension matches below this are curtailment/offset callouts, not the
# drawing's own overall spans — their drawn pt-width carries too much
# relative rounding error to trust for scale derivation. Matches
# dimension_extractor.py's own SPAN_DIM_THRESHOLD.
SPAN_DIM_THRESHOLD_MM = 1000

_SECTION_RE = re.compile(r'\((\d+)[xX](\d+)', re.I)


# ── Public dataclass ────────────────────────────────────────────────────────

@dataclass
class BarLengthResult:
    """
    Length figures for one bar occurrence, in millimetres.

    straight_length_mm : main bars only. The bar's known-in-Python
        length portion — for shape 00 (no hooks) this IS the final
        cutting length; for a hooked bar it is NOT (the hook length is
        added later, in Excel — see module docstring). None if this
        bar has no matched PhysicalBar or no page scale was derivable
        — a genuine data gap, not guessed.

    stirrup_leg_width_mm / stirrup_leg_depth_mm : stirrups only. Both
        legs of the rectangular link. Unlike main bars, a stirrup's
        length is fully known in Python (see module docstring) — no
        Excel lookup needed for any of its four dimensions.
    """
    numeric_mark:            Optional[str]
    position:                 Optional[str]
    diameter:                  Optional[int]
    is_stirrup:                  bool
    straight_length_mm:          Optional[float]
    lap_length_total_mm:          float
    lap_end_count:                  int
    stirrup_leg_width_mm:     Optional[float] = None
    stirrup_leg_depth_mm:     Optional[float] = None
    stirrup_tail_mm:                   float = STIRRUP_LEG_MM
    note:                      Optional[str] = None
    raw_text:                           str = ""


# ── Public API ────────────────────────────────────────────────────────────────

def calculate_bar_lengths(
    classified_bars:  list[ClassifiedBar],
    beam_label:        str,
    scale_mm_per_pt:    Optional[float],
) -> list[BarLengthResult]:
    """
    Calculate length figures for every classified bar occurrence in one
    beam. See module docstring for the main-bar and stirrup formulas.

    Args:
        classified_bars: One ClassifiedBar per bar occurrence in this
                          beam (from endpoint_classifier.classify_all()),
                          carrying the real PhysicalBar geometry and
                          resolved end classifications this module
                          consumes directly — nothing here is re-derived
                          from raw primitives.
        beam_label:       Full beam label, e.g. "MBM 01 (200x600mm)" —
                          used only for stirrup cross-section dims.
        scale_mm_per_pt:  This page's derived scale (see
                          derive_scale_mm_per_pt). None if no page-wide
                          scale could be derived — every main bar's
                          straight_length_mm will be None in that case,
                          flagged via each result's note rather than
                          silently defaulted.

    Returns:
        One BarLengthResult per input ClassifiedBar, same order.
    """
    width, depth = _parse_section(beam_label)
    return [_calculate_single(cb, width, depth, scale_mm_per_pt) for cb in classified_bars]


def derive_scale_mm_per_pt(dimension_matches: list[dict]) -> Optional[float]:
    """
    Derive the drawing's points-to-millimetres scale from its own drawn
    dimension lines. See module docstring "Points-to-millimetres scale".

    Returns None if no qualifying (span-sized) dimension match exists at
    all on the page — callers must treat that as "cannot compute a
    geometry-based length here", never guess a fallback scale.
    """
    scales: list[float] = []
    for m in dimension_matches:
        width_pt = m['x_right'] - m['x_left']
        if m['value'] >= SPAN_DIM_THRESHOLD_MM and width_pt > 0:
            scales.append(m['value'] / width_pt)
    if not scales:
        return None
    scales.sort()
    return scales[len(scales) // 2]


# ── Single-bar routing ────────────────────────────────────────────────────────

def _calculate_single(
    cb:      ClassifiedBar,
    width:   Optional[int],
    depth:   Optional[int],
    scale:   Optional[float],
) -> BarLengthResult:
    parsed = cb.matched_bar.source
    is_stirrup = parsed.spacing is not None

    if is_stirrup:
        return _calculate_stirrup(cb, width, depth)
    return _calculate_main_bar(cb, scale)


# ── Main bars ─────────────────────────────────────────────────────────────────

def _calculate_main_bar(cb: ClassifiedBar, scale: Optional[float]) -> BarLengthResult:
    mb        = cb.matched_bar
    diameter  = mb.diameter
    pb        = mb.physical_bar

    if pb is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
            is_stirrup=False, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note=f"no matched PhysicalBar (match_method={mb.match_method!r}) — cannot compute length",
            raw_text=mb.source.raw_text,
        )

    if scale is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
            is_stirrup=False, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note="no page-wide pt->mm scale derivable — no qualifying span dimensions found",
            raw_text=mb.source.raw_text,
        )

    physical_length_mm = pb.length * scale

    ends           = (cb.left_end, cb.right_end)
    lap_ends       = [e for e in ends if e.is_lap]
    non_lap_count  = len(ends) - len(lap_ends)
    lap_total_mm   = sum(e.lap_length for e in lap_ends if e.lap_length is not None)

    straight_length_mm = physical_length_mm - COVER_BEAM_MM * non_lap_count + lap_total_mm

    note = (
        f"geometry-based: {pb.length:.1f}pt x scale {scale:.5f} = {physical_length_mm:.1f}mm, "
        f"-{COVER_BEAM_MM}mm cover x{non_lap_count} end(s), +{lap_total_mm:.1f}mm lap"
    )

    return BarLengthResult(
        numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
        is_stirrup=False,
        straight_length_mm=round(straight_length_mm, 1),
        lap_length_total_mm=round(lap_total_mm, 1),
        lap_end_count=len(lap_ends),
        note=note, raw_text=mb.source.raw_text,
    )


# ── Stirrups ──────────────────────────────────────────────────────────────────

def _calculate_stirrup(cb: ClassifiedBar, width: Optional[int], depth: Optional[int]) -> BarLengthResult:
    mb = cb.matched_bar

    if width is None or depth is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=mb.diameter,
            is_stirrup=True, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note="beam cross-section dimensions not available — cannot compute stirrup legs",
            raw_text=mb.source.raw_text,
        )

    leg_width_mm = 2 * (width - 2 * COVER_BEAM_MM)
    leg_depth_mm = 2 * (depth - 2 * COVER_BEAM_MM)

    note = (
        f"stirrup: A=2x({width}-2x{COVER_BEAM_MM})={leg_width_mm}mm, "
        f"B=2x({depth}-2x{COVER_BEAM_MM})={leg_depth_mm}mm, tails={STIRRUP_LEG_MM}mm each"
    )

    return BarLengthResult(
        numeric_mark=mb.numeric_mark, position=mb.position, diameter=mb.diameter,
        is_stirrup=True,
        straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
        stirrup_leg_width_mm=leg_width_mm, stirrup_leg_depth_mm=leg_depth_mm,
        note=note, raw_text=mb.source.raw_text,
    )


# ── Section dimension parser (mirrors beam_converter.py's own copy) ───────────

def _parse_section(label: str) -> tuple[Optional[int], Optional[int]]:
    """Extract (width_mm, depth_mm) from a beam label like 'MBM 01 (200x600mm)'."""
    m = _SECTION_RE.search(label)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    import pdfplumber

    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes
    from parser.geometry.bar_detector import detect_bars
    from parser.geometry.bar_matcher import match_bars_to_geometry
    from parser.geometry.endpoint_classifier import classify_all
    from parser.pdf.dimension_extractor import extract_dimension_matches
    from parser.pdf.models import ParsedBarData
    import re as _re

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"
    target_beam = sys.argv[2] if len(sys.argv) > 2 else "MBM 01"

    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
        page_w, page_h = pdf.pages[0].width, pdf.pages[0].height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)
    dim_matches = extract_dimension_matches(path)
    scale = derive_scale_mm_per_pt(dim_matches)
    print(f"Derived scale: {scale} mm/pt")

    geometries = {g.beam_id: g for g in detect_bars(prims, boxes, dim_matches)}
    if target_beam not in geometries:
        print(f"Beam {target_beam!r} not found.")
        sys.exit(1)

    box = geometries[target_beam].box
    pattern = _re.compile(r'^(\d+)T(\d+)-(\d+)(?:\((\w+)\))?$')
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

    matched = match_bars_to_geometry(test_bars, geometries[target_beam], prims)
    classified = classify_all(matched)
    results = calculate_bar_lengths(classified, target_beam, scale)

    for r in results:
        print(f"  mark={r.numeric_mark:>4} pos={str(r.position):>5} straight={r.straight_length_mm} "
              f"lap_total={r.lap_length_total_mm} — {r.note}")