from parser.pdf.parser import parse_pdf
from parser.pdf.models import ParsedBarData

def test_parse_pdf_returns_parsed_bar_data():
    path = "tests/pdf/sample.pdf"
    results = parse_pdf(path)

    assert isinstance(results, list)
    assert all(isinstance(r, ParsedBarData) for r in results)


def test_parsed_bar_data_fields():
    path = "tests/pdf/sample.pdf"
    results = parse_pdf(path)

    assert len(results) > 0

    bar = results[0]
    assert hasattr(bar, "diameter")
    assert hasattr(bar, "spacing")
    assert hasattr(bar, "quantity")
    assert hasattr(bar, "confidence")
    assert 0.0 <= bar.confidence <= 1.0