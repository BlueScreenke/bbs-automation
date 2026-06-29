from parser.pdf.extractor import extract_pdf_lines
from parser.pdf.filter import is_rebar_candidate
from parser.pdf.patterns import (
    parse_diameter,
    parse_spacing,
    parse_quantity
)
from parser.pdf.models import ParsedBarData

def parse_pdf(path: str) -> list[ParsedBarData]:
    results = []

    for page, line in extract_pdf_lines(path):
        if not is_rebar_candidate(line):
            continue

        diameter = parse_diameter(line)
        spacing = parse_spacing(line)
        quantity = parse_quantity(line)

        confidence = 0.0
        if diameter: confidence += 0.4
        if spacing: confidence += 0.3
        if quantity: confidence += 0.3

        results.append(
            ParsedBarData(
                source="PDF",
                raw_text=line,
                diameter=diameter,
                length=None,
                spacing=spacing,
                quantity=quantity,
                bar_mark=None,
                confidence=confidence,
                page=page
            )
        )

    return results