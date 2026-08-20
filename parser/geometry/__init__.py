"""
parser/geometry/__init__.py

Public API for the geometry detection package.

This package is responsible for identifying the physical shape of each
reinforcing bar from the vector graphics of a structural drawing.

The package is deliberately format-agnostic above the extractor layer:
    pdf_line_extractor.py  → uses PyMuPDF
    dwg_line_extractor.py  → will use ezdxf (future)

Both produce PagePrimitives. Everything above that layer never touches
a file-format API.

Pipeline within this package:
    line_extractor  →  bar_detector  →  endpoint_classifier  →  shape_resolver

Public surface (what the rest of the project imports from here):
    extract_page_primitives   — Step 6A: get raw primitives from a PDF page
    detect_bars               — Step 6B: identify bar line segments
    classify_endpoints        — Step 6C: hook / lap / straight per bar end
    resolve_shape             — Step 6D: BS8666 shape code + A/B/C/D dims
"""

from parser.geometry.pdf_line_extractor import extract_page_primitives
from parser.geometry.primitives import PagePrimitives

__all__ = [
    "extract_page_primitives",
    "PagePrimitives",
]