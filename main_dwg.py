"""
main_dwg.py

Runs the full DWG pipeline: DXF -> parsed callouts -> beams -> Excel BBS.
Mirrors main.py's own PDF flow so the two paths are directly comparable.

Usage:
    python main_dwg.py path/to/drawing.dxf [output/bbs.xlsx]
"""

from __future__ import annotations

import sys

from parser.dwg.dwg_parser import parse_dwg
from parser.pdf.beam_converter import convert_to_beams
from export.excel_exporter import export_to_excel


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python main_dwg.py path/to/drawing.dxf [output/bbs.xlsx]")
        sys.exit(1)

    dxf_path    = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "output/bbs_dwg.xlsx"

    print(f"Parsing {dxf_path} ...")
    parsed_bars = parse_dwg(dxf_path)
    print(f"  {len(parsed_bars)} bar occurrences parsed")

    matched  = [b for b in parsed_bars if b.match_method not in ("unmatched", "no_outline")]
    resolved = [b for b in parsed_bars if b.shape_code is not None]
    print(f"  {len(matched)} matched to geometry (or needing none, e.g. stirrups), {len(resolved)} with a resolved shape")

    beams = convert_to_beams(parsed_bars)
    n_bars_in_beams = sum(len(b.bars) for b in beams)
    print(f"  {len(beams)} beams, {n_bars_in_beams} bars carried into the BBS")

    export_to_excel(beams, output_path)
    print(f"BBS exported -> {output_path}")


if __name__ == "__main__":
    main()
