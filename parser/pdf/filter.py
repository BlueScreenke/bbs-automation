import re

REBAR_KEYWORDS = ["Y", "T", "Ø", "@"]


def is_rebar_candidate(line: str) -> bool:
    """Legacy line-level filter. Kept for backward compatibility."""
    return any(k in line for k in REBAR_KEYWORDS)


# Pre-compiled pattern for bar callout token detection.
# Matches any token containing a bar-type prefix (T/Y/Ø/D) followed by
# 1-2 digit diameter and a hyphen, e.g:
#   "2T16-4(T1)"    "16T8-03-300"    "a=T16-5(B1)"    "T8-200"
_BAR_CALLOUT_RE = re.compile(r'(?:Y|Ø|D|T)\d{1,2}-')


def is_bar_callout_token(token: str) -> bool:
    """
    Token-level filter for the coordinate-aware pipeline.
    Returns True if the token looks like a bar callout or label assignment.

    Accepts both quantity-first ("2T16-4(T1)") and
    label-assignment ("a=T16-5(B1)") formats.
    """
    return bool(_BAR_CALLOUT_RE.search(token))