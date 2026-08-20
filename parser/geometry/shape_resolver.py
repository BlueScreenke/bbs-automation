"""
parser/geometry/shape_resolver.py

Step 6D (part 2) — assigns a shape code and lays out the A/B/C/D
dimension values for each classified bar.

Division of labour vs length_calculator.py (explicit project decision)
------------------------------------------------------------------------
length_calculator.py is the ONLY module that turns geometry into a
millimetre number. This module never re-derives a number
length_calculator has already produced — it only decides WHICH shape
code applies, and WHERE each of length_calculator's numbers belongs in
the A-D layout. The two modules share information (this one consumes
BarLengthResult directly) rather than each doing the same work twice.

The one thing this module produces itself is a lookup KEY, never a
length: hook lengths are deliberately NOT computed in Python at all
(project decision) — they belong in an editable reference table in the
exported Excel workbook, resolved there via INDEX/MATCH exactly the way
the reference BBS template (Thome_Beam_BBS_Typical_floors.xlsm) already
does it: a small sheet keyed "{shape}_{steel}" (e.g. "11_T16" -> 250),
looked up per row. If that table's numbers ever need to change, the
Excel sheet is edited directly — this code never needs to change with
it. A Dimension with lookup_key set carries no value; the Excel export
step (Step 6E, not yet built) is what writes the actual formula.

Shape codes in scope (office convention, per project decision — not the
literal BS8666:2005 catalogue numbering)
------------------------------------------------------------------------
    00  straight bar, no hook, no bend
    11  bar hooked at one end
    21  bar hooked at both ends
    51  rectangular stirrup / link

Shape code is decided ENTIRELY from hook_count / is_stirrup, both
already resolved upstream (bar_detector.py's geometry assembly,
exposed via ClassifiedBar) — this module never re-inspects raw
geometry itself.

Public surface
--------------
    resolve_shape(classified_bar, length_result)  -> ShapeResult
    resolve_shapes(classified_bars, length_results) -> list[ShapeResult]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from parser.geometry.endpoint_classifier import ClassifiedBar
from parser.pdf.length_calculator import BarLengthResult


# ── Public result types ───────────────────────────────────────────────────────

@dataclass
class Dimension:
    """
    One A/B/C/D cell for the BBS. Exactly one of (value, lookup_key) is
    meaningful:
      - value set, lookup_key None  -> a plain number, already known
        from real geometry (length_calculator's output) — the Excel
        export step writes this directly into the cell.
      - value None, lookup_key set  -> a hook length this module
        deliberately does not know (project decision) — the Excel
        export step writes an INDEX/MATCH formula against the editable
        hook-parameter reference sheet using this key instead.
    """
    value:      Optional[float] = None
    lookup_key: Optional[str]   = None

    @property
    def is_lookup(self) -> bool:
        return self.lookup_key is not None


@dataclass
class ShapeResult:
    """Shape code + dimension layout for one bar occurrence."""
    shape_code:  str
    dimensions:  dict[str, Dimension] = field(default_factory=dict)
    note:        Optional[str] = None

    def summary(self) -> str:
        parts = []
        for k, v in self.dimensions.items():
            parts.append(f"{k}=lookup:{v.lookup_key}" if v.is_lookup else f"{k}={v.value}")
        return f"ShapeResult(shape={self.shape_code!r}, {', '.join(parts)})"


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_shapes(
    classified_bars: list[ClassifiedBar],
    length_results:  list[BarLengthResult],
) -> list[ShapeResult]:
    """One ShapeResult per (ClassifiedBar, BarLengthResult) pair, same order."""
    return [resolve_shape(cb, lr) for cb, lr in zip(classified_bars, length_results)]


def resolve_shape(classified_bar: ClassifiedBar, length_result: BarLengthResult) -> ShapeResult:
    """
    Resolve one bar occurrence's shape code and dimension layout.

    Args:
        classified_bar: Carries hook_count (0/1/2) and is_lapped — the
                         already-resolved end classification this
                         function routes on. Never re-derived here.
        length_result:   This bar's BarLengthResult from
                         length_calculator.calculate_bar_lengths() — the
                         sole source of every numeric value used below.
    """
    if length_result.is_stirrup:
        return _resolve_stirrup(length_result)

    if classified_bar.left_end.kind == "unclassified" or classified_bar.right_end.kind == "unclassified":
        # Geometry never resolved for this bar at all (e.g. an unmatched
        # callout) — hook_count would read 0 by default in this case,
        # which would otherwise silently masquerade as a confirmed
        # straight (shape 00) bar. Left genuinely unresolved instead,
        # consistent with "an honest unmatched result beats a wrong
        # confident match."
        return ShapeResult(shape_code=None, note=length_result.note)

    hook_count = classified_bar.hook_count
    if hook_count == 0:
        return _resolve_straight(length_result)
    if hook_count == 1:
        return _resolve_single_hook(length_result)
    return _resolve_double_hook(length_result)


# ── Per-shape layout ──────────────────────────────────────────────────────────

def _resolve_straight(lr: BarLengthResult) -> ShapeResult:
    if lr.straight_length_mm is None:
        return ShapeResult(shape_code="00", note=lr.note)
    return ShapeResult(
        shape_code="00",
        dimensions={"A": Dimension(value=lr.straight_length_mm)},
        note=lr.note,
    )


def _resolve_single_hook(lr: BarLengthResult) -> ShapeResult:
    key = _hook_key("11", lr.diameter)
    dims = {"A": Dimension(lookup_key=key)}
    if lr.straight_length_mm is not None:
        dims["B"] = Dimension(value=lr.straight_length_mm)
    return ShapeResult(shape_code="11", dimensions=dims, note=lr.note)


def _resolve_double_hook(lr: BarLengthResult) -> ShapeResult:
    key = _hook_key("21", lr.diameter)
    dims = {"A": Dimension(lookup_key=key)}
    if lr.straight_length_mm is not None:
        dims["B"] = Dimension(value=lr.straight_length_mm)
    # Same diameter -> same hook length at both ends (confirmed against
    # the reference workbook: shape 21 rows always carry identical A/C
    # values for a given bar size).
    dims["C"] = Dimension(lookup_key=key)
    return ShapeResult(shape_code="21", dimensions=dims, note=lr.note)


def _resolve_stirrup(lr: BarLengthResult) -> ShapeResult:
    if lr.stirrup_leg_width_mm is None or lr.stirrup_leg_depth_mm is None:
        return ShapeResult(shape_code="51", note=lr.note)
    return ShapeResult(
        shape_code="51",
        dimensions={
            "A": Dimension(value=lr.stirrup_leg_width_mm),
            "B": Dimension(value=lr.stirrup_leg_depth_mm),
            "C": Dimension(value=lr.stirrup_tail_mm),
            "D": Dimension(value=lr.stirrup_tail_mm),
        },
        note=lr.note,
    )


# ── Hook lookup key ───────────────────────────────────────────────────────────

def _hook_key(shape_code: str, diameter: Optional[int]) -> Optional[str]:
    """
    Builds e.g. "11_T16" — same key convention as the reference
    workbook's own hook-parameter table (Shape & "_" & Steel). None if
    diameter is unknown (should not normally happen — every bar reaching
    this point matched a callout, which always carries a diameter — but
    left as an explicit None rather than a fabricated key if it ever
    does).
    """
    if diameter is None:
        return None
    return f"{shape_code}_T{diameter}"


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import re as _re
    import pdfplumber

    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes
    from parser.geometry.bar_detector import detect_bars
    from parser.geometry.bar_matcher import match_bars_to_geometry
    from parser.geometry.endpoint_classifier import classify_all
    from parser.pdf.dimension_extractor import extract_dimension_matches
    from parser.pdf.length_calculator import calculate_bar_lengths, derive_scale_mm_per_pt
    from parser.pdf.models import ParsedBarData

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"
    target_beam = sys.argv[2] if len(sys.argv) > 2 else "MBM 01"

    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
        page_w, page_h = pdf.pages[0].width, pdf.pages[0].height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)
    dim_matches = extract_dimension_matches(path)
    scale = derive_scale_mm_per_pt(dim_matches)

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
    length_results = calculate_bar_lengths(classified, target_beam, scale)
    shape_results = resolve_shapes(classified, length_results)

    for c, sr in zip(classified, shape_results):
        print(f"  mark={c.matched_bar.numeric_mark:>4}  {sr.summary()}")