# DWG pipeline — dimension-line usage fixed, and ported to PDF

## Wiring
Replace `parser/dwg/` with this folder's contents, `main_dwg.py` stays
as-is. Apply BOTH patches to `parser/pdf/`:

    git apply length_calculator.py.patch
    git apply patterns.py.patch

(If you already applied an earlier round's `patterns.py.patch`, this
one is the same content again — safe to re-apply or skip.)

## What was verified on the DWG side (your ask this round)

Confirmed with real numbers before touching anything further: with the
tolerances `length_calculator.py` already had (calibrated in PDF
*points*), only 47 of 507 main bars on Serenity_beams.dxf got a full
dimension-chain match — 320 fell to scale with no dimension found at
all, and for shape-21 (double-hooked) bars specifically, 51 of 52 used
scale only. Root cause: those tolerances are real distances (15/30
units), and DXF coordinates are already real millimetres, so reusing
them unchanged made the search far tighter than intended — PDF's own
effective real-world tolerance is roughly 270-600mm once its point-to-mm
scale is accounted for, not 15-30mm.

Made the tolerances configurable (`boundary_match_tol`/`chain_gap_tol`
parameters, defaulting to the PDF path's exact original values — zero
risk there) and had the DWG path pass real-mm-equivalent values. Result:
full dimension-chain matches went 47 → 183 overall, and for shape-21
bars specifically, 0 → 46 of 52. The remaining shape-21 bars are
covered by the "whole span" fallback from the round before. The 16
single-hook bars still without a dimension match were checked
individually (not just counted) — they're short support bars running
part-way into a span with no dimension bracketing their own cut point
anywhere on the drawing; their geometry-derived length is the right
call there, not a gap.

## A second bug this surfaced, also fixed and ported

While verifying shape-21 specifically, found that two *different* bars
genuinely sharing one dimension chain (e.g. a T1 and a B1 bar, both
hooked both ends, both running the same full span) — confirmed on
Serenity's FBM 19 marks 32/33 — only let the FIRST one claim the chain;
the second silently fell back to a worse, shorter value. This existed
on the PDF side too and was invisible there. Fixed with a narrow,
additive rule: after the existing matching finishes exactly as before,
a bar still unmatched may claim an *already-claimed* multi-dimension
chain only if its own geometry independently and fully matches that
exact chain — this cannot change any bar's original result, only let a
second bar reuse a chain that, by its own geometry, was never really
exclusive to the first.

**Verified this is safe on Beams_bondo.pdf with a full bar-by-bar diff,
not just totals**: 1045 parsed / 7 flagged unchanged, and only 12 bars
changed value, every one checked individually — each now matches
another bar in the same beam that already had the correct value (e.g.
`1BM 12` mark 41 now matches mark 43's `9553.5`; the same `9553.5`
independently shows up in `2BM 12` and `MBM 12` too, the same floor
plan repeated three times, which is a strong cross-check that the value
itself is right). The one precedent case this protection exists for
(`MBM 04` marks 19/20) was re-checked and is untouched: 2700.0 and
8198.3, exactly as before.

## Numbers
- Beams_bondo.pdf: 1045 parsed, 7 flagged — unchanged in count, 12 bar
  lengths corrected (all verified individually, see above)
- Serenity_beams.dxf: 714 parsed, 712 in the BBS (counts unchanged from
  last round — this round fixed values, not coverage)
