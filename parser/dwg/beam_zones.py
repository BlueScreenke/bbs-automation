"""
parser/dwg/beam_zones.py

Step D2 — decides which beam every bar, label and dimension belongs to.

Why this was rewritten (Session 6, round 3)
--------------------------------------------
The previous version built each beam's zone from "Beam Line(D)" outline
rectangles and then let every zone CLAIM whatever labels/bars fell within
generous margins. Confirmed against Serenity_beams.dxf, that produced
cross-beam contamination:
  - The outline rectangles are not a reliable description of the beam
    band. FBM 6's real 600mm band is the GAP between two outline
    rectangles (y 108029..108629), not either rectangle itself; the old
    code took one rectangle and compensated with big y-margins.
  - Merging same-level outline pieces also swallowed a neighbour's small
    A-A section box (FBM 9's, x -30551..-30351) into FBM 15's zone.
  - Wide capture margins let FBM 9 claim FBM 6's legend labels and FBM
    15 claim FBM 9's, because each beam's cross-section legend sits
    ~800-1500 units RIGHT of its own elevation — about as far as the
    next beam's left edge.

New approach: anchor on the bar polylines
------------------------------------------
Only elevation bars are LWPOLYLINEs on the bar-line layers (section-view
bars are CIRCLEs, ignored by dxf_reader). So bar polylines that touch or
nearly touch (column gaps are ~600) form one beam's elevation assembly.
Each beam label is attached to the assembly whose x-range contains it,
nearest vertically. Then:
  - bars belong to their assembly by construction (exactly one owner);
  - every label and dimension is assigned to exactly ONE assembly by a
    direction-aware cost (a cross-section legend sits to the RIGHT of
    its own beam, so a label right of a beam is never claimed by the
    beam on its right);
  - two labels attached to the same assembly (a rectangle shared by two
    beam IDs, e.g. FBM 24 / FBM 25) each get a zone over the same
    geometry; an owned item goes to whichever of them has the nearer
    beam-label text.

Public surface
--------------
    BeamZone (dataclass)
    detect_beam_zones(dwg) -> list[BeamZone]      (also assigns ownership)
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from parser.dwg.dxf_reader import DwgDrawing, RawDimension, RawPolyline, TextLabel

# Bars of one beam elevation: expanded bounding boxes that overlap join.
# Column gaps are ~600; distinct beams in a row are >= ~2400 apart.
CLUSTER_X_TOL = 700.0
CLUSTER_Y_TOL = 200.0

# A beam label sits just outside its own elevation, below by convention.
LABEL_X_MARGIN = 500.0
LABEL_MAX_Y_GAP = 3000.0

# Ownership of callout labels (bar / link labels).
LEGEND_MAX_DX = 2200.0      # how far right of its beam a legend label may sit
LEFT_OF_BEAM_TOL = 700.0    # callouts occasionally sit a little left of their own beam
LABEL_MAX_DY = 1200.0       # vertical distance from the assembly's bar range
LEFT_PENALTY = 2.0          # left-of-beam distance is weighted more heavily than right

# Ownership of native dimensions.
DIM_MAX_DY = 1200.0
DIM_X_TOL = 150.0

# Padding of the zone's y-range around the bars (leader shaft "inside/
# outside the beam" test uses it).
ZONE_Y_PAD = 80.0


@dataclass
class BeamZone:
    beam_id:    str
    beam_label: str
    x_left:     float
    x_right:    float
    y_top:      float
    y_bot:      float
    label_x:    float = 0.0
    label_y:    float = 0.0
    cluster_id: int = -1
    label_source: str = "beam_label"   # "beam_label" | "section_marker" (no beam label in the drawing)

    # Everything this beam owns — filled by detect_beam_zones().
    bars_top:          list[RawPolyline]   = field(default_factory=list)
    bars_bottom:       list[RawPolyline]   = field(default_factory=list)
    bar_labels_top:    list[TextLabel]     = field(default_factory=list)
    bar_labels_bottom: list[TextLabel]     = field(default_factory=list)
    link_labels:       list[TextLabel]     = field(default_factory=list)
    dimensions:        list[RawDimension]  = field(default_factory=list)

    def contains_y(self, y: float, margin: float = 0.0) -> bool:
        return (self.y_top - margin) <= y <= (self.y_bot + margin)

    def contains_x(self, x: float, margin: float = 0.0) -> bool:
        return (self.x_left - margin) <= x <= (self.x_right + margin)


@dataclass
class _Cluster:
    cid:     int
    x_left:  float
    x_right: float
    y_top:   float
    y_bot:   float
    tops:    list[RawPolyline] = field(default_factory=list)
    bottoms: list[RawPolyline] = field(default_factory=list)


def detect_beam_zones(dwg: DwgDrawing) -> list[BeamZone]:
    clusters = _cluster_bars(dwg)
    labels_by_cluster = _labels_by_cluster(dwg.beam_labels, clusters)

    zones: list[BeamZone] = []
    for cl in clusters:
        labels = labels_by_cluster.get(cl.cid)
        if not labels:
            continue
        beam_id, beam_label = _representative_label(labels)
        zones.append(BeamZone(
            beam_id=beam_id, beam_label=beam_label,
            x_left=cl.x_left, x_right=cl.x_right,
            y_top=cl.y_top - ZONE_Y_PAD, y_bot=cl.y_bot + ZONE_Y_PAD,
            label_x=sum(l.x for l in labels) / len(labels),
            label_y=sum(l.y for l in labels) / len(labels),
            cluster_id=cl.cid,
            bars_top=list(cl.tops), bars_bottom=list(cl.bottoms),
        ))

    zones.extend(_zones_from_section_markers(dwg, clusters, {z.cluster_id for z in zones}))

    _assign_labels(dwg.bar_labels_top,    zones, clusters, "bar_labels_top")
    _assign_labels(dwg.bar_labels_bottom, zones, clusters, "bar_labels_bottom")
    _assign_labels(dwg.link_labels,       zones, clusters, "link_labels")
    _assign_dimensions(dwg.dimensions, zones, clusters)
    return zones


# A beam whose own "ID (WxD mm)" label is missing from the drawing can
# still be named from its cross-section marker, "(FBM 5) A". Confirmed on
# Serenity_beams: FBM 5 and TBM 110 have no beam label in either the DXF
# or the PDF. Their width/depth is then unknown (only the label carries
# it), so stirrup lengths for these beams cannot be computed.
_SECTION_MARKER_RE = re.compile(r"^\(([A-Za-z0-9]+ ?\d+)\)\s*A$")
SECTION_MARKER_MARGIN = 1500.0


def _zones_from_section_markers(dwg: DwgDrawing, clusters: list["_Cluster"], used: set[int]) -> list[BeamZone]:
    out: list[BeamZone] = []
    for cl in clusters:
        if cl.cid in used:
            continue
        ids = Counter(
            m.group(1)
            for t in dwg.section_labels
            if (m := _SECTION_MARKER_RE.match(t.text.strip()))
            and cl.x_left - SECTION_MARKER_MARGIN <= t.x <= cl.x_right + SECTION_MARKER_MARGIN
            and cl.y_top - SECTION_MARKER_MARGIN <= t.y <= cl.y_bot + SECTION_MARKER_MARGIN
        )
        if not ids:
            continue
        beam_id = ids.most_common(1)[0][0]
        out.append(BeamZone(
            beam_id=beam_id, beam_label=beam_id,
            x_left=cl.x_left, x_right=cl.x_right,
            y_top=cl.y_top - ZONE_Y_PAD, y_bot=cl.y_bot + ZONE_Y_PAD,
            label_x=(cl.x_left + cl.x_right) / 2, label_y=(cl.y_top + cl.y_bot) / 2,
            cluster_id=cl.cid, label_source="section_marker",
            bars_top=list(cl.tops), bars_bottom=list(cl.bottoms),
        ))
    return out


# ── Clustering ────────────────────────────────────────────────────────────────

def _cluster_bars(dwg: DwgDrawing) -> list[_Cluster]:
    items = [(p, "top") for p in dwg.bar_lines_top] + [(p, "bottom") for p in dwg.bar_lines_bottom]
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    boxes = [(p.x_left - CLUSTER_X_TOL, p.x_right + CLUSTER_X_TOL,
              p.y_top - CLUSTER_Y_TOL, p.y_bot + CLUSTER_Y_TOL) for p, _ in items]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = boxes[i], boxes[j]
            if a[0] <= b[1] and b[0] <= a[1] and a[2] <= b[3] and b[2] <= a[3]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    clusters: list[_Cluster] = []
    for cid, idxs in enumerate(groups.values()):
        polys = [items[i][0] for i in idxs]
        cl = _Cluster(
            cid=cid,
            x_left=min(p.x_left for p in polys), x_right=max(p.x_right for p in polys),
            y_top=min(p.y_top for p in polys),   y_bot=max(p.y_bot for p in polys),
        )
        for i in idxs:
            (cl.tops if items[i][1] == "top" else cl.bottoms).append(items[i][0])
        clusters.append(cl)
    return clusters


def _labels_by_cluster(labels: list[TextLabel], clusters: list["_Cluster"]) -> dict[int, list[TextLabel]]:
    """Every beam label that names a given assembly — usually one, but
    a drawing can span-label the same continuous elevation with several
    distinct beam IDs (confirmed real case: Serenity_beams.dxf's
    "FBM 24"/"FBM 25" and "TBM 106".."TBM 109"). Per Brian's own
    explicit domain guidance: this is normal drafting-software output
    that's ordinarily hand-corrected on site by renaming every such
    label to one representative name for the whole beam — so rather
    than trying to split which bar belongs to which of several close-
    together labels (unreliable — confirmed a mark can sit closer to
    the WRONG one of two labels), every label naming the same assembly
    is merged into one zone up front. No split is attempted."""
    out: dict[int, list[TextLabel]] = {}
    for label in labels:
        cl = _cluster_for_label(label, clusters)
        if cl is not None:
            out.setdefault(cl.cid, []).append(label)
    return out


def _representative_label(labels: list[TextLabel]) -> tuple[str, str]:
    """Picks one label to stand in for every beam ID sharing an
    assembly — the lowest beam number, matching the on-site convention
    Brian described (rename the group to one representative name)."""
    def sort_key(l: TextLabel) -> tuple:
        m = re.match(r"^([A-Za-z0-9]+?)\s*(\d+)", l.text)
        return (m.group(1), int(m.group(2))) if m else (l.text, 0)
    chosen = min(labels, key=sort_key)
    return _extract_beam_id(chosen.text), chosen.text


def _cluster_for_label(label: TextLabel, clusters: list[_Cluster]) -> Optional[_Cluster]:
    best, best_gap = None, LABEL_MAX_Y_GAP
    for cl in clusters:
        if not (cl.x_left - LABEL_X_MARGIN <= label.x <= cl.x_right + LABEL_X_MARGIN):
            continue
        gap = 0.0 if cl.y_top <= label.y <= cl.y_bot else min(abs(label.y - cl.y_top), abs(label.y - cl.y_bot))
        if gap < best_gap:
            best, best_gap = cl, gap
    return best


# ── Exclusive ownership ───────────────────────────────────────────────────────

def _label_cost(x: float, y: float, cl: _Cluster) -> Optional[float]:
    """Direction-aware distance from a callout label to an assembly, or
    None if it cannot belong to it. A cross-section legend sits to the
    RIGHT of its own beam, so right-of-beam distance is cheap, left-of-
    beam is heavily penalised and capped."""
    if x > cl.x_right:
        dx = x - cl.x_right
        if dx > LEGEND_MAX_DX:
            return None
    elif x < cl.x_left:
        dx = cl.x_left - x
        if dx > LEFT_OF_BEAM_TOL:
            return None
        dx *= LEFT_PENALTY
    else:
        dx = 0.0
    dy = 0.0 if cl.y_top <= y <= cl.y_bot else min(abs(y - cl.y_top), abs(y - cl.y_bot))
    if dy > LABEL_MAX_DY:
        return None
    return dx + dy


def _pick_zone_in_cluster(zones: list[BeamZone], x: float, y: float) -> BeamZone:
    """Several zones can share one assembly (two beam IDs on one drawn
    rectangle); the item goes to the one whose own beam-label text is
    nearest."""
    return min(zones, key=lambda z: (z.label_x - x) ** 2 + (z.label_y - y) ** 2)


def _assign_labels(labels: list[TextLabel], zones: list[BeamZone], clusters: list[_Cluster], attr: str) -> None:
    by_cluster: dict[int, list[BeamZone]] = {}
    for z in zones:
        by_cluster.setdefault(z.cluster_id, []).append(z)
    cl_by_id = {c.cid: c for c in clusters if c.cid in by_cluster}

    for lbl in labels:
        best_cid, best_cost = None, None
        for cid, cl in cl_by_id.items():
            cost = _label_cost(lbl.x, lbl.y, cl)
            if cost is not None and (best_cost is None or cost < best_cost):
                best_cid, best_cost = cid, cost
        if best_cid is None:
            continue
        zone = _pick_zone_in_cluster(by_cluster[best_cid], lbl.x, lbl.y)
        getattr(zone, attr).append(lbl)


def _assign_dimensions(dims: list[RawDimension], zones: list[BeamZone], clusters: list[_Cluster]) -> None:
    by_cluster: dict[int, list[BeamZone]] = {}
    for z in zones:
        by_cluster.setdefault(z.cluster_id, []).append(z)

    for d in dims:
        mx = (d.x_left + d.x_right) / 2
        best_cid, best_dy = None, DIM_MAX_DY
        for cl in clusters:
            if cl.cid not in by_cluster:
                continue
            if not (cl.x_left - DIM_X_TOL <= mx <= cl.x_right + DIM_X_TOL):
                continue
            dy = 0.0 if cl.y_top <= d.y <= cl.y_bot else min(abs(d.y - cl.y_top), abs(d.y - cl.y_bot))
            if dy < best_dy:
                best_cid, best_dy = cl.cid, dy
        if best_cid is None:
            continue
        for z in by_cluster[best_cid]:
            z.dimensions.append(d)   # shared assembly -> shared dimensions


def _extract_beam_id(label_text: str) -> str:
    """'FBM 1 (200x600mm)' -> 'FBM 1'."""
    paren = label_text.find("(")
    return label_text[:paren].strip() if paren != -1 else label_text.strip()
