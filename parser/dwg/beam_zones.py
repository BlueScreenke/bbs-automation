"""
parser/dwg/beam_zones.py

Step D2 — groups "beam outline" edges into one rectangle per beam and
assigns each its beam label.

Why this is simpler than PDF's outline_detector.py
------------------------------------------------------
The PDF path has to reconstruct outline rectangles from disconnected,
possibly-duplicated line fragments with no guarantee two edges of the
same rectangle share an endpoint exactly (see outline_detector.py's
X_FUSE_GAP discussion). In DXF, "Beam Line(D)" edges are individual
2-point LWPOLYLINE entities that snap to shared endpoints (confirmed:
consecutive edges of the same outline share exact coordinates to
float precision) — so a plain union-find over shared endpoints, with
no gap-fusing heuristics at all, correctly reconstructs each beam's
full outline polygon (however many edges it has — a beam with columns
drawn mid-span still ends up as one connected component, since the
column's own outline edges connect to the beam's).

Beam-label assignment
----------------------
Each connected outline component gets the nearest beam label whose
text sits within LABEL_X_MARGIN of the component's x-range and within
LABEL_MAX_Y_GAP below (or above) it — same "label sits just outside its
own outline" convention already confirmed on the PDF path, since this
is the same drafting software.

Public surface
--------------
    BeamZone (dataclass)
    detect_beam_zones(dwg) -> list[BeamZone]
"""

from __future__ import annotations

from dataclasses import dataclass, field

from parser.dwg.dxf_reader import DwgDrawing, RawPolyline, TextLabel

SNAP_TOL = 0.5          # shared-endpoint tolerance for union-find clustering
MIN_OUTLINE_WIDTH = 100.0   # excludes stray short edges (annotation ticks etc.)
LABEL_X_MARGIN = 250.0
LABEL_MAX_Y_GAP = 3000.0


@dataclass
class BeamZone:
    beam_id:    str
    beam_label: str          # full label incl. section, e.g. "FBM 1 (200x600mm)"
    x_left:     float
    x_right:    float
    y_top:      float
    y_bot:      float

    def contains_y(self, y: float, margin: float = 0.0) -> bool:
        return (self.y_top - margin) <= y <= (self.y_bot + margin)

    def contains_x(self, x: float, margin: float = 0.0) -> bool:
        return (self.x_left - margin) <= x <= (self.x_right + margin)


def detect_beam_zones(dwg: DwgDrawing) -> list[BeamZone]:
    """
    One zone per beam LABEL, not per outline component — deliberately
    mirrors the PDF path's own per-anchor search (outline_detector.py's
    _assign_labels_to_outlines) rather than assuming a 1:1 label:outline
    relationship. Confirmed necessary: Serenity_beams.dxf has at least
    one case (FBM 24 / FBM 25) where two distinct beam labels share one
    single continuous drawn rectangle — the same "duplicate label"
    convention already confirmed on the PDF path's own MBM 05 case.
    Looping per component and marking it "used" after the first label
    claim (the original version of this function) silently dropped the
    second label's zone entirely. Looping per label instead, without
    exclusivity, means both labels get a zone — sharing the same
    bounding box when their outline is genuinely shared — so bar/label
    matching downstream (which disambiguates by each occurrence's own
    text position, not by box exclusivity) still has real geometry to
    search against.
    """
    components = _cluster_edges(dwg.beam_outline_edges)
    boxes = []
    for comp in components:
        xs = [p.x for edge in comp for p in edge.points]
        ys = [p.y for edge in comp for p in edge.points]
        width = max(xs) - min(xs)
        if width < MIN_OUTLINE_WIDTH:
            continue
        boxes.append({"x_left": min(xs), "x_right": max(xs), "y_top": min(ys), "y_bot": max(ys)})

    zones: list[BeamZone] = []
    for label in dwg.beam_labels:
        box = _nearest_box(label, boxes)
        if box is None:
            continue
        beam_id = _extract_beam_id(label.text)
        zones.append(BeamZone(
            beam_id=beam_id, beam_label=label.text,
            x_left=box["x_left"], x_right=box["x_right"],
            y_top=box["y_top"], y_bot=box["y_bot"],
        ))
    return zones


def _cluster_edges(edges: list[RawPolyline]) -> list[list[RawPolyline]]:
    n = len(edges)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # index endpoints for proximity lookup
    endpoints = []  # (x, y, edge_idx)
    for i, e in enumerate(edges):
        endpoints.append((e.points[0].x, e.points[0].y, i))
        endpoints.append((e.points[-1].x, e.points[-1].y, i))

    for a in range(len(endpoints)):
        ax, ay, ai = endpoints[a]
        for b in range(a + 1, len(endpoints)):
            bx, by, bi = endpoints[b]
            if ai == bi:
                continue
            if abs(ax - bx) <= SNAP_TOL and abs(ay - by) <= SNAP_TOL:
                union(ai, bi)

    groups: dict[int, list[RawPolyline]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(edges[i])
    return list(groups.values())


def _nearest_box(label: TextLabel, boxes: list[dict]) -> dict | None:
    best, best_dist = None, LABEL_MAX_Y_GAP
    for box in boxes:
        if not (box["x_left"] - LABEL_X_MARGIN <= label.x <= box["x_right"] + LABEL_X_MARGIN):
            continue
        gap = min(abs(label.y - box["y_top"]), abs(label.y - box["y_bot"]))
        if gap < best_dist:
            best, best_dist = box, gap
    return best


def _extract_beam_id(label_text: str) -> str:
    """'FBM 1 (200x600mm)' -> 'FBM 1'. Falls back to the full text if no
    trailing '(...)' section suffix is present."""
    paren = label_text.find("(")
    return label_text[:paren].strip() if paren != -1 else label_text.strip()
