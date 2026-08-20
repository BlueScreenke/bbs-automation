"""
parser/__init__.py

Top-level parser entry point. Routes to the correct sub-parser based on
file extension and returns list[Beam] in all cases.

Supported formats:
    .pdf  →  parser.pdf pipeline  (parse_pdf → convert_to_beams)
    .dwg  →  future, not yet implemented

The legacy .txt path has been removed — it produced structurally invalid
beams (length=0.0) and was never connected to real output.
"""

from pathlib import Path


def parse_input(file_path: str):
    """
    Route a drawing file to the correct parser.

    Args:
        file_path: Path to the input file (.pdf or .dwg).

    Returns:
        list[Beam] — ready for validation and calculation.

    Raises:
        ValueError: If the file extension is not supported.
        NotImplementedError: If the format is recognised but not yet built.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext == ".dwg":
        raise NotImplementedError(
            "DWG support is planned but not yet implemented. "
            "Convert to PDF and rerun, or wait for the DWG module."
        )
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Expected .pdf or .dwg."
        )


def _parse_pdf(file_path: str):
    from parser.pdf import parse_pdf
    from parser.pdf.beam_converter import convert_to_beams

    parsed_bars = parse_pdf(file_path)
    return convert_to_beams(parsed_bars)