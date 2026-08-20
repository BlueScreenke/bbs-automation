"""
parser/pdf/parser.py

Orchestrates the full PDF → ParsedBarData pipeline.

Changes this session
------------------------
1. Position inheritance for bare numeric-mark callouts. Confirmed on
   Page_1_beams.pdf: MBM 04's "2T16-25" has no bracketed position at
   all, sitting immediately after "2T20-24(B1)" on the same text row.
   This is a drafting shorthand — a bare mark inherits the nearest
   preceding bracketed callout's position on the same row — not a
   geometry-matching problem (its own leader-arrow geometry falls
   genuinely short of any real bar row, unlike the tolerance-edge cases
   bar_matcher.py's retry pass already handles). See
   _inherit_missing_positions().

2. The full Step 6B/6C geometry pipeline is now wired in:
   outline_detector.detect_beam_boxes() -> bar_detector.detect_bars()
   (assembles PhysicalBar objects: full merged spans, hook/lap/straight
   already resolved at both ends) -> bar_matcher.match_bars_to_geometry()
   (links each callout occurrence to its PhysicalBar) ->
   endpoint_classifier.classify_all() (exposes that classification,
   applying the 50 x diameter lap default once each bar's diameter is
   known). Every ParsedBarData this module returns now carries
   hook_count / is_lapped / lap_length / lap_source / match_method —
   see models.py for field definitions.

   detect_bars() is called once per page (like beam boxes and
   dimension matches), and its result list is in the same order as
   boxes — necessary because a beam_id can legitimately appear in more
   than one zone (confirmed: Page_1_beams.pdf's "MBM 05" duplicate-
   label quirk), so zone and geometry must be paired by position, not
   looked up by beam_id.

Changes from v4 (still in effect)
------------------------------------
  - Beam box construction moved to parser.geometry.outline_detector
    (geometry-first: real outline rectangles found directly from vector
    geometry, then the nearest "MBM n" label matched to each). Replaces
    dimension_extractor.get_beam_boxes(), whose label-midpoint math
    defaulted any single-label drawing row to the full page width — on
    Page_1_beams.pdf this affected 8 of 17 beams and produced confirmed
    cross-beam text contamination.
  - boxes are computed once per page and shared across the callout-text
    pass, dimension assignment, and (now) geometry detection — a single
    source of truth for beam zones.
  - Sets beam_id and beam_label on every ParsedBarData so beam_converter
    can group bars by zone and extract beam cross-section dimensions.
"""

from __future__ import annotations

import re
from typing import Optional

from parser.pdf.extractor import extract_words_with_coords
from parser.pdf.filter import is_bar_callout_token
from parser.pdf.patterns import (
    parse_diameter,
    parse_spacing,
    parse_quantity,
    parse_numeric_mark,
    parse_position,
)
from parser.pdf.models import ParsedBarData
from parser.pdf.dimension_extractor import extract_dimension_matches
from parser.pdf.length_calculator import calculate_bar_lengths, derive_scale_mm_per_pt
from parser.geometry.outline_detector import detect_beam_boxes
from parser.geometry.pdf_line_extractor import extract_page_primitives
from parser.geometry.bar_detector import detect_bars, BeamBarGeometry
from parser.geometry.bar_matcher import match_bars_to_geometry, _unmatched
from parser.geometry.endpoint_classifier import classify_all, ClassifiedBar
from parser.geometry.shape_resolver import resolve_shapes
from parser.geometry.primitives import PagePrimitives

import pdfplumber

_MAX_MARK_DIGITS = 4
_SECTION_RE = re.compile(r'\(\d+[xX]\d+', re.I)

# Position-inheritance tolerances (see module docstring, point 1).
# Confirmed case (MBM 04 marks 24/25): same top to the pixel, x-gap
# ~25.5pt. Y_TOL small — this only absorbs rounding within one drawn
# text row, never bridges two different rows. X_GAP bounded so
# inheritance can't reach across unrelated tokens further down the row.
POSITION_INHERIT_Y_TOL     = 2.0
POSITION_INHERIT_MAX_X_GAP = 60.0


def parse_pdf(path: str) -> list[ParsedBarData]:
    all_results: list[ParsedBarData] = []
    pages_words = extract_words_with_coords(path)

    for page_no, words in pages_words.items():
        page_w, page_h = _get_page_dims(path, page_no)
        primitives    = extract_page_primitives(path, page_no=page_no)
        boxes         = detect_beam_boxes(words, primitives, page_w, page_h, page_no=page_no)
        dim_matches   = extract_dimension_matches(path) if page_no == 1 else []
        scale         = derive_scale_mm_per_pt(dim_matches) if page_no == 1 else None
        geometries    = detect_bars(primitives, boxes, dim_matches) if page_no == 1 else [None] * len(boxes)

        for box, geometry in zip(boxes, geometries):
            beam_results = _process_beam_zone(words, box, geometry, primitives, scale)
            all_results.extend(beam_results)

    return all_results


def _process_beam_zone(
    words:      list[dict],
    box:        dict,
    geometry:   Optional[BeamBarGeometry],
    primitives: PagePrimitives,
    scale_mm_per_pt: Optional[float],
) -> list[ParsedBarData]:

    beam_id    = box['id']
    page_no    = box['page']
    zone_words = [w for w in words if _word_in_box(w, box)]
    beam_label = _find_beam_label(words, beam_id)

    raw_bars: list[ParsedBarData] = []
    for w in zone_words:
        token = w['text']
        if not is_bar_callout_token(token):
            continue

        bar = _parse_token(token, page_no, beam_id, beam_label, w['x0'], w['top'])
        if bar is None:
            continue

        raw_bars.append(bar)

    if not raw_bars:
        return []

    _inherit_missing_positions(raw_bars)
    classified_by_id = _attach_geometry_classification(raw_bars, geometry, primitives)

    keyed:   dict[str, list[ParsedBarData]] = {}
    unnamed: list[ParsedBarData]      = []
    for bar in raw_bars:
        if bar.numeric_mark:
            keyed.setdefault(bar.numeric_mark, []).append(bar)  # keeps every occurence
        else:
            unnamed.append(bar)

    bars: list[ParsedBarData] = [b for group in keyed.values() for b in group] + unnamed

    # classified_for_bars is looked up by identity (id()), not rebuilt
    # positionally, because the grouping step above can reorder bars
    # relative to raw_bars (marks are grouped by first occurrence, not
    # left in original text order) — a positional zip here would
    # silently pair the wrong bar with the wrong ClassifiedBar. See
    # bar_matcher.py's own docstring for the precedent bug this exact
    # mistake caused last session.
    classified_for_bars = [classified_by_id[id(b)] for b in bars]

    length_results = calculate_bar_lengths(classified_for_bars, beam_label, scale_mm_per_pt)
    shape_results   = resolve_shapes(classified_for_bars, length_results)

    for bar, lr, sr in zip(bars, length_results, shape_results):
        bar.shape_code = sr.shape_code
        if lr.is_stirrup:
            legs = (lr.stirrup_leg_width_mm, lr.stirrup_leg_depth_mm)
            bar.length = sum(legs) + 2 * lr.stirrup_tail_mm if all(v is not None for v in legs) else None
        else:
            bar.length = lr.straight_length_mm

        dim_a = sr.dimensions.get("A")
        dim_b = sr.dimensions.get("B")
        dim_c = sr.dimensions.get("C")
        dim_d = sr.dimensions.get("D")
        if dim_a is not None:
            bar.dim_a_mm, bar.dim_a_lookup_key = dim_a.value, dim_a.lookup_key
        if dim_b is not None:
            bar.dim_b_mm = dim_b.value
        if dim_c is not None:
            bar.dim_c_mm, bar.dim_c_lookup_key = dim_c.value, dim_c.lookup_key
        if dim_d is not None:
            bar.dim_d_mm = dim_d.value

    return bars


def _inherit_missing_positions(bars: list[ParsedBarData]) -> None:
    """
    A bare numeric-mark callout with no bracketed position (e.g.
    "2T16-25", confirmed on MBM 04 immediately after "2T20-24(B1)")
    inherits the nearest preceding bracketed callout's position — a
    drafting shorthand, not a geometry problem. Scoped to the same text
    row (top within POSITION_INHERIT_Y_TOL) and a bounded x-gap
    (POSITION_INHERIT_MAX_X_GAP) so it can never reach across unrelated
    tokens or a different row. Mutates bars in place.
    """
    rows: list[list[ParsedBarData]] = []
    for b in bars:
        if b.top is None:
            continue
        row = next((r for r in rows if abs(b.top - r[0].top) <= POSITION_INHERIT_Y_TOL), None)
        if row is None:
            rows.append([b])
        else:
            row.append(b)

    for row in rows:
        row.sort(key=lambda b: b.x0 if b.x0 is not None else 0.0)
        for i in range(1, len(row)):
            cur, prev = row[i], row[i - 1]
            if cur.position is not None or prev.position is None:
                continue
            if cur.x0 is None or prev.x0 is None:
                continue
            if (cur.x0 - prev.x0) > POSITION_INHERIT_MAX_X_GAP:
                continue
            cur.position = prev.position


def _attach_geometry_classification(
    bars:       list[ParsedBarData],
    geometry:   Optional[BeamBarGeometry],
    primitives: PagePrimitives,
) -> dict[int, ClassifiedBar]:
    """
    Runs the Step 6B/6C geometry pipeline for this beam zone, attaches
    the result to each ParsedBarData in place (hook_count, is_lapped,
    lap_length, lap_source, match_method — see models.py), and returns
    {id(bar): ClassifiedBar} so length_calculator.py and
    shape_resolver.py (Step 6D) can consume the full ClassifiedBar —
    not just these flattened fields — without re-running matching or
    classification a second time. A beam with no reliable outline
    (geometry is None, or geometry.has_outline is False) still returns
    one ClassifiedBar per bar (both ends "unclassified", via the same
    bar_matcher._unmatched() -> classify_all() path bar_matcher itself
    uses for a callout it fails to match) — length_calculator then
    reports "no matched PhysicalBar" for each, a real data gap rather
    than a silently-skipped one.
    """
    if geometry is None or not geometry.has_outline:
        for bar in bars:
            bar.match_method = "no_outline"
        beam_id = geometry.beam_id if geometry is not None else (bars[0].beam_id or "")
        classified = classify_all([_unmatched(b, beam_id) for b in bars])
        return {id(b): c for b, c in zip(bars, classified)}

    matched    = match_bars_to_geometry(bars, geometry, primitives)
    classified = classify_all(matched)

    result: dict[int, ClassifiedBar] = {}
    for bar, c in zip(bars, classified):
        bar.match_method = c.matched_bar.match_method
        bar.hook_count   = c.hook_count
        bar.is_lapped    = c.is_lapped

        lap_ends = [e for e in (c.left_end, c.right_end) if e.is_lap]
        if lap_ends:
            # Both ends lapped is rare but possible in principle — prefer
            # whichever end actually resolved a length over one that
            # couldn't (e.g. lap confirmed but no diameter available).
            lap_end = next((e for e in lap_ends if e.lap_length is not None), lap_ends[0])
            bar.lap_length = lap_end.lap_length
            bar.lap_source = lap_end.lap_source

        result[id(bar)] = c

    return result


def _parse_token(
    token:      str,
    page_no:    int,
    beam_id:    str,
    beam_label: str,
    x0:         float,
    top:        float,
) -> Optional[ParsedBarData]:
    diameter = parse_diameter(token)
    if diameter is None:
        return None

    numeric_mark = parse_numeric_mark(token)
    if numeric_mark and len(numeric_mark) > _MAX_MARK_DIGITS:
        return None

    quantity = parse_quantity(token)
    spacing  = parse_spacing(token)
    position = parse_position(token)

    confidence = 0.0
    if diameter:      confidence += 0.4
    if quantity:      confidence += 0.3
    if numeric_mark:  confidence += 0.2
    if position:      confidence += 0.1

    return ParsedBarData(
        source="PDF",
        raw_text=token,
        diameter=diameter,
        length=None,
        spacing=spacing,
        quantity=quantity,
        numeric_mark=numeric_mark,
        position=position,
        confidence=confidence,
        page=page_no,
        beam_id=beam_id,
        beam_label=beam_label,
        x0=x0,
        top=top,
    )


def _word_in_box(word: dict, box: dict) -> bool:
    x = float(word['x0'])
    y = float(word['top'])
    return (box['x_left'] <= x <= box['x_right'] and
            box['y_top']  <= y <= box['y_bot'])


def _find_beam_label(all_words: list[dict], beam_id: str) -> str:
    beam_num = beam_id.split(' ', 1)[-1]
    anchor = None
    for w in all_words:
        if w['text'] == 'MBM':
            siblings = sorted(
                [x for x in all_words if abs(x['top']-w['top']) < 6 and x['x0'] > w['x0']],
                key=lambda x: x['x0'],
            )
            if siblings and siblings[0]['text'] == beam_num:
                anchor = w
                break

    if anchor is None:
        return beam_id

    ax, ay = anchor['x0'], anchor['top']
    for w in all_words:
        if abs(float(w['top']) - ay) < 12 and abs(float(w['x0']) - ax) < 600:
            if _SECTION_RE.search(w['text']):
                return f"{beam_id} {w['text']}"

    return beam_id


def _get_page_dims(path: str, page_no: int) -> tuple[float, float]:
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_no - 1]
        return page.width, page.height