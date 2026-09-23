"""
parser/dwg/layer_roles.py

Classifies a DWG/DXF layer name into a semantic role by keyword, not by
an exact-name lookup table.

Why keyword-based, not a hardcoded list
----------------------------------------
The layer names confirmed on Serenity_beams.dxf ("Rebar Line", "Top
Rebar Line", "Rebar Kink", "Beam Line(D)", ...) exactly match the ones
already referenced in parser/geometry/bar_detector.py's own docstring
for the PDF path, confirming this DWG and those PDFs share one
drafting-software family. But per explicit project decision, not every
DWG this pipeline will ever see comes from that same software — a
different consultant's template could use "REBAR_ELEV", "RC-MAIN-BOT",
etc. A keyword rule set generalizes across naming conventions the exact
list above never anticipated; an unrecognised layer simply classifies
as UNKNOWN rather than crashing or silently mis-filing.

Role precedence
----------------
Rules are checked in order; the first match wins. More specific rules
(TOP_REBAR_*) are listed before their more general counterparts
(REBAR_*) so "Top Rebar Line" classifies as bar-top, not bar-bottom.

Public surface
--------------
    LayerRole (enum-like str constants)
    classify_layer(layer_name: str) -> str
"""

from __future__ import annotations


class LayerRole:
    BAR_LINE_TOP     = "bar_line_top"
    BAR_LINE_BOTTOM  = "bar_line_bottom"
    BAR_LABEL_TOP    = "bar_label_top"
    BAR_LABEL_BOTTOM = "bar_label_bottom"
    LINK_LINE        = "link_line"
    LINK_LABEL       = "link_label"
    KINK             = "kink"
    BEAM_OUTLINE     = "beam_outline"
    BEAM_LABEL       = "beam_label"
    DIMENSION_LINE   = "dimension_line"
    DIMENSION_TEXT   = "dimension_text"
    LEADER           = "leader"
    SECTION          = "section"          # section-view detail — excluded from elevation geometry
    NON_REBAR_OUTLINE = "non_rebar_outline"  # e.g. "Front Elevation Line" — outline duplicate, exclude
    UNKNOWN          = "unknown"


# Ordered (keyword-set, role) rules. All keywords in a rule must appear
# (case-insensitively) in the layer name. Checked top to bottom —
# first match wins, so more specific combinations are listed first.
_RULES: list[tuple[tuple[str, ...], str]] = [
    (("top", "rebar", "label"),   LayerRole.BAR_LABEL_TOP),
    (("top", "rebar", "line"),    LayerRole.BAR_LINE_TOP),
    (("rebar", "kink"),           LayerRole.KINK),
    (("rebar", "label"),          LayerRole.BAR_LABEL_BOTTOM),
    (("rebar", "line"),           LayerRole.BAR_LINE_BOTTOM),
    (("link", "label"),           LayerRole.LINK_LABEL),
    (("link", "line"),            LayerRole.LINK_LINE),
    (("beam", "label"),           LayerRole.BEAM_LABEL),
    (("beam", "line"),            LayerRole.BEAM_OUTLINE),
    (("front", "elevation"),      LayerRole.NON_REBAR_OUTLINE),
    (("dimension", "text"),       LayerRole.DIMENSION_TEXT),
    (("dimension",),              LayerRole.DIMENSION_LINE),
    (("leader",),                 LayerRole.LEADER),
    (("section",),                LayerRole.SECTION),
]


def classify_layer(layer_name: str) -> str:
    """
    Return the LayerRole for a given DWG layer name. Unrecognised
    layers (survey/road/topo layers, title-block layers, "0",
    "Defpoints", etc.) return LayerRole.UNKNOWN — callers should ignore
    UNKNOWN entities rather than guess at their purpose.
    """
    name = layer_name.lower()
    for keywords, role in _RULES:
        if all(kw in name for kw in keywords):
            return role
    return LayerRole.UNKNOWN
