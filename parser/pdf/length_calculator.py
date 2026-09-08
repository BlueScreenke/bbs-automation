"""
parser/pdf/length_calculator.py

Step 6D (part 1) — turns real geometry into millimetre numbers.

Scope, per explicit project decision (this session)
--------------------------------------------------------
This module is the ONLY place in the pipeline that turns geometry into
a length in millimetres. It knows nothing about BS8666 shape codes —
that decision (and the A/B/C/D/E layout) belongs entirely to
shape_resolver.py, which consumes this module's output rather than
re-deriving any of it. The two modules must not duplicate each other's
job: this one measures, shape_resolver.py arranges.

Rewritten this session — dimension-first, per-beam scale
------------------------------------------------------------
Confirmed against Page_1_beams.pdf (MBM 01 marks 4/5): the previous
version's pure geometry×page-wide-scale approach double-counted length.
A hooked bar's drawn line extends a little past the true face into
where the hook mark begins (confirmed ~9-10.6pt on this drawing), and
subtracting a flat 25mm cover never corrected for that — the drawing's
OWN dimension (2696.5mm for that span) is the ground truth, and hook
length is added on top of it (in Excel), not doubly baked into a
geometry-derived figure that already reaches past the true face.

New approach, in priority order, per bar:
  1. Dimension chain (preferred). Walk this beam's own drawn dimensions
     (main spans AND curtailment/support-bar dims, treated uniformly —
     see _find_dimension_coverage) and try to reconstruct the bar's own
     physical span exactly from one or more of them, anchored from
     whichever end lines up with a real dimension boundary. Confirmed
     against three real, independent cases on this drawing:
       - MBM 01 marks 4/5: single dimension (2696.5) matches the bar's
         geometry directly (both ends within ~9-10.6pt — the hook
         mark's own overshoot).
       - MBM 04 mark 19 (a SUPPORT bar — see below): dimensions 1330 +
         1370 chain to a combined 2700mm, matching the bar's own
         geometric span within ~1.7-1.8pt at both ends.
       - MBM 04 mark 21 (two occurrences, also support bars): 1520+780
         and 780+1520 respectively, each matching within ~1.7pt.
     No cover deduction is applied to a dimension-derived length — the
     drawn dimension already IS the intended straight length (confirmed
     directly: "for MBM 01, the 2696.5 and the hook lengths are used,
     no cover deductions").
  2. Partial dimension + local-scale remainder. Some main (non-support)
     bars run from a dimensioned face (e.g. a hook at a support, main
     span dimension available) into a lap splice with no drawn
     dimension at all (confirmed: MBM 04 mark 20 laps with mark 23
     partway through the second span, marked only by small diagonal
     tick marks — no bracketing dimension). The dimensioned portion is
     used as-is; only the un-dimensioned remainder falls back to this
     beam's own local scale (see derive_local_scale_mm_per_pt) — never
     the whole bar, so the well-known portion stays exact.
  3. Full local-scale fallback. Only when NEITHER end of the bar lines
     up with any drawn dimension at all.
No cover deduction is applied in any of the three cases — confirmed
this is a page-wide rule, not specific to the single-span case.

Support bars vs main bars (domain context, not itself used to route
the calculation — the dimension-chain search above handles both
uniformly by geometry alone)
------------------------------------------------------------------------
Confirmed (project domain knowledge): a beam's reinforcement includes
both MAIN bars (run the long span, dimensioned end-to-end or via a
grid-to-grid chain) and SUPPORT bars (short, sit at a column/support to
resist negative moment, dimensioned as two short distances either side
of the support — e.g. MBM 04 mark 19's "1330 + 1370"). The two are not
distinguished by shape or diameter, only by how they're dimensioned and
where they sit — which is exactly why the dimension-chain search
doesn't need to know which kind of bar it's looking at.

Local, per-beam scale — not a single page-wide value
----------------------------------------------------------
Confirmed necessary: some beams' own drawn dimensions imply measurably
different mm-per-pt scale than the page-wide median (MBM 01's own
2696.5 dimension implies ~19.85mm/pt vs a page-wide median of
~18.35mm/pt — an 8% difference, large enough to matter for the
geometry-fallback case). derive_local_scale_mm_per_pt() computes this
from ONLY the current beam's own zone dimensions; the page-wide
derive_scale_mm_per_pt() is kept solely as a last-resort fallback for
the rare beam with no dimensions in its own zone at all.

Stirrups (unchanged)
------------------------
Computed from beam cross-section (width/depth) and a fixed 100mm tail
on each leg — per project decision, NOT from diameter, and NOT from
PhysicalBar geometry:
    A (width leg)  = 2 x (beam_width - 2 x cover)
    B (depth leg)  = 2 x (beam_depth - 2 x cover)
    C, D (tails)   = 100mm each, fixed, regardless of diameter
Confirmed against the reference workbook's own worked example (a
200x600mm beam, 25mm cover): A = 2x(200-50) = 300, B = 2x(600-50) =
1100, matching the sheet's own A=300, B=1100 for that beam exactly.
Stirrup cover deduction is unrelated to the main-bar change above (a
stirrup's own legs are always measured face-to-face of the concrete
cover, a separate, unambiguous convention) and is left as-is.

Public surface
--------------
    calculate_bar_lengths(classified_bars, beam_label, zone_dims, fallback_scale) -> list[BarLengthResult]
    derive_scale_mm_per_pt(dimension_matches) -> Optional[float]
    derive_local_scale_mm_per_pt(zone_dims) -> Optional[float]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from parser.geometry.endpoint_classifier import ClassifiedBar


# ── Constants ─────────────────────────────────────────────────────────────────

COVER_BEAM_MM = 25          # stirrup legs only now — see module docstring

STIRRUP_LEG_MM = 100        # fixed tail length per leg, regardless of diameter

# Dimension matches below this are excluded from SCALE derivation only
# (both page-wide and per-beam) — a small dimension's drawn pt-width
# carries more relative rounding error than a large one. Does NOT gate
# the dimension-CHAIN search below, which uses every dimension in a
# beam's zone regardless of size (confirmed: MBM 04's support-bar
# dimensions, e.g. 780mm, are just as scale-consistent as the main
# spans after this session's dimension_extractor.py fix).
SPAN_DIM_THRESHOLD_MM = 1000

# How close a bar's own physical endpoint must sit to a dimension
# chain's outer boundary to count as "this dimension (or chain of them)
# accounts for this bar". Measured page-wide, this session: a
# non-hooked end (a support bar's plain/curtailed end) lines up within
# ~1.7-2.3pt; a hooked end overshoots by ~9-10.6pt (the hook mark's own
# small drawn extension past the true face). 15pt clears both with
# margin, well short of the ~30pt+ real spacing between unrelated
# dimension boundaries.
BOUNDARY_MATCH_TOL = 15.0

# Maximum gap between two dimensions' facing edges to treat them as
# parts of the same chain (e.g. MBM 04 mark 19's 1330+1370, whose
# facing edges are 3.4pt apart at the shared column). Kept below the
# real spacing to the next, unrelated dimension along the same beam.
CHAIN_GAP_TOL = 30.0

_SECTION_RE = re.compile(r'\((\d+)[xX](\d+)', re.I)


# ── Public dataclass ────────────────────────────────────────────────────────

@dataclass
class BarLengthResult:
    """
    Length figures for one bar occurrence, in millimetres.

    straight_length_mm : main bars only. The bar's known-in-Python
        length portion — for shape 00 (no hooks) this IS the final
        cutting length; for a hooked bar it is NOT (the hook length is
        added later, in Excel — see module docstring). None if this
        bar has no matched PhysicalBar or no length could be resolved
        by either dimension-chain or scale fallback — a genuine data
        gap, not guessed.

    stirrup_leg_width_mm / stirrup_leg_depth_mm : stirrups only. Both
        legs of the rectangular link. Unlike main bars, a stirrup's
        length is fully known in Python (see module docstring) — no
        Excel lookup needed for any of its four dimensions.
    """
    numeric_mark:            Optional[str]
    position:                 Optional[str]
    diameter:                  Optional[int]
    is_stirrup:                  bool
    straight_length_mm:          Optional[float]
    lap_length_total_mm:          float
    lap_end_count:                  int
    stirrup_leg_width_mm:     Optional[float] = None
    stirrup_leg_depth_mm:     Optional[float] = None
    stirrup_tail_mm:                   float = STIRRUP_LEG_MM
    note:                      Optional[str] = None
    raw_text:                           str = ""


# ── Public API ────────────────────────────────────────────────────────────────

def calculate_bar_lengths(
    classified_bars:  list[ClassifiedBar],
    beam_label:        str,
    zone_dims:          list[dict],
    fallback_scale:      Optional[float] = None,
) -> list[BarLengthResult]:
    """
    Calculate length figures for every classified bar occurrence in one
    beam. See module docstring for the main-bar priority order and the
    stirrup formula.

    Two passes over the bars in this beam — not a single independent
    pass per bar — because a single drawn dimension is a one-time
    physical annotation that can only ever belong to ONE bar's own
    reconstruction, never two. Confirmed real case (MBM 04): "1370"
    fully explains mark 19 (paired with "1330", a complete
    covers_full=True match) — but mark 19 and mark 20 are both a
    plausible x-proximity fit for a chain search run independently, and
    an independent per-bar pass wrongly let mark 20's chain also latch
    onto "1370" as if it partially explained mark 20 too, before
    falling to scale for the true (undimensioned) remainder. That
    produced a confidently wrong length for mark 20, not an honest
    scale-only fallback.

    Pass 1 finds every bar whose dimensions fully reconstruct it
    end-to-end (covers_full) and removes those dimensions from the
    shared pool. Pass 2 resolves every remaining bar (partial dimension
    + scale, or full scale fallback) using only what's left — so a
    dimension already spoken for by a confirmed full match is never
    also offered to a different bar's partial match.

    Args:
        classified_bars: One ClassifiedBar per bar occurrence in this
                          beam (from endpoint_classifier.classify_all()),
                          carrying the real PhysicalBar geometry and
                          resolved end classifications this module
                          consumes directly — nothing here is re-derived
                          from raw primitives.
        beam_label:       Full beam label, e.g. "MBM 01 (200x600mm)" —
                          used only for stirrup cross-section dims.
        zone_dims:        This beam's own drawn dimension matches (from
                          dimension_extractor.extract_dimension_matches(),
                          filtered to this beam's own box zone by the
                          caller) — {value, y, x_left, x_right} each.
                          Used both for the dimension-chain search and
                          for this beam's own local scale (see
                          derive_local_scale_mm_per_pt). A single page-
                          wide scale is deliberately NOT used here —
                          confirmed different beams on the same page can
                          imply measurably different mm-per-pt scale.
        fallback_scale:   Last-resort scale (see derive_scale_mm_per_pt,
                          page-wide) for the rare beam with no
                          dimensions in its own zone at all. None if no
                          page-wide scale could be derived either.

    Returns:
        One BarLengthResult per input ClassifiedBar, same order.
    """
    width, depth = _parse_section(beam_label)
    local_scale = derive_local_scale_mm_per_pt(zone_dims)
    scale = local_scale if local_scale is not None else fallback_scale

    main_bar_idxs = [
        i for i, cb in enumerate(classified_bars)
        if cb.matched_bar.source.spacing is None
    ]
    available = list(zone_dims)

    # Pass 1: claim only complete (covers_full) matches, removing their
    # dimensions from the shared pool as they're claimed — but ONLY
    # when the match chained two or more dimensions together. A
    # single-dimension match (e.g. MBM 01's "2696.5") is a shared span
    # dimension: both mark 4 (T1) and mark 5 (B1) legitimately run that
    # same full span and must each be able to match it independently.
    # A multi-dimension chain (e.g. mark 19's "1330"+"1370") is, by
    # contrast, a bar-specific pair of curtailment brackets that will
    # never legitimately belong to a second bar — confirmed real case:
    # leaving "1370" unclaimed after mark 19's chain let mark 20's
    # unrelated partial match wrongly latch onto it too (see docstring).
    full_matches: dict[int, dict] = {}
    for i in main_bar_idxs:
        pb = classified_bars[i].matched_bar.physical_bar
        if pb is None:
            continue
        coverage = _find_dimension_coverage(pb.x_left, pb.x_right, available)
        if coverage is not None and coverage['covers_full']:
            full_matches[i] = coverage
            if len(coverage['used']) > 1:
                claimed_ids = {id(d) for d in coverage['used']}
                available = [d for d in available if id(d) not in claimed_ids]

    # Pass 2: everything else, using only what pass 1 left unclaimed.
    results: list[BarLengthResult] = []
    for i, cb in enumerate(classified_bars):
        if cb.matched_bar.source.spacing is not None:
            results.append(_calculate_stirrup(cb, width, depth))
        elif i in full_matches:
            results.append(_finish_main_bar(cb, full_matches[i], scale))
        else:
            pb = cb.matched_bar.physical_bar
            coverage = _find_dimension_coverage(pb.x_left, pb.x_right, available) if pb is not None else None
            results.append(_calculate_main_bar(cb, coverage, scale))

    return results


def derive_scale_mm_per_pt(dimension_matches: list[dict]) -> Optional[float]:
    """
    Derive a drawing's points-to-millimetres scale from its own drawn
    dimension lines, page-wide. Kept only as a last-resort fallback for
    a beam with no dimensions in its own zone — see
    derive_local_scale_mm_per_pt for the preferred, per-beam version
    every beam with any dimensions at all should use instead.

    Returns None if no qualifying (span-sized) dimension match exists at
    all on the page — callers must treat that as "cannot compute a
    geometry-based length here", never guess a fallback scale.
    """
    return _median_scale(dimension_matches)


def derive_local_scale_mm_per_pt(zone_dims: list[dict]) -> Optional[float]:
    """
    Derive THIS BEAM's own points-to-millimetres scale from only the
    dimensions drawn in its own zone. Confirmed necessary (this
    session): MBM 01's own span dimension implies ~19.85mm/pt against a
    page-wide median of ~18.35mm/pt — an 8% difference large enough to
    matter for the geometry-fallback case, most plausibly because a
    beam's own local drawing area can be positioned/scaled slightly
    differently within the overall sheet layout.

    Returns None if this beam's zone has no qualifying (span-sized)
    dimension at all — callers fall back to the page-wide scale.
    """
    return _median_scale(zone_dims)


def _median_scale(dimension_matches: list[dict]) -> Optional[float]:
    scales: list[float] = []
    for m in dimension_matches:
        width_pt = m['x_right'] - m['x_left']
        if m['value'] >= SPAN_DIM_THRESHOLD_MM and width_pt > 0:
            scales.append(m['value'] / width_pt)
    if not scales:
        return None
    scales.sort()
    return scales[len(scales) // 2]


# ── Main bars ─────────────────────────────────────────────────────────────────

def _calculate_main_bar(cb: ClassifiedBar, coverage: Optional[dict], scale: Optional[float]) -> BarLengthResult:
    mb        = cb.matched_bar
    diameter  = mb.diameter
    pb        = mb.physical_bar

    if pb is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
            is_stirrup=False, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note=f"no matched PhysicalBar (match_method={mb.match_method!r}) — cannot compute length",
            raw_text=mb.source.raw_text,
        )

    if coverage is not None and coverage['covers_full']:
        return _finish_main_bar(cb, coverage, scale)

    if coverage is not None:
        if scale is None:
            return BarLengthResult(
                numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
                is_stirrup=False, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
                note=(f"partial dimension match ({coverage['description']}) but no local or "
                      f"page-wide scale available for the {coverage['leftover_pt']:.1f}pt remainder"),
                raw_text=mb.source.raw_text,
            )
        leftover_mm = coverage['leftover_pt'] * scale
        base_length_mm = coverage['matched_mm'] + leftover_mm
        base_note = (
            f"partial dimension + scale: {coverage['description']}, "
            f"+{coverage['leftover_pt']:.1f}pt (undimensioned, e.g. a lap with no drawn bracket) "
            f"x scale {scale:.5f} = {leftover_mm:.1f}mm remainder"
        )
        return _finalize_main_bar(cb, base_length_mm, base_note)

    if scale is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=diameter,
            is_stirrup=False, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note="no dimension matched this bar's geometry and no local or page-wide scale available",
            raw_text=mb.source.raw_text,
        )
    base_length_mm = pb.length * scale
    base_note = f"geometry-only fallback (no matching dimension found): {pb.length:.1f}pt x scale {scale:.5f}"
    return _finalize_main_bar(cb, base_length_mm, base_note)


def _finish_main_bar(cb: ClassifiedBar, coverage: dict, scale: Optional[float]) -> BarLengthResult:
    """Pass-1-confirmed complete (covers_full) dimension match — no
    scale involved at all, the dimension chain alone fully accounts for
    this bar's own physical span."""
    base_note = f"dimension-based: {coverage['description']}"
    return _finalize_main_bar(cb, coverage['matched_mm'], base_note)


def _finalize_main_bar(cb: ClassifiedBar, base_length_mm: float, base_note: str) -> BarLengthResult:
    mb = cb.matched_bar
    ends           = (cb.left_end, cb.right_end)
    lap_ends       = [e for e in ends if e.is_lap]
    lap_total_mm   = sum(e.lap_length for e in lap_ends if e.lap_length is not None)

    # No cover deduction — confirmed: the drawn dimension (or the
    # geometry it stands in for) already represents the intended
    # straight length; hook length is added separately, in Excel.
    straight_length_mm = base_length_mm + lap_total_mm

    note = f"{base_note}, +{lap_total_mm:.1f}mm lap, no cover deduction"

    return BarLengthResult(
        numeric_mark=mb.numeric_mark, position=mb.position, diameter=mb.diameter,
        is_stirrup=False,
        straight_length_mm=round(straight_length_mm, 1),
        lap_length_total_mm=round(lap_total_mm, 1),
        lap_end_count=len(lap_ends),
        note=note, raw_text=mb.source.raw_text,
    )


# ── Dimension-chain reconstruction ────────────────────────────────────────────

def _find_dimension_coverage(bar_x_left: float, bar_x_right: float, zone_dims: list[dict]) -> Optional[dict]:
    """
    Try to reconstruct a bar's own physical span from this beam's own
    drawn dimensions — main span dims and curtailment/support-bar dims
    treated uniformly, chained by x-position regardless of which row
    they're drawn in (see module docstring for three confirmed real
    cases). Anchors from the LEFT first; if the bar's left edge doesn't
    line up with any dimension at all, mirrors the same search from the
    RIGHT (covers a bar whose only dimensioned end is its right one).

    Returns None if neither end anchors to any dimension — caller falls
    back to pure geometry x scale. Otherwise returns a dict with:
        matched_mm    : sum of the chained dimension value(s)
        covers_full   : True if the chain reaches the bar's OTHER edge
                        too (no scale fallback needed at all)
        leftover_pt   : un-dimensioned remainder at the bar's other end,
                        in points (0.0 if covers_full)
        description   : human-readable chain summary, for the note
    """
    left = _chain_from_left(bar_x_left, bar_x_right, zone_dims)
    if left is not None:
        return left
    return _chain_from_right(bar_x_left, bar_x_right, zone_dims)


def _chain_from_left(bar_x_left: float, bar_x_right: float, zone_dims: list[dict]) -> Optional[dict]:
    candidates = sorted(zone_dims, key=lambda d: d['x_left'])
    for start_idx, d in enumerate(candidates):
        if abs(d['x_left'] - bar_x_left) > BOUNDARY_MATCH_TOL:
            continue
        # A starting dimension that already overshoots the bar's own far
        # edge by itself can't be this bar's own dimension at all — it
        # belongs to something longer that merely happens to start at
        # the same point (confirmed real case: MBM 04 mark 8, a short
        # edge support bar whose left edge coincides with bay 1's own
        # dimension start — "4700" is bay 1's full span, not mark 8's
        # own ~1391mm length, and must never be tried as its start).
        if d['x_right'] > bar_x_right + BOUNDARY_MATCH_TOL:
            continue

        total, right_edge, used = d['value'], d['x_right'], [d]
        idx = start_idx + 1
        while idx < len(candidates):
            if abs(right_edge - bar_x_right) <= BOUNDARY_MATCH_TOL:
                break
            nxt = candidates[idx]
            if nxt['x_left'] - right_edge > CHAIN_GAP_TOL:
                break
            if nxt['x_left'] < right_edge - CHAIN_GAP_TOL:
                idx += 1
                continue
            # Same overshoot guard applied to each extension step —
            # confirmed real case: MBM 04 mark 20 laps mid-span with no
            # drawn bracket for the lap itself (see module docstring).
            # Without this guard, extending onto "1370" (a real
            # dimension, but one that fully belongs to mark 19's own
            # chain, not mark 20's) landed within undershoot tolerance
            # and was wrongly accepted as partial coverage.
            if nxt['x_right'] > bar_x_right + BOUNDARY_MATCH_TOL:
                break
            total += nxt['value']
            right_edge = nxt['x_right']
            used.append(nxt)
            idx += 1

        covers_full = abs(right_edge - bar_x_right) <= BOUNDARY_MATCH_TOL
        leftover_pt = 0.0 if covers_full else max(0.0, bar_x_right - right_edge)
        return {
            'matched_mm': total, 'covers_full': covers_full, 'leftover_pt': leftover_pt,
            'used': used, 'description': _describe(used, d['x_left'], right_edge, total),
        }
    return None


def _chain_from_right(bar_x_left: float, bar_x_right: float, zone_dims: list[dict]) -> Optional[dict]:
    candidates = sorted(zone_dims, key=lambda d: d['x_left'])
    for start_idx in range(len(candidates) - 1, -1, -1):
        d = candidates[start_idx]
        if abs(d['x_right'] - bar_x_right) > BOUNDARY_MATCH_TOL:
            continue
        # Mirror of the left-side overshoot guard above.
        if d['x_left'] < bar_x_left - BOUNDARY_MATCH_TOL:
            continue

        total, left_edge, used = d['value'], d['x_left'], [d]
        idx = start_idx - 1
        while idx >= 0:
            if abs(left_edge - bar_x_left) <= BOUNDARY_MATCH_TOL:
                break
            prv = candidates[idx]
            if left_edge - prv['x_right'] > CHAIN_GAP_TOL:
                break
            if prv['x_right'] > left_edge + CHAIN_GAP_TOL:
                idx -= 1
                continue
            if prv['x_left'] < bar_x_left - BOUNDARY_MATCH_TOL:
                break
            total += prv['value']
            left_edge = prv['x_left']
            used.insert(0, prv)
            idx -= 1

        covers_full = abs(left_edge - bar_x_left) <= BOUNDARY_MATCH_TOL
        leftover_pt = 0.0 if covers_full else max(0.0, left_edge - bar_x_left)
        return {
            'matched_mm': total, 'covers_full': covers_full, 'leftover_pt': leftover_pt,
            'used': used, 'description': _describe(used, left_edge, d['x_right'], total),
        }
    return None


def _describe(used: list[dict], x_left: float, x_right: float, total: float) -> str:
    parts = "+".join(f"{u['value']:.1f}" for u in used)
    return f"{parts}={total:.1f}mm [{x_left:.1f}-{x_right:.1f}]"


# ── Stirrups ──────────────────────────────────────────────────────────────────

def _calculate_stirrup(cb: ClassifiedBar, width: Optional[int], depth: Optional[int]) -> BarLengthResult:
    mb = cb.matched_bar

    if width is None or depth is None:
        return BarLengthResult(
            numeric_mark=mb.numeric_mark, position=mb.position, diameter=mb.diameter,
            is_stirrup=True, straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
            note="beam cross-section dimensions not available — cannot compute stirrup legs",
            raw_text=mb.source.raw_text,
        )

    leg_width_mm = 2 * (width - 2 * COVER_BEAM_MM)
    leg_depth_mm = 2 * (depth - 2 * COVER_BEAM_MM)

    note = (
        f"stirrup: A=2x({width}-2x{COVER_BEAM_MM})={leg_width_mm}mm, "
        f"B=2x({depth}-2x{COVER_BEAM_MM})={leg_depth_mm}mm, tails={STIRRUP_LEG_MM}mm each"
    )

    return BarLengthResult(
        numeric_mark=mb.numeric_mark, position=mb.position, diameter=mb.diameter,
        is_stirrup=True,
        straight_length_mm=None, lap_length_total_mm=0.0, lap_end_count=0,
        stirrup_leg_width_mm=leg_width_mm, stirrup_leg_depth_mm=leg_depth_mm,
        note=note, raw_text=mb.source.raw_text,
    )


# ── Section dimension parser (mirrors beam_converter.py's own copy) ───────────

def _parse_section(label: str) -> tuple[Optional[int], Optional[int]]:
    """Extract (width_mm, depth_mm) from a beam label like 'MBM 01 (200x600mm)'."""
    m = _SECTION_RE.search(label)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None