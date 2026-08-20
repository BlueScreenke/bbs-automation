from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedBarData:
    """
    Intermediate representation of a single bar callout extracted from a PDF.

    Field notes
    -----------
    numeric_mark : str, optional
        Project-level sequential bar mark. e.g. "4" from "2T16-4(T1)".

    position : str, optional
        Cross-section location label. e.g. "T1", "B1", "B1/T1".

    beam_id : str, optional
        The beam elevation zone this bar belongs to. e.g. "MBM 01".
        Set by the parser during zone processing.

    beam_label : str, optional
        Full beam label including section dimensions. e.g. "MBM 01 (200x600mm)".
        Used by beam_converter to extract width and depth for the Beam model.

    bar_mark : property
        Deprecated backward-compatible alias for numeric_mark.
        
    x0, top : float, optional
        Page coordinates of the callout token (pdfplumber's x0/top).
        Needed to match this occurrence to its geometric bar line —
        not populated before this fix.

    Geometry classification fields (populated by parser.py once the
    Step 6B/6C geometry pipeline — bar_detector.detect_bars(),
    bar_matcher.match_bars_to_geometry(), endpoint_classifier.classify_all()
    — has run for this bar's beam zone). None if that beam had no
    reliable outline, or if this occurrence's callout never matched a
    PhysicalBar (see match_method).

    hook_count : int, optional
        0, 1, or 2 — how many ends of this bar's matched PhysicalBar
        are hooked (bezier + vertical leg signature; see
        bar_detector.py). Hook length itself always comes from
        length_calculator.py's diameter-based table, never from this.

    is_lapped : bool, optional
        True if either end of this bar's matched PhysicalBar is a
        confirmed lap junction (connector + deliberate y-shift, or a
        genuine empty gap to another bar at the same y — see
        bar_detector.py's empirical basis).

    lap_length : float, optional
        Resolved lap length in mm, if is_lapped. A drawn dimension
        bracketing the junction if bar_detector found one, else
        50 x diameter (this drawing's own General Note 14 default,
        applied by endpoint_classifier.py once a diameter is known).
        None if is_lapped is True but neither a drawn dimension nor a
        diameter was available to fall back on.

    lap_source : str, optional
        "drawn" | "assumed_50d" — which of the above lap_length is.

    match_method : str, optional
        How this occurrence's callout was linked to its PhysicalBar:
        "leader_arrow" | "learned_y" | "ordinal" | "leader_arrow_retry"
        | "unmatched" | "no_outline". See bar_matcher.py.

    Shape/length fields (populated by parser.py once length_calculator.py
    and shape_resolver.py — Step 6D — have run for this bar). Kept flat
    (plain float/str, not the richer Dimension/BarLengthResult types
    those modules use internally) deliberately: this module sits below
    both of them in the import graph (bar_matcher.py imports
    ParsedBarData from here), so importing their types back into this
    file would create a circular import. The flat fields below are the
    full contract those modules promise; nothing is lost by not typing
    them more richly here.

    shape_code : str, optional
        "00" (straight) | "11" (hooked one end) | "21" (hooked both
        ends) | "51" (stirrup/link) — office convention, not the literal
        BS8666:2005 catalogue numbering. See shape_resolver.py.

    length : float, optional
        For shape "00": the final cutting length in mm. For shape "51"
        (stirrup): also final — every stirrup dimension is known in
        Python (see length_calculator.py), so this is
        dim_a_mm+dim_b_mm+dim_c_mm+dim_d_mm. For shape "11"/"21": this
        is ONLY the bar's known-in-Python "B" portion, NOT the final
        cutting length — the hook length(s) that complete the total are
        deliberately left for the Excel export step to resolve via
        dim_a_lookup_key/dim_c_lookup_key (see below). Consumers that
        need a true final total for a hooked bar must wait for that
        Excel-side sum; this field alone is not it. None if no length
        could be computed at all (unmatched geometry, or no page scale)
        — a genuine data gap, not guessed.

    dim_a_mm, dim_b_mm, dim_c_mm, dim_d_mm : float, optional
        Known-in-Python A/B/C/D dimension values, where applicable for
        this bar's shape_code. None where that letter isn't used by this
        shape, AND None (with the matching *_lookup_key set instead)
        where the value is a hook length deliberately deferred to Excel.

    dim_a_lookup_key, dim_c_lookup_key : str, optional
        Set only for a hook dimension (shape "11"'s A, shape "21"'s A
        and C) — e.g. "11_T16". The Excel export step resolves this via
        INDEX/MATCH against an editable hook-parameter reference sheet,
        the same pattern the project's reference BBS template already
        uses. Adjusting a hook length in the future means editing that
        Excel table, never this code.
    """

    source:       str
    raw_text:     str
    diameter:     Optional[int]
    length:       Optional[float]
    spacing:      Optional[int]
    quantity:     Optional[int]
    numeric_mark: Optional[str]
    position:     Optional[str]
    confidence:   float
    page:         int
    beam_id:      Optional[str] = None
    beam_label:   Optional[str] = None
    x0:           Optional[float] = None
    top:          Optional[float] = None
    hook_count:   Optional[int] = None
    is_lapped:    Optional[bool] = None
    lap_length:   Optional[float] = None
    lap_source:   Optional[str] = None
    match_method: Optional[str] = None
    shape_code:        Optional[str] = None
    dim_a_mm:           Optional[float] = None
    dim_b_mm:           Optional[float] = None
    dim_c_mm:           Optional[float] = None
    dim_d_mm:           Optional[float] = None
    dim_a_lookup_key:    Optional[str] = None
    dim_c_lookup_key:    Optional[str] = None

    @property
    def bar_mark(self) -> Optional[str]:
        """Backward-compatible alias — returns numeric_mark."""
        return self.numeric_mark