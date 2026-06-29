import os
from pathlib import Path


def parse_input(file_path: str):
    """
    Entry point for all input parsing.

    Routes to the appropriate parser based on file extension:
      .pdf  → parser.pdf.parser.parse_pdf()  → list[ParsedBarData]
      .txt  → legacy text parser             → list[Beam]

    NOTE: The PDF path currently returns list[ParsedBarData].
    A ParsedBarData → Beam converter (step 3) will be added next,
    at which point this function will return list[Beam] for all inputs.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext == ".txt":
        return _parse_txt(file_path)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Expected .pdf or .txt."
        )


def _parse_pdf(file_path: str):
    """Delegates to the PDF parsing pipeline."""
    from parser.pdf.parser import parse_pdf
    return parse_pdf(file_path)


def _parse_txt(file_path: str):
    """Legacy path: reads plain text and builds a single Beam."""
    from parser.pdf_text_parser import PDFTextParser
    from parser.beam_builder import BeamBuilder

    text = PDFTextParser().extract_text(file_path)
    beam = BeamBuilder().build_beam(text)
    return [beam]