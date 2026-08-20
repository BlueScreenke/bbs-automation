"""
parser/geometry/endpoint_classifier.py

Step 6C — exposes each matched bar's hook/lap/straight classification
for downstream use (Step 6D shape resolution, length calculation).

Radically simplified this session
------------------------------------
Every previous version of this module re-derived, per matched callout,
whether a bar's end was a hook, a lap, a curtailment, or a plain
straight end — independently searching the same geometry bar_detector.py
had already looked at once during PhysicalBar assembly. That
duplication is gone: bar_detector.py now resolves both ends of every
PhysicalBar during assembly (hook / lap / straight, with lap length
already resolved from a drawn dimension where one exists), and
bar_matcher.py hands this module a MatchedBar carrying the complete
PhysicalBar, not a raw fragment. This module's only remaining job is:

    1. Expose that already-resolved classification in the shape Step 6D
       and length_calculator.py expect (ClassifiedBar / EndClassification).
    2. Apply the diameter-based 50 x diameter lap-length default, for
       lap ends where bar_detector found no drawn dimension. This is
       the one piece of real work left here, and it belongs here rather
       than in bar_detector.py deliberately: bar_detector.py is
       callout-agnostic (it has no idea what diameter a bar is, only
       where it is), so the default can only be resolved once a callout
       with a known diameter has been matched to that end.
    3. Handle bars with no PhysicalBar at all (a bar_matcher matching
       failure) as "unclassified" — a genuine data problem, kept
       distinct from a real end classification.

Scope, per project decision (unchanged)
------------------------------------------
All hooks are assumed 90 degrees. Hook *length* never comes from
geometry — always from length_calculator.py's diameter-based BS8666
allowance table. Cutting-length arithmetic stays entirely in
length_calculator.py; this module (and bar_detector.py before it) only
classifies.

Public surface
--------------
    classify_bar_ends(matched_bar) -> ClassifiedBar
    classify_all(matched_bars) -> list[ClassifiedBar]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from parser.geometry.bar_detector import BarEnd, PhysicalBar
from parser.geometry.bar_matcher import MatchedBar

# Drawing's own default (General Note 14: "Minimum laps to all bars to
# be 50 phi unless otherwise stated"). Kept in sync with, but not
# imported from, length_calculator.LAP_FACTOR — this module only needs
# the constant to fill in a lap length; the arithmetic itself stays in
# length_calculator.py per the project's module-boundary decision.
DEFAULT_LAP_FACTOR = 50


# ── Public result types ───────────────────────────────────────────────────────

@dataclass
class EndClassification:
    """Classification of one end of one matched bar, ready for Step 6D / length_calculator."""
    kind:       str                # "hook" | "lap" | "straight" | "unclassified"
    lap_length: Optional[float] = None
    lap_source: Optional[str]   = None   # "drawn" | "assumed_50d" (lap ends only)
    detail:     Optional[str]   = None

    @property
    def is_hook(self) -> bool:
        return self.kind == "hook"

    @property
    def is_lap(self) -> bool:
        return self.kind == "lap"


@dataclass
class ClassifiedBar:
    """Both-ends classification for one matched bar occurrence."""
    matched_bar: MatchedBar
    left_end:    EndClassification
    right_end:   EndClassification

    @property
    def hook_count(self) -> int:
        return sum(1 for e in (self.left_end, self.right_end) if e.is_hook)

    @property
    def is_lapped(self) -> bool:
        return self.left_end.is_lap or self.right_end.is_lap

    def summary(self) -> str:
        return (
            f"ClassifiedBar(mark={self.matched_bar.numeric_mark!r}, "
            f"position={self.matched_bar.position!r}, "
            f"left={self.left_end.kind!r}, right={self.right_end.kind!r}, "
            f"hooks={self.hook_count})"
        )


# ── Public API ────────────────────────────────────────────────────────────────

def classify_all(matched_bars: list[MatchedBar]) -> list[ClassifiedBar]:
    """Classify every matched bar occurrence. See classify_bar_ends."""
    return [classify_bar_ends(mb) for mb in matched_bars]


def classify_bar_ends(matched_bar: MatchedBar) -> ClassifiedBar:
    """
    Expose a matched bar's already-resolved end classification
    (bar_detector.PhysicalBar.left_end / right_end), filling in the
    50 x diameter lap default where bar_detector found no drawn
    dimension for a lap end.
    """
    pb = matched_bar.physical_bar
    if pb is None:
        unmatched = EndClassification("unclassified", detail="bar has no matched PhysicalBar")
        return ClassifiedBar(matched_bar, unmatched, unmatched)

    left  = _to_end_classification(pb.left_end, matched_bar.diameter)
    right = _to_end_classification(pb.right_end, matched_bar.diameter)
    return ClassifiedBar(matched_bar, left, right)


# ── Internal ──────────────────────────────────────────────────────────────────

def _to_end_classification(end: BarEnd, diameter: Optional[int]) -> EndClassification:
    if not end.is_lap:
        return EndClassification(kind=end.kind, detail=end.detail)

    if end.lap_length is not None:
        return EndClassification(kind="lap", lap_length=end.lap_length, lap_source="drawn", detail=end.detail)

    # bar_detector found a genuine lap junction but no drawn dimension
    # bracketing it — apply the drawing's own default now that a
    # diameter is known (bar_detector itself is callout-agnostic and
    # deliberately leaves this to whichever module first has a
    # diameter to apply it with).
    if diameter is not None:
        return EndClassification(
            kind="lap", lap_length=DEFAULT_LAP_FACTOR * diameter, lap_source="assumed_50d",
            detail=(end.detail or "") + " — 50 x diameter default applied",
        )

    # Lap confirmed but no diameter known (bar has no matched callout
    # diameter) — cannot resolve a length yet. Surface as-is rather
    # than guess a diameter.
    return EndClassification(kind="lap", lap_length=None, lap_source=None, detail=end.detail)


# ── CLI smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import re

    import pdfplumber

    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes
    from parser.geometry.bar_detector import detect_bars
    from parser.geometry.bar_matcher import match_bars_to_geometry
    from parser.pdf.dimension_extractor import extract_dimension_matches
    from parser.pdf.models import ParsedBarData

    path = sys.argv[1] if len(sys.argv) > 1 else "input/sample.pdf"
    target_beam = sys.argv[2] if len(sys.argv) > 2 else "MBM 01"

    with pdfplumber.open(path) as pdf:
        words = pdf.pages[0].extract_words()
        page_w, page_h = pdf.pages[0].width, pdf.pages[0].height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)
    dim_matches = extract_dimension_matches(path)
    geometries = {g.beam_id: g for g in detect_bars(prims, boxes, dim_matches)}

    if target_beam not in geometries:
        print(f"Beam {target_beam!r} not found.")
        sys.exit(1)

    box = geometries[target_beam].box
    pattern = re.compile(r'^(\d+)T(\d+)-(\d+)(?:\((\w+)\))?$')
    test_bars = []
    for w in words:
        if not (box['x_left'] <= w['x0'] <= box['x_right'] and box['y_top'] <= w['top'] <= box['y_bot']):
            continue
        m = pattern.fullmatch(w['text'])
        if not m:
            continue
        qty, dia, mark, pos = m.groups()
        test_bars.append(ParsedBarData(
            source="PDF", raw_text=w['text'], diameter=int(dia), length=None,
            spacing=None, quantity=int(qty), numeric_mark=mark, position=pos,
            confidence=1.0, page=1, beam_id=target_beam, beam_label=target_beam,
            x0=w['x0'], top=w['top'],
        ))

    matched = match_bars_to_geometry(test_bars, geometries[target_beam], prims)
    classified = classify_all(matched)

    print(f"{target_beam}: {len(classified)} bars classified")
    for c in classified:
        print(" ", c.summary())
        print("    left :", c.left_end)
        print("    right:", c.right_end)