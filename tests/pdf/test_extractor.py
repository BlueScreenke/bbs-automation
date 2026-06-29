from parser.pdf.extractor import extract_pdf_lines

def test_extract_pdf_lines_returns_list():
    # This assumes you have a very small test PDF
    path = "tests/pdf/sample.pdf"
    lines = extract_pdf_lines(path)

    assert isinstance(lines, list)
    assert len(lines) > 0
    assert isinstance(lines[0], tuple)
    assert isinstance(lines[0][0], int)   # page number
    assert isinstance(lines[0][1], str)   # text line