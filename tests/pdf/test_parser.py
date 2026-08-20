"""
tests/pdf/test_parser.py

Integration tests for the PDF parsing pipeline using Page_1_beams.pdf.
Place the PDF at: tests/pdf/Page_1_beams.pdf

Changes from v2
----------------
- Added test_no_cross_beam_contamination(): a regression test for the
  bug found and fixed via parser.geometry.outline_detector. The old
  label-midpoint box builder (dimension_extractor.get_beam_boxes(),
  now retired) defaulted any single-label drawing row to the full page
  width, which on this drawing caused MBM 09's zone to capture MBM 10's
  and MBM 11's callout text verbatim, while MBM 09's own callouts were
  captured under MBM 08's zone instead. None of the existing tests
  below would have caught this — they check aggregate shape (counts,
  ratios, set membership), never per-beam content — so this test
  checks specific known callouts land under their correct beam_id.

- Added test_per_beam_bar_count_sane(): the existing
  test_reasonable_bar_count() only bounds the *total* count across all
  beams, which would happily hide one beam with 0 bars and another
  with 60 as long as the sum stayed in range. This checks each
  individual beam.

- Added test_all_expected_beam_ids_present(): checks all 16 distinct
  beam labels on this drawing are present (MBM 05 appears twice in the
  source drawing — a labelling typo, confirmed against the drawing,
  where the second occurrence should read "MBM 06" — so 17 box entries
  produce 16 distinct ids; MBM 06/13 don't otherwise exist on this
  page).

- Corrected test_reasonable_bar_count()'s docstring: it claimed "6-9
  bars each," which was never true for this drawing (real per-beam
  counts range from 3 to 26 — stirrup quantity alone varies a lot by
  beam) and predates this investigation.
"""

import pytest
from parser.pdf import parse_pdf, ParsedBarData

PDF_PATH = "tests/pdf/Page_1_beams.pdf"


def test_parse_pdf_returns_list():
    results = parse_pdf(PDF_PATH)
    assert isinstance(results, list)
    assert len(results) > 0


def test_all_results_are_parsed_bar_data():
    results = parse_pdf(PDF_PATH)
    assert all(isinstance(r, ParsedBarData) for r in results)


def test_parsed_bar_data_has_required_fields():
    results = parse_pdf(PDF_PATH)
    bar = results[0]
    assert hasattr(bar, "diameter")
    assert hasattr(bar, "spacing")
    assert hasattr(bar, "quantity")
    assert hasattr(bar, "numeric_mark")
    assert hasattr(bar, "position")
    assert hasattr(bar, "beam_id")
    assert hasattr(bar, "beam_label")
    assert hasattr(bar, "confidence")
    assert hasattr(bar, "length")


def test_confidence_in_valid_range():
    results = parse_pdf(PDF_PATH)
    for bar in results:
        assert 0.0 <= bar.confidence <= 1.0, (
            f"Confidence out of range for {bar.raw_text!r}: {bar.confidence}"
        )


def test_all_bars_have_diameter():
    results = parse_pdf(PDF_PATH)
    missing = [r.raw_text for r in results if r.diameter is None]
    assert not missing, f"Bars missing diameter: {missing}"


def test_beam_ids_are_assigned():
    results = parse_pdf(PDF_PATH)
    missing = [r.raw_text for r in results if not r.beam_id]
    assert not missing, f"Bars missing beam_id: {missing}"


def test_reasonable_bar_count():
    """Page_1_beams.pdf has 17 beam entries (16 distinct MBM ids) with
    per-beam bar counts ranging roughly 3-26 (stirrup quantity varies a
    lot by beam length, e.g. MBM 05 legitimately has 26)."""
    results = parse_pdf(PDF_PATH)
    assert 50 < len(results) < 300, (
        f"Unexpected bar count: {len(results)} — "
        "check for parser regression or PDF change"
    )


def test_known_beams_present():
    results = parse_pdf(PDF_PATH)
    beam_ids = {r.beam_id for r in results if r.beam_id}
    for expected in ["MBM 01", "MBM 02", "MBM 11"]:
        assert expected in beam_ids, f"Expected beam {expected!r} not found"


def test_all_expected_beam_ids_present():
    """All 16 distinct beam labels on this drawing should be present.
    MBM 05 is drawn twice (the second occurrence is a labelling typo on
    the drawing itself — confirmed it should read "MBM 06" — so this is
    17 box entries collapsing to 16 distinct ids; MBM 06/13 don't
    otherwise exist as separate beams on this page)."""
    results = parse_pdf(PDF_PATH)
    beam_ids = {r.beam_id for r in results if r.beam_id}
    expected = {
        "MBM 01", "MBM 02", "MBM 03", "MBM 04", "MBM 05", "MBM 07",
        "MBM 08", "MBM 09", "MBM 10", "MBM 11", "MBM 12", "MBM 14",
        "MBM 15", "MBM 16", "MBM 17", "MBM 18",
    }
    missing = expected - beam_ids
    assert not missing, f"Expected beam ids not found: {missing}"


def test_per_beam_bar_count_sane():
    """Guards against a box swallowing (or losing) a whole beam's worth
    of callouts. test_reasonable_bar_count() only bounds the *total*
    across all beams, which would hide one beam with 0 bars and another
    with 60 as long as the sum stayed in range. Bounds here are set
    generously around the real observed range (3-26) rather than tight
    to it, since stirrup-heavy beams can legitimately run higher."""
    results = parse_pdf(PDF_PATH)
    by_beam: dict[str, int] = {}
    for r in results:
        if r.beam_id:
            by_beam[r.beam_id] = by_beam.get(r.beam_id, 0) + 1

    empty = [b for b in by_beam if by_beam[b] == 0]
    assert not empty, f"Beams with zero bars: {empty}"

    outliers = {b: n for b, n in by_beam.items() if not (2 <= n <= 35)}
    assert not outliers, (
        f"Beam(s) with implausible bar counts (possible cross-beam "
        f"contamination or a swallowed zone): {outliers}"
    )


def test_no_cross_beam_contamination():
    """
    Regression test for the box-construction bug fixed via
    parser.geometry.outline_detector (see module docstring). Checks a
    sample of specific callouts, confirmed by hand against the source
    drawing, land under their correct beam_id and nowhere else.

    Before the fix: MBM 09's box (label-midpoint math, defaulted to
    full page width since it was the only label in its drawing row)
    captured MBM 10's and MBM 11's callouts verbatim, while MBM 09's
    own callouts were captured under MBM 08's zone instead.
    """
    results = parse_pdf(PDF_PATH)
    by_text: dict[str, set[str]] = {}
    for r in results:
        by_text.setdefault(r.raw_text, set()).add(r.beam_id)

    # (raw_text, expected beam_id) — confirmed against Page_1_beams.pdf
    expected_home = [
        ("2T20-51(B1)", "MBM 09"),
        ("2T16-52(B2)", "MBM 09"),
        ("2T16-55(B1)", "MBM 09"),
        ("2T16-57(T2)", "MBM 10"),
        ("2T16-58(T2)", "MBM 10"),
        ("3T16-61(T1)", "MBM 11"),
        ("4T25-60(T2)", "MBM 11"),
        ("2T16-65(T1)", "MBM 12"),
        ("2T16-64(B1)", "MBM 12"),
    ]

    wrong_home = []
    for raw_text, expected_beam in expected_home:
        actual_beams = by_text.get(raw_text, set())
        if actual_beams != {expected_beam}:
            wrong_home.append((raw_text, expected_beam, actual_beams))

    assert not wrong_home, (
        "Callout(s) assigned to the wrong beam (raw_text, expected, actual): "
        f"{wrong_home}"
    )

    # MBM 09 should contain none of MBM 10's or MBM 11's known callouts.
    mbm09_texts = {r.raw_text for r in results if r.beam_id == "MBM 09"}
    contamination = mbm09_texts & {
        "2T16-57(T2)", "2T16-58(T2)", "2T16-59(T1)",
        "3T16-61(T1)", "4T25-60(T2)", "3T20-62(B1)",
    }
    assert not contamination, (
        f"MBM 09 contains callout(s) belonging to MBM 10/11: {contamination}"
    )


def test_lengths_populated():
    """All bars should have a calculated length after Module 3 integration."""
    results = parse_pdf(PDF_PATH)
    pending = [r for r in results if r.length is None]
    assert not pending, (
        f"{len(pending)} bars still have length=None: "
        f"{[r.raw_text for r in pending[:5]]}"
    )


def test_t8_stirrups_detected():
    results = parse_pdf(PDF_PATH)
    stirrups = [r for r in results if r.diameter == 8 and r.spacing is not None]
    assert len(stirrups) > 0, "No T8 stirrups detected"


def test_main_bars_have_position():
    """T16/T20/T25 bars should have a position label (T1, B1, etc.)."""
    results = parse_pdf(PDF_PATH)
    main_bars = [r for r in results if r.diameter in (16, 20, 25)]
    with_pos = [r for r in main_bars if r.position]
    ratio = len(with_pos) / len(main_bars) if main_bars else 0
    assert ratio > 0.7, (
        f"Only {ratio:.0%} of main bars have a position label "
        f"({len(with_pos)}/{len(main_bars)})"
    )