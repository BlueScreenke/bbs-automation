"""
parser/pdf/dimension_extractor.py

Extracts dimension values (span lengths, lap lengths, etc.) from beam
elevation drawings and assigns them to beam boxes.

Changed: box construction moved out.
------------------------------------
This module used to also compute beam boxes itself (get_beam_boxes(),
built purely from "MBM n" label-midpoint math — no geometry awareness).
That approach defaulted any single-label drawing row to the full page
width, which on Page_1_beams.pdf affected 8 of 17 beams and produced
confirmed cross-beam text contamination (e.g. MBM 09's box captured
MBM 10's and MBM 11's callouts, while MBM 09's own callouts were
captured under MBM 08's zone instead).

Box construction now lives in parser.geometry.outline_detector
(geometry-first: real outline rectangles are found directly from vector
geometry, then the nearest label is matched to each — never the
reverse). This module no longer computes boxes; it takes them as an
argument, so there's a single source of truth for beam zones shared by
both callout-text parsing (parser.py) and dimension assignment (this
module) rather than two independent computations that could disagree.

Public surface
--------------
    extract_beam_dimensions(pdf_path, boxes) -> dict[str, dict]
    extract_dimension_matches(pdf_path) -> list[dict]

extract_dimension_matches() added for endpoint_classifier.py (Step 6C)
------------------------------------------------------------------------
Lap-length lookup needs the raw, page-wide {value, y, x_left, x_right}
matches — not the per-beam all_dims/span_dims summary extract_beam_
dimensions() returns, which already discards x-position once a value is
assigned to a beam. Rather than have endpoint_classifier.py duplicate
the drawing/word loading and tick/arrowhead matching logic, that
intermediate list is now exposed directly and extract_beam_dimensions()
is rewritten as a thin wrapper over it — one source of truth for
dimension-line detection, same pattern already used for beam boxes.

Rewritten this session — fixed a real line-pairing bug
------------------------------------------------------------------
Confirmed against Page_1_beams.pdf: this drawing uses TWO distinct
dimension-line styles, not one.

  - "Gapped" style (e.g. the overall grid dimension near MBM 01's
    column circles, value 2696.5): drawn as two half-segments with the
    number sitting in a real gap between them —
    [97.64,153.92] + [177.20,233.48], text centred in the 23pt gap.
  - "Continuous" style (every per-beam span AND curtailment dimension,
    e.g. MBM 04's main spans 4700/4853.5/2696.5/4850, and its smaller
    curtailment dims like 1330/1370): drawn as ONE complete line with
    the number sitting just above it, no gap at all. Confirmed directly
    ("1330" is one line [281.06,353.06], width 72pt → 1330/72=18.47
    mm/pt, matching every other confirmed scale on the page).

The old version only knew the "gapped" style: it paired ANY two
horizontal segments at the same y with a 3-60pt gap between them,
assuming that gap always held a number. That is wrong for the
"continuous" style — when two already-complete continuous-style
dimensions happen to sit close together in the same row (confirmed:
MBM 04's "1330" and "1370" segments are only 3.4pt apart, nowhere near
enough room for a number), the old code welded them into one fake
dimension spanning from the first one's own start to the second one's
own end — nearly double the true width. Checked page-wide: EVERY
previously-extracted curtailment-scale dimension on this page (all
values below ~2700mm) was corrupted this way, with implied scales
ranging 2-9mm/pt against a confirmed true scale of ~18.35-19.85mm/pt.
Only the larger, more sparsely-spaced span dimensions happened to
escape the bug, which is why it went unnoticed until a specific bar's
computed length was checked by hand.

Fix: try each qualifying line segment against text centred ON ITSELF
first (tight x-tolerance — real "continuous" style dims confirmed
centred within ~0.1pt of their own segment's centre). Only a segment
with no such self-owned text is a candidate for the old two-segment
"gapped" pairing, and even then the merge is only accepted if a number
is centred specifically IN THE GAP between the two segments (not
merely somewhere in the same generous y-window as before). This
correctly resolves both styles without needing to know in advance
which one applies where, since MBM 04's "1330"/"1370" are each
consumed by their own self-match before either is ever considered for
pairing with its neighbour.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

import fitz
import pdfplumber


# ── Tuneable constants ────────────────────────────────────────────────────────

TEXT_Y_TOLERANCE    = 60
DIM_GAP_MIN         = 20
DIM_GAP_MAX         = 60
# DIM_GAP_MIN raised from an original 3 (this session): measured every
# genuine "gapped" style text-gap page-wide (see module docstring) —
# they cluster tightly at 23.28-27.66pt. The one confirmed false
# positive this excludes is a beam's own outline top edge, split by a
# column, with an 11.34pt gap that otherwise passed every other check
# (both ends witnessed, a real dimension's text within GAP_MATCH_X_TOL
# of the gap's centre purely by spatial coincidence).
EXT_LINE_X_TOL      = 5
H_LINE_MIN_LEN      = 20
V_LINE_MIN_LEN      = 10
SPAN_DIM_THRESHOLD  = 1000

# "Continuous" style self-match: how close a number's own x-centre must
# sit to a single candidate segment's own x-centre to count as that
# segment's own label. Confirmed real cases sit within ~0.1pt of exact
# centre (MBM 04's "1330": segment centre 317.06, text centre 316.95;
# "1370": segment centre 393.59, text centre 393.51) — real spacing
# between DIFFERENT dimensions' centres on this page is never under
# ~50pt, so this tolerance has wide margin without risking a cross-match.
SELF_MATCH_X_TOL = 12.0

# "Continuous" style self-match, vertical tolerance. Measured page-wide:
# every genuine self-match sits 3.5-4.5pt above its own line (text just
# above, no gap). The next-nearest x-coincidental-but-unrelated
# candidate sits 23pt+ away — a clean, wide empty band. Kept well below
# TEXT_Y_TOLERANCE (60, needed for the "gapped" style's larger offsets)
# specifically so a stray non-dimension line's coincidental x-alignment
# with a distant, unrelated number can't win a self-match (confirmed
# real false-positive this caught: a beam-outline-adjacent stray line
# at MBM 02 briefly matched the page's "2800" grid-dimension text 52pt
# above it before this tolerance was tightened).
SELF_MATCH_Y_TOL = 15.0

# "Gapped" style pairing: how close a number's own x-centre must sit to
# the literal midpoint of the gap between two candidate segments (not
# just anywhere in a wide shared window, which is what let the old
# version weld two already-complete "continuous" style dimensions
# together). Confirmed real case (MBM 01's "2696.5"): gap centre 165.56,
# text centre 165.6 — near-exact. Kept looser than SELF_MATCH_X_TOL
# since a gap can be a few pt off-centre depending on digit count.
GAP_MATCH_X_TOL = 10.0


# ── Public API ────────────────────────────────────────────────────────────────

def extract_beam_dimensions(pdf_path: str, boxes: list[dict], page_no: int = 1) -> dict[str, dict]:
    """
    Extract dimension values for every beam elevation found on one page
    of the PDF, and assign each to whichever box (by id) it falls within.

    Args:
        pdf_path: Path to the PDF.
        boxes:    Beam boxes from
                  parser.geometry.outline_detector.detect_beam_boxes() —
                  the single source of truth for beam zones, computed
                  once and shared with parser.py's callout-text pass.
        page_no:  1-indexed page number (default 1, for backward
                  compatibility with single-page callers). parser.py's
                  own multi-page loop always passes this explicitly.

    Returns:
        { beam_id: { 'all_dims': [float,...], 'span_dims': [float,...] } }
    """
    matched = extract_dimension_matches(pdf_path, page_no)
    return _assign_dims_to_beams(matched, boxes)


def extract_dimension_matches(pdf_path: str, page_no: int = 1) -> list[dict]:
    """
    Page-wide dimension values with their drawn position, before any
    per-beam assignment: [{ 'value': float, 'y': float, 'x_left': float,
    'x_right': float }, ...].

    This is the same matching this module has always done internally —
    pulled out as its own public function so other modules (e.g.
    parser.geometry.endpoint_classifier, which needs to look up a
    specific dimension value near a specific gap in the geometry rather
    than "all dims for beam X") can reuse it without duplicating the
    drawing/word loading and tick-mark matching logic. extract_beam_
    dimensions() now calls this internally too, so there's one source
    of truth for "what dimension values exist on this page and where" —
    the same single-computation pattern already used for beam boxes.

    Args:
        pdf_path: Path to the PDF.
        page_no:  1-indexed page number (default 1). Added for
                  multi-page support — every dimension-line search below
                  is scoped to this one page's own drawings/words, never
                  the whole document, so a multi-sheet PDF's pages never
                  cross-contaminate each other's dimension matches.
    """
    drawings = _load_drawings(pdf_path, page_no)
    words    = _load_words(pdf_path, page_no)

    h_lines, v_lines = _collect_lines(drawings)
    numeric_words = [
        (idx, w) for idx, w in enumerate(words)
        if re.match(r'^\d{3,6}(\.\d+)?$', w['text'])
    ]

    by_y: dict[int, list] = defaultdict(list)
    for hl in h_lines:
        by_y[round(hl['y'])].append(hl)

    used_words: set[int] = set()
    matched: list[dict] = []

    for y_key, segs in by_y.items():
        segs = sorted(segs, key=lambda s: s['x_left'])

        # Pass 1: "continuous" style — each segment matched to text
        # centred on itself. Segments consumed here are never offered
        # to pass 2, which is what stops two already-complete
        # dimensions sitting close together from being welded (see
        # module docstring).
        self_matched: set[int] = set()
        for seg_idx, seg in enumerate(segs):
            if not _has_witnesses(seg, v_lines, y_key):
                continue
            seg_cx = (seg['x_left'] + seg['x_right']) / 2
            word = _nearest_word(seg_cx, y_key, numeric_words, used_words, SELF_MATCH_X_TOL, SELF_MATCH_Y_TOL)
            if word is not None:
                word_idx, w = word
                used_words.add(word_idx)
                self_matched.add(seg_idx)
                matched.append({
                    'value': float(w['text']), 'y': y_key,
                    'x_left': seg['x_left'], 'x_right': seg['x_right'],
                })

        # Pass 2: "gapped" style — only for segments pass 1 left alone,
        # and only accepted if a number is centred specifically in the
        # gap between the pair (not merely nearby).
        remaining = [s for i, s in enumerate(segs) if i not in self_matched]
        for i in range(len(remaining) - 1):
            s1, s2 = remaining[i], remaining[i + 1]
            gap = s2['x_left'] - s1['x_right']
            if not (DIM_GAP_MIN < gap < DIM_GAP_MAX):
                continue
            xs, xe = s1['x_left'], s2['x_right']
            if not (_has_witnesses({'x_left': xs, 'x_right': xs}, v_lines, y_key)
                    and _has_witnesses({'x_left': xe, 'x_right': xe}, v_lines, y_key)):
                continue
            gap_cx = (s1['x_right'] + s2['x_left']) / 2
            word = _nearest_word(gap_cx, y_key, numeric_words, used_words, GAP_MATCH_X_TOL)
            if word is not None:
                word_idx, w = word
                used_words.add(word_idx)
                matched.append({
                    'value': float(w['text']), 'y': y_key,
                    'x_left': xs, 'x_right': xe,
                })

    return matched


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_drawings(pdf_path: str, page_no: int = 1) -> list:
    doc = fitz.open(pdf_path)
    return doc[page_no - 1].get_drawings()


def _load_words(pdf_path: str, page_no: int = 1) -> list[dict]:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[page_no - 1].extract_words()


def _collect_lines(drawings: list) -> tuple[list[dict], list[dict]]:
    """Split raw PyMuPDF single-item straight-line drawings into
    horizontal candidates (possible dimension lines) and vertical
    candidates (possible witness/extension lines)."""
    h_lines, v_lines = [], []
    for d in drawings:
        items = d['items']
        if len(items) != 1 or items[0][0] != 'l':
            continue
        p1, p2 = items[0][1], items[0][2]
        dy, dx = abs(p2.y - p1.y), abs(p2.x - p1.x)

        if dy < 2.0 and dx >= H_LINE_MIN_LEN:
            h_lines.append({'y': p1.y, 'x_left': min(p1.x, p2.x), 'x_right': max(p1.x, p2.x)})
        elif dx < 2.0 and dy >= V_LINE_MIN_LEN:
            v_lines.append({'x': p1.x, 'y_top': min(p1.y, p2.y), 'y_bot': max(p1.y, p2.y)})
    return h_lines, v_lines


def _has_witnesses(seg: dict, v_lines: list[dict], y_key: float) -> bool:
    """True if both ends of `seg` (x_left, x_right) have a vertical
    witness/extension line crossing this y nearby. Required for both
    matching styles so a stray black horizontal line (e.g. a
    reinforcement bar line, which can coincidentally sit near an
    unrelated vertical like a hook leg) is never mistaken for a genuine
    dimension line on its own — see module docstring."""
    has_l = any(abs(v['x'] - seg['x_left']) < EXT_LINE_X_TOL and v['y_top'] < y_key < v['y_bot'] for v in v_lines)
    has_r = any(abs(v['x'] - seg['x_right']) < EXT_LINE_X_TOL and v['y_top'] < y_key < v['y_bot'] for v in v_lines)
    return has_l and has_r


def _nearest_word(
    target_cx:      float,
    y_key:          float,
    numeric_words:  list[tuple[int, dict]],
    used_words:     set[int],
    x_tol:          float,
    y_tol:          float = TEXT_Y_TOLERANCE,
) -> Optional[tuple[int, dict]]:
    """Nearest not-yet-used numeric word whose own x-centre sits within
    `x_tol` of `target_cx` and within `y_tol` of `y_key`."""
    best, best_dist = None, float('inf')
    for idx, w in numeric_words:
        if idx in used_words:
            continue
        word_cx = (float(w['x0']) + float(w['x1'])) / 2
        wy = float(w['top'])
        if abs(word_cx - target_cx) > x_tol:
            continue
        if abs(wy - y_key) >= y_tol:
            continue
        dist = abs(word_cx - target_cx) + abs(wy - y_key) * 1.5
        if dist < best_dist:
            best_dist, best = dist, (idx, w)
    return best


def _assign_dims_to_beams(matched: list[dict], boxes: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for box in boxes:
        zone = sorted(
            [m for m in matched
             if box['x_left'] < (m['x_left']+m['x_right'])/2 < box['x_right']
             and box['y_top'] < m['y'] < box['y_bot']],
            key=lambda m: m['x_left'],
        )
        all_dims  = [m['value'] for m in zone]
        span_dims = [v for v in all_dims if v >= SPAN_DIM_THRESHOLD]
        result[box['id']] = {'all_dims': all_dims, 'span_dims': span_dims}
    return result


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    from parser.geometry.pdf_line_extractor import extract_page_primitives
    from parser.geometry.outline_detector import detect_beam_boxes

    path = sys.argv[1] if len(sys.argv) > 1 else 'input/sample.pdf'

    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words()
        page_w, page_h = page.width, page.height

    prims = extract_page_primitives(path, page_no=1)
    boxes = detect_beam_boxes(words, prims, page_w, page_h, page_no=1)

    dims = extract_beam_dimensions(path, boxes)
    for beam_id, data in dims.items():
        print(f"{beam_id:14}  spans={data['span_dims']}  all={data['all_dims']}")