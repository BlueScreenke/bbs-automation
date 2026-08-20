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
"""

from __future__ import annotations

import re
from collections import defaultdict

import fitz
import pdfplumber


# ── Tuneable constants ────────────────────────────────────────────────────────

TEXT_Y_TOLERANCE    = 60
DIM_GAP_MIN         = 3
DIM_GAP_MAX         = 60
EXT_LINE_X_TOL      = 5
H_LINE_MIN_LEN      = 20
V_LINE_MIN_LEN      = 10
SPAN_DIM_THRESHOLD  = 1000


# ── Public API ────────────────────────────────────────────────────────────────

def extract_beam_dimensions(pdf_path: str, boxes: list[dict]) -> dict[str, dict]:
    """
    Extract dimension values for every beam elevation found in the PDF,
    and assign each to whichever box (by id) it falls within.

    Args:
        pdf_path: Path to the PDF.
        boxes:    Beam boxes from
                  parser.geometry.outline_detector.detect_beam_boxes() —
                  the single source of truth for beam zones, computed
                  once and shared with parser.py's callout-text pass.

    Returns:
        { beam_id: { 'all_dims': [float,...], 'span_dims': [float,...] } }
    """
    matched = extract_dimension_matches(pdf_path)
    return _assign_dims_to_beams(matched, boxes)


def extract_dimension_matches(pdf_path: str) -> list[dict]:
    """
    Page-wide dimension values with their drawn position, before any
    per-beam assignment: [{ 'value': float, 'y': float, 'x_left': float,
    'x_right': float }, ...].

    This is the same matching this module has always done internally —
    pulled out as its own public function so other modules (e.g.
    parser.geometry.endpoint_classifier, which needs to look up a
    specific dimension value near a specific gap in the geometry rather
    than "all dims for beam X") can reuse it without duplicating the
    drawing/word loading and tick-mark matching logic. extract_beam_dimensions()
    now calls this internally too, so there's one source of truth for
    "what dimension values exist on this page and where" — the same
    single-computation pattern already used for beam boxes.
    """
    drawings = _load_drawings(pdf_path)
    words    = _load_words(pdf_path)

    dim_lines = _detect_dimension_lines(drawings)
    return _match_text_to_dim_lines(dim_lines, words)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_drawings(pdf_path: str) -> list:
    doc = fitz.open(pdf_path)
    return doc[0].get_drawings()


def _load_words(pdf_path: str) -> list[dict]:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[0].extract_words()


def _detect_dimension_lines(drawings: list) -> list[dict]:
    h_lines, v_lines = [], []

    for d in drawings:
        items = d['items']
        if len(items) != 1 or items[0][0] != 'l':
            continue
        p1, p2 = items[0][1], items[0][2]
        dy, dx = abs(p2.y - p1.y), abs(p2.x - p1.x)

        if dy < 2.0 and dx >= H_LINE_MIN_LEN:
            h_lines.append({'y': p1.y, 'x_left': min(p1.x,p2.x), 'x_right': max(p1.x,p2.x)})
        elif dx < 2.0 and dy >= V_LINE_MIN_LEN:
            v_lines.append({'x': p1.x, 'y_top': min(p1.y,p2.y), 'y_bot': max(p1.y,p2.y)})

    by_y: dict[int, list] = defaultdict(list)
    for hl in h_lines:
        by_y[round(hl['y'])].append(hl)

    dim_lines = []
    for y_key, segs in by_y.items():
        segs = sorted(segs, key=lambda s: s['x_left'])
        for i in range(len(segs) - 1):
            s1, s2 = segs[i], segs[i+1]
            gap = s2['x_left'] - s1['x_right']
            if not (DIM_GAP_MIN < gap < DIM_GAP_MAX):
                continue
            xs, xe = s1['x_left'], s2['x_right']
            has_l = any(abs(v['x']-xs) < EXT_LINE_X_TOL and v['y_top'] < y_key < v['y_bot'] for v in v_lines)
            has_r = any(abs(v['x']-xe) < EXT_LINE_X_TOL and v['y_top'] < y_key < v['y_bot'] for v in v_lines)
            if has_l and has_r:
                dim_lines.append({'y': y_key, 'x_left': xs, 'x_right': xe})

    return dim_lines


def _match_text_to_dim_lines(dim_lines: list[dict], words: list[dict]) -> list[dict]:
    numeric_words = [
        (idx, w) for idx, w in enumerate(words)
        if re.match(r'^\d{3,6}(\.\d+)?$', w['text'])
    ]
    used, matched = set(), []

    for dl in dim_lines:
        cx, cy = (dl['x_left'] + dl['x_right']) / 2, dl['y']
        best, best_dist = None, float('inf')

        for idx, w in numeric_words:
            if idx in used:
                continue
            wx, wy = float(w['x0']), float(w['top'])
            if not (dl['x_left'] - 10 < wx < dl['x_right'] + 10):
                continue
            if abs(wy - cy) >= TEXT_Y_TOLERANCE:
                continue
            dist = abs(wx - cx) + abs(wy - cy) * 1.5
            if dist < best_dist:
                best_dist, best = dist, (idx, w)

        if best:
            used.add(best[0])
            matched.append({
                'value':   float(best[1]['text']),
                'y':       cy,
                'x_left':  dl['x_left'],
                'x_right': dl['x_right'],
            })

    return matched


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