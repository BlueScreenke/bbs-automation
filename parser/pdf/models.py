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

    flagged_for_review : bool
        True if this bar's computed result should be manually checked
        against the drawing before trusting it — set for two distinct,
        deliberately conservative reasons (see flag_reason): an
        implausible length (exceeds standard ~12m rebar stock) or an
        ambiguous match (two different numeric marks both fell back to
        the same PhysicalBar, meaning neither's own leader arrow found
        its true target). Never set for ordinary matched bars, and
        never used to silently alter a length or shape — purely a
        downstream review signal (e.g. for Excel export highlighting),
        per explicit project decision: differentiate genuine bars from
        suspicious ones by flagging, not by guessing which geometry to
        discard.

    flag_reason : str, optional
        Human-readable reason when flagged_for_review is True.
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
    flagged_for_review: bool = False
    flag_reason:        Optional[str] = None

    # Step 6D/6E fields (shape code + A/B/C/D dimension layout — see
    # parser.geometry.shape_resolver.ShapeResult). parser.py sets these
    # conditionally, only when shape_resolver actually produced that
    # particular dimension (e.g. a straight bar, shape 00, only ever
    # gets dim_a_mm — dim_b/c/d are never set on it at all; only a
    # stirrup, shape 51, populates all four). Declared here as proper
    # dataclass fields with a None default so every ParsedBarData
    # instance has all of them from construction — confirmed real bug
    # this session: beam_converter.py reads item.dim_d_mm
    # unconditionally, and without these declared (with defaults) that
    # attribute simply doesn't exist at all on any non-stirrup bar,
    # raising AttributeError rather than returning a genuine "no D
    # dimension for this shape" None.
    shape_code:        Optional[str] = None
    dim_a_mm:          Optional[float] = None
    dim_a_lookup_key:  Optional[str] = None
    dim_b_mm:          Optional[float] = None
    dim_c_mm:          Optional[float] = None
    dim_c_lookup_key:  Optional[str] = None
    dim_d_mm:          Optional[float] = None

    @property
    def bar_mark(self) -> Optional[str]:
        """Backward-compatible alias — returns numeric_mark."""
        return self.numeric_mark