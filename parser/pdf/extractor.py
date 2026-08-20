import pdfplumber


def extract_pdf_lines(path: str) -> list[tuple[int, str]]:
    """
    Legacy flat-text extractor. Kept for backward compatibility.
    Does not preserve coordinates — not used by the Step 2 pipeline.
    """
    lines = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if not text:
                continue
            for line in text.splitlines():
                lines.append((page_no, line.strip()))
    return lines


def extract_words_with_coords(path: str) -> dict[int, list[dict]]:
    """
    Extract all words with their bounding-box coordinates, grouped by page.

    Returns:
        { page_no (1-indexed): [pdfplumber word dicts, ...] }

    Each word dict has at minimum: text, x0, top, x1, bottom.
    """
    pages = {}
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            if words:
                pages[page_no] = words
    return pages