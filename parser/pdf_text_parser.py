class PDFTextParser:
    """
    Simulates extraction of raw text from structural drawings.
    Later we will replace this with pdfplumber / OCR.
    """

    def extract_text(self, file_path: str) -> str:
        # Placeholder for now
        # In real system: extract text from PDF
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()