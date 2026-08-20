"""
parser/pdf/beam_converter.py

Converts a flat list[ParsedBarData] into list[Beam], grouped by beam zone.

Changes this session (Step 6D wiring)
------------------------------------------
- shape and dimension fields are now carried straight through from
  ParsedBarData onto Bar, instead of the shape="straight" placeholder
  every previous session left in place. Nothing is recomputed here —
  length_calculator.py and shape_resolver.py already did that work; this
  module's only job is grouping bars into their beam and forwarding
  what those two modules produced.
- A bar whose shape could not be resolved (item.shape_code is None — a
  genuine geometry gap, e.g. the two known OCR-garbage tokens) is now
  skipped with its own explicit reason, rather than silently forwarding
  shape=None and length=None into the BBS as if it were a real straight
  bar.

Changes in Step 3 (unchanged)
-----------------
- Groups bars by beam_id (e.g. "MBM 01") instead of by page number.
  Previously all bars on a page were merged into one "Page-N" Beam object,
  losing the beam-level structure the parser worked hard to build.

- Populates Bar.position and Bar.beam_id from ParsedBarData.

- Parses beam cross-section dimensions (width, depth) from beam_label
  so the Beam model carries accurate geometry instead of zeros.

- Skips bars with quantity=None (label-assignment tokens with no
  countable quantity — they cannot appear in the BBS).

- Keeps the length=None guard as a safety net for edge cases.
"""

from __future__ import annotations

import re
from typing import List, Optional

from parser.pdf.models import ParsedBarData
from models.bar import Bar
from models.beam import Beam

MIN_CONFIDENCE = 0.4

_SECTION_RE = re.compile(r'\((\d+)[xX](\d+)', re.I)


def convert_to_beams(parsed_bars: List[ParsedBarData]) -> List[Beam]:
    """
    Convert list[ParsedBarData] → list[Beam], one Beam per beam zone.

    Bars are grouped by beam_id. If beam_id is missing (legacy path),
    falls back to grouping by page number.

    Args:
        parsed_bars: Output from parse_pdf().

    Returns:
        list[Beam] ready for validation and calculation pipeline.
    """
    # Group by beam_id; fall back to "Page-N" for bars without one
    groups: dict[str, List[ParsedBarData]] = {}
    for item in parsed_bars:
        key = item.beam_id or f"Page-{item.page}"
        groups.setdefault(key, []).append(item)

    beams  = []
    skipped = []

    for beam_key, items in groups.items():
        # Extract cross-section dimensions from the first item's beam_label
        beam_label = next((i.beam_label for i in items if i.beam_label), beam_key)
        width, depth = _parse_section(beam_label)

        beam = Beam(
            id=beam_key,
            span_length=0,              # set from max(span_dims) in a future step
            width=width  or 0,
            depth=depth  or 0,
            concrete_grade="UNKNOWN",
        )

        for idx, item in enumerate(items, start=1):
            bar, reason = _convert_bar(item, beam_key, idx)
            if bar:
                beam.add_bar(bar)
            else:
                skipped.append((item.raw_text, reason))

        if beam.bars:
            beams.append(beam)

    if skipped:
        print(f"\n[beam_converter] Skipped {len(skipped)} bar(s):")
        for raw_text, reason in skipped:
            print(f"  SKIPPED ({reason}): {raw_text!r}")

    return beams


def _convert_bar(
    item:     ParsedBarData,
    beam_key: str,
    idx:      int,
) -> tuple:
    """
    Convert a single ParsedBarData to a Bar.

    Returns (Bar, None) on success or (None, reason_str) on skip.
    """
    if item.confidence < MIN_CONFIDENCE:
        return None, f"confidence {item.confidence:.2f} below threshold"

    if item.diameter is None:
        return None, "missing diameter"

    if item.quantity is None:
        return None, "missing quantity — label-assignment token with no count"

    if item.shape_code is None:
        return None, "shape could not be resolved (no matched geometry) — see match_method"

    if item.length is None:
        return None, "length could not be calculated"

    mark = item.numeric_mark or f"{beam_key}-{idx}"

    bar = Bar(
        mark=mark,
        diameter=item.diameter,
        shape=item.shape_code,
        length=item.length,
        quantity=item.quantity,
        steel_grade="HY",           # grade extraction deferred
        location=_location_from_position(item.position),
        position=item.position,     # ← new: e.g. "T1", "B1"
        beam_id=item.beam_id,       # ← new: e.g. "MBM 01"
        dim_a_mm=item.dim_a_mm,
        dim_b_mm=item.dim_b_mm,
        dim_c_mm=item.dim_c_mm,
        dim_d_mm=item.dim_d_mm,
        dim_a_lookup_key=item.dim_a_lookup_key,
        dim_c_lookup_key=item.dim_c_lookup_key,
    )

    return bar, None


def _parse_section(label: str) -> tuple[Optional[int], Optional[int]]:
    """
    Extract (width_mm, depth_mm) from a beam label like "MBM 01 (200x600mm)".
    Returns (None, None) if the pattern is not found.
    """
    m = _SECTION_RE.search(label)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _location_from_position(position: Optional[str]) -> Optional[str]:
    """
    Derive a coarse location tag from the position label for backward
    compatibility with any code that reads bar.location.

        T1, T2, T3  → "top"
        B1, B2, B3  → "bottom"
        None        → "stirrup"   (bars without a position are typically stirrups)
    """
    if position is None:
        return "stirrup"
    if position.startswith("T"):
        return "top"
    if position.startswith("B"):
        return "bottom"
    return None