import pdfplumber

def extract_pdf_lines(path: str) -> list[tuple[int, str]]:
    lines = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if not text:
                continue
            for line in text.splitlines():
                lines.append((page_no, line.strip()))
    return lines