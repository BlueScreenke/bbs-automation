"""
parser/geometry/pdf_line_extractor.py

Extracts geometric primitives from structural engineering PDFs using PyMuPDF.

This is the PDF-specific implementation of the line extractor interface.
It converts PyMuPDF's internal drawing representation into the format-agnostic
PagePrimitives type defined in primitives.py.

When DWG support is added, a parallel dwg_line_extractor.py will produce the
same PagePrimitives output using ezdxf. Everything above this layer
(bar_detector, endpoint_classifier, shape_resolver) is unchanged.

PyMuPDF path item types handled
--------------------------------
'l'  → straight line segment  → LineSegment
'c'  → cubic bezier curve     → CubicBezier
'qu' → quadratic bezier       → approximated as CubicBezier
multi-segment paths           → Polyline (if ≥ 3 segments)
                              → LineSegment (if exactly 2 points / 1 segment)

Coordinate convention
---------------------
PyMuPDF uses a top-left origin with y increasing downward, which matches
pdfplumber's word-coordinate system. No coordinate transformation is applied.
"""

from __future__ import annotations

from typing import Optional

import fitz  # PyMuPDF

from parser.geometry.primitives import (
    Colour,
    CubicBezier,
    LineSegment,
    PagePrimitives,
    Point,
    Polyline,
)


def extract_page_primitives(pdf_path: str, page_no: int = 1) -> PagePrimitives:
    """
    Extract all geometric primitives from one page of a PDF.

    Args:
        pdf_path: Path to the PDF file.
        page_no:  1-indexed page number (default: 1).

    Returns:
        PagePrimitives containing all lines, beziers, and polylines.
    """
    doc  = fitz.open(pdf_path)
    page = doc[page_no - 1]

    result = PagePrimitives(
        page_width=page.rect.width,
        page_height=page.rect.height,
        page_no=page_no,
        source="pdf",
    )

    for drawing in page.get_drawings():
        stroke = Colour.from_tuple(drawing.get("color"))
        fill   = Colour.from_tuple(drawing.get("fill"))
        items  = drawing["items"]

        _process_drawing(items, stroke, fill, result)

    return result


def extract_all_pages(pdf_path: str) -> list[PagePrimitives]:
    """
    Extract primitives from every page in the PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        List of PagePrimitives, one per page, in page order.
    """
    doc = fitz.open(pdf_path)
    return [extract_page_primitives(pdf_path, i + 1) for i in range(len(doc))]


# ── Internal dispatch ─────────────────────────────────────────────────────────

def _process_drawing(
    items:  list,
    stroke: Optional[Colour],
    fill:   Optional[Colour],
    result: PagePrimitives,
) -> None:
    """
    Route a single PyMuPDF drawing path to the correct primitive type.

    Routing rules:
    - 1 item, type 'l'  → LineSegment
    - 1 item, type 'c'  → CubicBezier
    - 1 item, type 'qu' → CubicBezier (quadratic approximated as cubic)
    - 2+ items, all 'l' → Polyline
    - 2+ items, mixed   → decompose into individual LineSegments/CubicBeziers
    """
    if not items:
        return

    # Single straight line
    if len(items) == 1 and items[0][0] == 'l':
        seg = _make_line(items[0], stroke, fill)
        if seg:
            result.lines.append(seg)
        return

    # Single cubic bezier
    if len(items) == 1 and items[0][0] == 'c':
        bez = _make_bezier(items[0], stroke, fill)
        if bez:
            result.beziers.append(bez)
        return

    # Single quadratic bezier — approximate as cubic
    if len(items) == 1 and items[0][0] == 'qu':
        bez = _make_quad_as_cubic(items[0], stroke, fill)
        if bez:
            result.beziers.append(bez)
        return

    # Multi-segment path: all straight lines → Polyline
    if all(i[0] == 'l' for i in items):
        poly = _make_polyline(items, stroke, fill)
        if poly:
            result.polylines.append(poly)
        return

    # Multi-segment path: all beziers → sequence of CubicBeziers
    if all(i[0] == 'c' for i in items):
        for item in items:
            bez = _make_bezier(item, stroke, fill)
            if bez:
                result.beziers.append(bez)
        return

    # Mixed path: decompose each item individually
    for item in items:
        if item[0] == 'l':
            seg = _make_line(item, stroke, fill)
            if seg:
                result.lines.append(seg)
        elif item[0] == 'c':
            bez = _make_bezier(item, stroke, fill)
            if bez:
                result.beziers.append(bez)
        elif item[0] == 'qu':
            bez = _make_quad_as_cubic(item, stroke, fill)
            if bez:
                result.beziers.append(bez)


# ── Primitive constructors ────────────────────────────────────────────────────

def _make_line(
    item:   tuple,
    stroke: Optional[Colour],
    fill:   Optional[Colour],
) -> Optional[LineSegment]:
    """Build a LineSegment from a PyMuPDF 'l' item."""
    # item format: ('l', Point, Point)
    try:
        _, p1, p2 = item
        return LineSegment(
            start=Point(float(p1.x), float(p1.y)),
            end=Point(float(p2.x), float(p2.y)),
            stroke=stroke,
            fill=fill,
        )
    except (ValueError, IndexError, AttributeError):
        return None


def _make_bezier(
    item:   tuple,
    stroke: Optional[Colour],
    fill:   Optional[Colour],
) -> Optional[CubicBezier]:
    """Build a CubicBezier from a PyMuPDF 'c' item."""
    # item format: ('c', p0, p1, p2, p3)
    try:
        _, p0, p1, p2, p3 = item
        return CubicBezier(
            p0=Point(float(p0.x), float(p0.y)),
            p1=Point(float(p1.x), float(p1.y)),
            p2=Point(float(p2.x), float(p2.y)),
            p3=Point(float(p3.x), float(p3.y)),
            stroke=stroke,
            fill=fill,
        )
    except (ValueError, IndexError, AttributeError):
        return None


def _make_quad_as_cubic(
    item:   tuple,
    stroke: Optional[Colour],
    fill:   Optional[Colour],
) -> Optional[CubicBezier]:
    """
    Convert a PyMuPDF quadratic bezier ('qu') to cubic bezier approximation.

    Quadratic bezier has 3 control points (p0, ctrl, p3).
    Cubic equivalent: p1 = p0 + 2/3*(ctrl-p0), p2 = p3 + 2/3*(ctrl-p3).
    """
    try:
        _, p0, ctrl, p3 = item
        p1x = p0.x + (2/3) * (ctrl.x - p0.x)
        p1y = p0.y + (2/3) * (ctrl.y - p0.y)
        p2x = p3.x + (2/3) * (ctrl.x - p3.x)
        p2y = p3.y + (2/3) * (ctrl.y - p3.y)
        return CubicBezier(
            p0=Point(float(p0.x), float(p0.y)),
            p1=Point(p1x, p1y),
            p2=Point(p2x, p2y),
            p3=Point(float(p3.x), float(p3.y)),
            stroke=stroke,
            fill=fill,
        )
    except (ValueError, IndexError, AttributeError):
        return None


def _make_polyline(
    items:  list,
    stroke: Optional[Colour],
    fill:   Optional[Colour],
) -> Optional[Polyline]:
    """
    Build a Polyline from a sequence of PyMuPDF 'l' items.

    For a chain of line segments, each item's start point should be the
    same as the previous item's end point. We collect all unique waypoints.
    """
    if not items:
        return None

    try:
        points = [Point(float(items[0][1].x), float(items[0][1].y))]
        for item in items:
            _, _p1, p2 = item
            points.append(Point(float(p2.x), float(p2.y)))

        # Detect if closed (last point ≈ first point)
        closed = (
            len(points) > 2
            and abs(points[-1].x - points[0].x) < 0.5
            and abs(points[-1].y - points[0].y) < 0.5
        )
        if closed:
            points = points[:-1]  # remove duplicate closing point

        return Polyline(
            points=points,
            closed=closed,
            stroke=stroke,
            fill=fill,
        )
    except (ValueError, IndexError, AttributeError):
        return None


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"
    prims = extract_page_primitives(path, page_no=1)
    print(prims.summary())

    # Breakdown
    filled_polys   = [p for p in prims.polylines if p.is_filled]
    unfilled_polys = [p for p in prims.polylines if not p.is_filled]
    h_lines = [l for l in prims.lines if l.is_horizontal]
    v_lines = [l for l in prims.lines if l.is_vertical]

    print(f"\nLines        : {len(prims.lines)}")
    print(f"  Horizontal : {len(h_lines)}")
    print(f"  Vertical   : {len(v_lines)}")
    print(f"Beziers      : {len(prims.beziers)}")
    print(f"Polylines    : {len(prims.polylines)}")
    print(f"  Filled     : {len(filled_polys)}")
    print(f"  Unfilled   : {len(unfilled_polys)}")
    sizes = sorted(set(p.segment_count for p in prims.polylines))
    print(f"  Segment counts: {sizes}")