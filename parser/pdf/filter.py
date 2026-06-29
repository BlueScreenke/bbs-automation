REBAR_KEYWORDS = ["Y", "T", "Ø", "@"]

def is_rebar_candidate(line: str) -> bool:
    return any(k in line for k in REBAR_KEYWORDS)