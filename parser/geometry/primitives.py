"""
parser/geometry/primitives.py

Format-agnostic geometric primitive types produced by all line extractors.

This module is the abstraction boundary between file formats and the
shape detection logic. pdf_line_extractor.py (PyMuPDF) and the future
dwg_line_extractor.py (ezdxf) both produce these types. Everything in
bar_detector.py, endpoint_classifier.py, and shape_resolver.py consumes
only these types — they never touch a PDF or DWG API directly.

Coordinate system
-----------------
All coordinates are in points (PDF user units). The origin (0, 0) is the
top-left of the page. x increases rightward, y increases downward. This
matches pdfplumber's coordinate system, so spatial comparisons between
primitives and extracted text words work without conversion.

For DWG files: ezdxf uses a Y-up coordinate system. The extractor must
flip y-coordinates before populating these primitives so everything above
this layer uses the same convention.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ── Colour ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Colour:
    """Normalised RGB colour (each channel 0.0–1.0)."""
    r: float
    g: float
    b: float

    @classmethod
    def black(cls) -> "Colour":
        return cls(0.0, 0.0, 0.0)

    @classmethod
    def from_tuple(cls, t: Optional[tuple]) -> Optional["Colour"]:
        if t is None:
            return None
        return cls(float(t[0]), float(t[1]), float(t[2]))

    def is_black(self) -> bool:
        return self.r < 0.05 and self.g < 0.05 and self.b < 0.05


# ── Point ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Point:
    """An (x, y) coordinate in page space."""
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return ((self.x - other.x) ** 2 + (self.y - other.y) ** 2) ** 0.5


# ── Primitive types ───────────────────────────────────────────────────────────

@dataclass
class LineSegment:
    """
    A straight line segment between two points.

    In structural drawings these appear as:
    - Bar lines (long, horizontal, thin)
    - Extension lines at dimension ends (short, vertical)
    - Beam outlines (medium length, horizontal or vertical)
    - Diagonal hatching (rare in vector drawings)
    """
    start:  Point
    end:    Point
    stroke: Optional[Colour] = None
    fill:   Optional[Colour] = None

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def is_horizontal(self, tol: float = 2.0) -> bool:
        return abs(self.end.y - self.start.y) < tol

    @property
    def is_vertical(self, tol: float = 2.0) -> bool:
        return abs(self.end.x - self.start.x) < tol

    @property
    def mid_x(self) -> float:
        return (self.start.x + self.end.x) / 2

    @property
    def mid_y(self) -> float:
        return (self.start.y + self.end.y) / 2

    @property
    def x_left(self) -> float:
        return min(self.start.x, self.end.x)

    @property
    def x_right(self) -> float:
        return max(self.start.x, self.end.x)

    @property
    def y_top(self) -> float:
        return min(self.start.y, self.end.y)

    @property
    def y_bot(self) -> float:
        return max(self.start.y, self.end.y)


@dataclass
class CubicBezier:
    """
    A cubic bezier curve segment (PDF 'c' operator / DXF SPLINE).

    In structural drawings these appear as:
    - Small circular section markers (4 beziers forming a circle)
    - Bar end hooks (single bezier quarter-arc)
    - Column junction curves

    The four control points follow the standard cubic bezier convention:
    p0 = start, p1 = first handle, p2 = second handle, p3 = end.
    """
    p0: Point  # start
    p1: Point  # first control handle
    p2: Point  # second control handle
    p3: Point  # end
    stroke: Optional[Colour] = None
    fill:   Optional[Colour] = None

    @property
    def bounding_width(self) -> float:
        xs = [self.p0.x, self.p1.x, self.p2.x, self.p3.x]
        return max(xs) - min(xs)

    @property
    def bounding_height(self) -> float:
        ys = [self.p0.y, self.p1.y, self.p2.y, self.p3.y]
        return max(ys) - min(ys)


@dataclass
class Polyline:
    """
    A connected sequence of line segments (open or closed).

    In structural drawings these appear as:
    - Stirrup links (4-segment closed rectangle)
    - Beam rectangle outlines (4+ segments)
    - Filled arrowheads (3-segment filled triangle)
    - Column/beam junction filled markers (6-segment filled shape)

    A closed polyline has its last point coincident with its first point
    or is marked closed=True.
    """
    points:  list[Point]
    closed:  bool = False
    stroke:  Optional[Colour] = None
    fill:    Optional[Colour] = None

    @property
    def segment_count(self) -> int:
        n = len(self.points) - 1
        return n + 1 if self.closed else n

    @property
    def is_filled(self) -> bool:
        return self.fill is not None

    @property
    def bounding_box(self) -> tuple[float, float, float, float]:
        """Returns (x_left, y_top, x_right, y_bot)."""
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def width(self) -> float:
        bb = self.bounding_box
        return bb[2] - bb[0]

    @property
    def height(self) -> float:
        bb = self.bounding_box
        return bb[3] - bb[1]


# ── Page primitive collection ─────────────────────────────────────────────────

@dataclass
class PagePrimitives:
    """
    All geometric primitives extracted from a single page.

    This is the output contract of every line extractor module.
    bar_detector.py receives a PagePrimitives and works entirely with
    these types — it never calls into PyMuPDF or ezdxf.
    """
    lines:        list[LineSegment] = field(default_factory=list)
    beziers:      list[CubicBezier] = field(default_factory=list)
    polylines:    list[Polyline]    = field(default_factory=list)
    page_width:   float = 0.0
    page_height:  float = 0.0
    page_no:      int   = 1
    source:       str   = "unknown"   # "pdf", "dwg", etc.

    def summary(self) -> str:
        return (
            f"PagePrimitives(page={self.page_no}, source={self.source!r}, "
            f"lines={len(self.lines)}, beziers={len(self.beziers)}, "
            f"polylines={len(self.polylines)}, "
            f"size={self.page_width:.0f}×{self.page_height:.0f}pt)"
        )