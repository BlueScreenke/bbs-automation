"""
parser/dwg/dxf_reader.py

Step D1 — reads a DXF file (via ezdxf) and buckets its modelspace
entities by semantic layer role (see layer_roles.py), producing a
DwgDrawing: the DWG-path equivalent of what pdf_line_extractor.py +
extractor.py (words) + dimension_extractor.py together produce for the
PDF path, but far less reconstruction is needed here because the
drafting software already gives us semantically separated layers and,
for dimensions, native DIMENSION objects with exact values and exact
endpoint coordinates (see project memory / handoff notes for the
investigation this is based on).

Units
-----
Confirmed against Serenity_beams.dxf: modelspace coordinates and
native DIMENSION.get_measurement() values are already in the same
units (mm) — a 1640-unit x-delta between two points corresponds
exactly to a drawn/measured "1640" dimension. Unlike the PDF path,
there is no pt-to-mm scale to derive; everything downstream that
expects a scale can be handed 1.0.

What is deliberately NOT extracted
-----------------------------------
CIRCLE entities on a bar-line-role layer are cross-section (section
view) markers, not elevation geometry — confirmed directly by the
project owner. Only LWPOLYLINE entities are read for bar/link/outline
roles; CIRCLE and other entity types on those layers are ignored here.

Public surface
--------------
    DwgDrawing (dataclass)
    load_dwg_drawing(dxf_path) -> DwgDrawing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import ezdxf

from parser.dwg.layer_roles import LayerRole, classify_layer
from parser.geometry.primitives import Point


@dataclass
class TextLabel:
    text: str
    x: float
    y: float


@dataclass
class RawPolyline:
    points: list[Point]
    layer: str

    @property
    def x_left(self) -> float:
        return min(p.x for p in self.points)

    @property
    def x_right(self) -> float:
        return max(p.x for p in self.points)

    @property
    def y_top(self) -> float:
        return min(p.y for p in self.points)

    @property
    def y_bot(self) -> float:
        return max(p.y for p in self.points)

    @property
    def mid_y(self) -> float:
        return (self.y_top + self.y_bot) / 2


@dataclass
class RawDimension:
    """One native DIMENSION entity's measured span."""
    value:   float   # exact measurement, in drawing units (== mm, confirmed)
    x_left:  float
    x_right: float
    y:       float


@dataclass
class DwgDrawing:
    bar_lines_top:      list[RawPolyline] = field(default_factory=list)
    bar_lines_bottom:   list[RawPolyline] = field(default_factory=list)
    bar_labels_top:      list[TextLabel]   = field(default_factory=list)
    bar_labels_bottom:   list[TextLabel]   = field(default_factory=list)
    link_lines:          list[RawPolyline] = field(default_factory=list)
    link_labels:          list[TextLabel]   = field(default_factory=list)
    kinks:                list[RawPolyline] = field(default_factory=list)
    beam_outline_edges:   list[RawPolyline] = field(default_factory=list)
    beam_labels:          list[TextLabel]   = field(default_factory=list)
    dimensions:           list[RawDimension] = field(default_factory=list)
    leader_shafts:        list[RawPolyline] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"DwgDrawing(bars_top={len(self.bar_lines_top)}, bars_bot={len(self.bar_lines_bottom)}, "
            f"labels_top={len(self.bar_labels_top)}, labels_bot={len(self.bar_labels_bottom)}, "
            f"link_lines={len(self.link_lines)}, link_labels={len(self.link_labels)}, "
            f"kinks={len(self.kinks)}, outline_edges={len(self.beam_outline_edges)}, "
            f"beam_labels={len(self.beam_labels)}, dimensions={len(self.dimensions)}, "
            f"leader_shafts={len(self.leader_shafts)})"
        )


def load_dwg_drawing(dxf_path: str) -> DwgDrawing:
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    dwg = DwgDrawing()

    for e in msp:
        role = classify_layer(e.dxf.layer)
        etype = e.dxftype()

        if role in (LayerRole.BAR_LINE_TOP, LayerRole.BAR_LINE_BOTTOM, LayerRole.LINK_LINE,
                    LayerRole.KINK, LayerRole.BEAM_OUTLINE, LayerRole.LEADER):
            poly = _as_polyline(e, etype)
            if poly is None:
                continue
            if role == LayerRole.BAR_LINE_TOP:
                dwg.bar_lines_top.append(poly)
            elif role == LayerRole.BAR_LINE_BOTTOM:
                dwg.bar_lines_bottom.append(poly)
            elif role == LayerRole.LINK_LINE:
                dwg.link_lines.append(poly)
            elif role == LayerRole.KINK:
                dwg.kinks.append(poly)
            elif role == LayerRole.BEAM_OUTLINE:
                dwg.beam_outline_edges.append(poly)
            elif role == LayerRole.LEADER:
                dwg.leader_shafts.append(poly)

        elif role in (LayerRole.BAR_LABEL_TOP, LayerRole.BAR_LABEL_BOTTOM,
                      LayerRole.LINK_LABEL, LayerRole.BEAM_LABEL):
            if etype != "TEXT":
                continue
            label = _as_label(e)
            if role == LayerRole.BAR_LABEL_TOP:
                dwg.bar_labels_top.append(label)
            elif role == LayerRole.BAR_LABEL_BOTTOM:
                dwg.bar_labels_bottom.append(label)
            elif role == LayerRole.LINK_LABEL:
                dwg.link_labels.append(label)
            elif role == LayerRole.BEAM_LABEL:
                dwg.beam_labels.append(label)

        elif role == LayerRole.DIMENSION_LINE and etype == "DIMENSION":
            dim = _as_dimension(e)
            if dim is not None:
                dwg.dimensions.append(dim)

    return dwg


def _as_polyline(e, etype: str) -> Optional[RawPolyline]:
    if etype == "LWPOLYLINE":
        pts = [Point(float(x), float(y)) for x, y in e.get_points("xy")]
    elif etype == "LINE":
        pts = [Point(float(e.dxf.start.x), float(e.dxf.start.y)),
               Point(float(e.dxf.end.x), float(e.dxf.end.y))]
    else:
        return None
    if len(pts) < 2:
        return None
    return RawPolyline(points=pts, layer=e.dxf.layer)


def _as_label(e) -> TextLabel:
    text = e.dxf.text.replace("%%U", "").replace("%%u", "").strip()
    x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
    return TextLabel(text=text, x=x, y=y)


def _as_dimension(e) -> Optional[RawDimension]:
    """
    Reads a linear DIMENSION's exact measured span from defpoint2/
    defpoint3 (confirmed empirically: these are the two extension-line
    origin points, i.e. the exact endpoints of what's being measured —
    e.g. defpoint2.x=-53923.1, defpoint3.x=-52283.1, delta=1640.0,
    matching get_measurement()==1640.0 exactly).
    """
    try:
        value = float(e.get_measurement())
    except Exception:
        return None
    p2, p3 = e.dxf.defpoint2, e.dxf.defpoint3
    x_left, x_right = sorted((float(p2.x), float(p3.x)))
    y = (float(p2.y) + float(p3.y)) / 2
    return RawDimension(value=value, x_left=x_left, x_right=x_right, y=y)
