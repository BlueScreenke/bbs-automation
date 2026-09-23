import re
from typing import Optional


def parse_diameter(text: str) -> Optional[int]:
    """
    Matches bar diameter. Supports 1- and 2-digit diameters after T/Y/Ø/D.
    Fixes: previous version required \d{2} which silently dropped T8 bars.

    Examples:
        "16T8-03-300"       → 8
        "2T16-4(T1)"        → 16
        "2T25-31(T3)"       → 25
        "Ø16 bars"          → 16
    """
    match = re.search(r"(?:Y|Ø|D|T)\s?(\d{1,2})", text)
    return int(match.group(1)) if match else None


def parse_spacing(text: str) -> Optional[int]:
    """
    Matches bar spacing in mm.
    Supports '@' notation and dash-separated notation (stirrups).

    Examples:
        "Y12 @ 150"         → 150
        "16T8-03-300"       → 300
        "26T8-300-63"       → 300
        "48x1T8-150-156(T)" → 150   (trailing position marker — see below)
    """
    match = re.search(r"@\s?(\d{2,3})", text)
    if match:
        return int(match.group(1))

    # Dash-separated: two segments after the bar type, one is spacing (mult of 25).
    # The optional trailing "(...)" tolerates a stirrup-zone position marker some
    # drawings append after the dash pair (confirmed: Serenity_beams.dxf/.pdf use
    # "T8-150-156(T)"/"(B)" for stirrups tied to a specific top/bottom zone,
    # unlike Beams_bondo's plain "T8-03-300" with no such suffix) — without this,
    # the pattern silently failed to match at all on that drawing's own stirrups.
    m = re.search(r"(?:Y|Ø|D|T)\d+-(\d{1,3})-(\d{1,3})(?:\([A-Za-z0-9/]*\))?$", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a % 25 == 0 and b % 25 != 0:
            return a   # first segment is spacing
        if b % 25 == 0 and a % 25 != 0:
            return b   # second segment is spacing

    return None


_BARE_LABEL_QUANTITY_RE = re.compile(r"^(?:[a-zA-Z]=)?(?:Y|Ø|D|T)\d+-")

# "NxMT..." stirrup-count convention (confirmed real case: "16x1T8-300-5"
# / "21x1T8-300-5", Beams_bondo.pdf — 1BM 29 / 2BM 29's own stirrup
# line). Confirmed directly by the project owner: the leading number
# (16, 21) IS the bar's real quantity — the "x1" is a per-group-of-one
# multiplier, always 1 in every confirmed instance, not a separate
# count to combine with the leading number. Kept as its own explicit
# pattern rather than folded into the general leading-quantity regex
# above: that regex requires the quantity digits to sit DIRECTLY before
# the bar-type letter (no "x" in between), which is deliberate — a
# token with digits separated from the bar-type letter by anything
# unrecognised should stay unparsed (see _BARE_LABEL_QUANTITY_RE's own
# docstring note) until confirmed, not be guessed at automatically.
_X_MULTIPLIER_QUANTITY_RE = re.compile(r"^(\d+)x\d+(?:Y|Ø|D|T)\d+")


def parse_quantity(text: str) -> Optional[int]:
    """
    Matches bar quantity.
    Supports "N No/nos" suffix and leading integer before bar-type prefix.

    Examples:
        "4 No Y12 bars"     → 4
        "2T16-4(T1)"        → 2
        "16T8-03-300"       → 16
        "16x1T8-300-5"      → 16  (confirmed "NxM" stirrup convention)
        "T20-9(B2)"         → 1   (bare, no leading count — see below)
        "a=T20-44(B1)"      → 1   (cross-section legend label — see below)

    Bare / label-assignment convention (confirmed by the project owner
    against the rendered drawing, this session): a callout with NO
    leading quantity digit at all — either a plain bar-type token
    ("T20-9(B2)") or a cross-section legend assignment ("a=T20-44(B1)",
    "d=T8-300-2") — always represents exactly one bar. Deliberately
    narrow: only fires when the bar-type letter is the very first
    character of the token (optionally preceded by a single-letter
    label and "="), so a token that DOES carry its own leading digit
    but isn't recognised by the "NxM" pattern either stays unparsed
    rather than being silently guessed at.
    """
    match = re.search(r"(\d+)\s?(?:No|nos)\b", text, re.I)
    if match:
        return int(match.group(1))
    stripped = text.strip()
    match = re.search(r"^(\d+)(?:Y|Ø|D|T)\d+", stripped)
    if match:
        return int(match.group(1))
    match = _X_MULTIPLIER_QUANTITY_RE.match(stripped)
    if match:
        return int(match.group(1))
    if _BARE_LABEL_QUANTITY_RE.match(stripped):
        return 1
    return None


def parse_numeric_mark(text: str) -> Optional[str]:
    """
    Extracts the project-level numeric bar mark — the sequential number
    that uniquely identifies a bar shape in the BBS schedule.

    This is DISTINCT from the position label (T1, B1, etc.):
        "2T16-4(T1)"  → numeric mark = "4",  position = "T1"
        "2T16-13(T2)" → numeric mark = "13", position = "T2"
        "16T8-03-300" → numeric mark = "03", spacing  = 300
        "26T8-300-63" → numeric mark = "63", spacing  = 300
        "2T16-25"     → numeric mark = "25"

    Returns None if no numeric mark is detectable.
    """
    # Main bar: NTdd-MARK(POSITION) — mark is between '-' and '('
    m = re.search(r"(?:Y|Ø|D|T)\d+-(\d+)\(", text)
    if m:
        return m.group(1)

    # Stirrup: NTdd-A-B where one of A/B is the spacing (divisible by 25).
    # See parse_spacing()'s own note: the optional trailing "(...)" tolerates
    # a stirrup-zone position marker some drawings append (e.g. "(T)"/"(B)").
    m = re.search(r"(?:Y|Ø|D|T)\d+-(\d{1,3})-(\d{1,3})(?:\([A-Za-z0-9/]*\))?$", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a % 25 == 0 and b % 25 != 0:
            return str(b)          # b is the mark
        if b % 25 == 0 and a % 25 != 0:
            mark = str(a)
            return mark.zfill(2) if len(mark) == 1 else mark  # e.g. "3" → "03"

    # No position, no spacing — trailing number is the mark
    m = re.search(r"(?:Y|Ø|D|T)\d+-(\d+)$", text)
    if m:
        return m.group(1)

    return None


def parse_position(text: str) -> Optional[str]:
    """
    Extracts the position label (T1, T2, B1, B2, B3, etc.) that
    indicates where in the beam cross-section the bar sits.

    This is kept separate from parse_numeric_mark — they are different fields:
        "2T16-4(T1)" → position = "T1", numeric mark = "4"

    Examples:
        "2T16-4(T1)"            → "T1"
        "67T10-08-200(B1/T1)"   → "B1/T1"
        "2T25-31(T3)"           → "T3"
        "16T8-03-300"           → None  (stirrups have no position label)
    """
    m = re.search(r"\(([A-Z]\d+(?:/[A-Z]\d+)*)\)", text)
    return m.group(1) if m else None