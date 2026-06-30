from typing import List
from parser.pdf.models import ParsedBarData
from models.bar import Bar
from models.beam import Beam

# Minimum confidence score for a parsed bar to be included.
# Bars below this threshold are likely false positives.
MIN_CONFIDENCE = 0.4


def convert_to_beams(parsed_bars: List[ParsedBarData]) -> List[Beam]:
    """
    Converts a list of ParsedBarData into a list of Beam objects.

    Grouping strategy: one Beam per PDF page.
    Bars with missing diameter, missing length, or low confidence
    are skipped and reported.

    Args:
        parsed_bars: Output from parse_pdf()

    Returns:
        list[Beam] ready for validation and calculation pipeline
    """
    # Group parsed bars by page number
    pages: dict[int, List[ParsedBarData]] = {}
    for item in parsed_bars:
        pages.setdefault(item.page, []).append(item)

    beams = []
    skipped = []

    for page_num, items in sorted(pages.items()):
        beam = Beam(
            id=f"Page-{page_num}",
            span_length=0,   # not extractable from PDF text at this stage
            width=0,         # not extractable from PDF text at this stage
            depth=0,         # not extractable from PDF text at this stage
            concrete_grade="UNKNOWN"
        )

        for idx, item in enumerate(items, start=1):
            bar, reason = _convert_bar(item, page_num, idx)
            if bar:
                beam.add_bar(bar)
            else:
                skipped.append((item.raw_text, reason))

        # Only include the beam if it has at least one valid bar
        if beam.bars:
            beams.append(beam)

    if skipped:
        print(f"\n[beam_converter] Skipped {len(skipped)} bar(s):")
        for raw_text, reason in skipped:
            print(f"  SKIPPED ({reason}): {raw_text!r}")

    return beams
def _convert_bar(
    item: ParsedBarData,
    page_num: int,
    idx: int
) -> tuple:
    """
    Converts a single ParsedBarData to a Bar.

    Returns:
        (Bar, None)   if conversion succeeded
        (None, str)   if skipped, with a reason string
    """
    if item.confidence < MIN_CONFIDENCE:
        return None, f"confidence {item.confidence:.2f} below threshold"

    if item.diameter is None:
        return None, "missing diameter"

    if item.length is None:
        return None, "missing length"

    if item.quantity is None:
        return None, "missing quantity"

    mark = item.bar_mark or f"P{page_num}-{idx}"

    bar = Bar(
        mark=mark,
        diameter=item.diameter,
        shape="straight",       # shape extraction not implemented yet
        length=item.length,
        quantity=item.quantity,
        steel_grade="HY",       # default; grade extraction not implemented yet
        location=None
    )

    return bar, None

