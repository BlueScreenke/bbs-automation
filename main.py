"""
main.py

Runs the full pipeline: PDF -> parsed callouts -> beams -> Excel BBS.

Usage:
    python main.py path/to/drawing.pdf [output/bbs.xlsx]
"""

from __future__ import annotations

import sys

from parser.pdf import parse_pdf
from parser.pdf.beam_converter import convert_to_beams
from export.excel_exporter import export_to_excel


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main.py path/to/drawing.pdf [output/bbs.xlsx]")
        sys.exit(1)

    pdf_path    = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/bbs.xlsx"

    print(f"Parsing {pdf_path} ...")
    parsed_bars = parse_pdf(pdf_path)
    print(f"  {len(parsed_bars)} bar occurrences parsed")

    matched  = [b for b in parsed_bars if b.match_method not in ("unmatched", "no_outline")]
    resolved = [b for b in parsed_bars if b.shape_code is not None]
    print(f"  {len(matched)} matched to geometry, {len(resolved)} with a resolved shape")

    beams = convert_to_beams(parsed_bars)
    n_bars_in_beams = sum(len(b.bars) for b in beams)
    print(f"  {len(beams)} beams, {n_bars_in_beams} bars carried into the BBS")

    export_to_excel(beams, output_path)
    print(f"BBS exported -> {output_path}")


if __name__ == "__main__":
    main()