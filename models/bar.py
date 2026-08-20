from dataclasses import dataclass
from typing import Optional


@dataclass
class Bar:
    """
    Domain model for a single reinforcing bar in the BBS pipeline.

    Field notes
    -----------
    mark : str
        The project-level numeric bar mark that uniquely identifies this bar
        shape in the BBS schedule. e.g. "4", "31", "03".
        This is what appears in the "Bar Mark" column of the Excel BBS.
        Previously mislabelled as a position label (B1, T2) — corrected here.

    position : str, optional
        Cross-section location label indicating where in the beam the bar sits.
        e.g. "T1" (top layer 1), "B1" (bottom layer 1), "T2" (top layer 2).
        Used for shape code assignment (Step 6) and for reading the BBS.

    beam_id : str, optional
        The beam elevation zone this bar belongs to. e.g. "MBM 01".
        Provides traceability from BBS row back to the source beam on the drawing.

    shape : str, optional
        BS8666 shape code (office convention — see shape_resolver.py):
        "00" straight | "11" hook one end | "21" hook both ends |
        "51" stirrup/link. None if shape_resolver.py could not resolve
        this bar's geometry (a genuine data gap — excel_exporter.py
        skips these rather than guess a shape).

    length : float, optional
        Carried straight through from ParsedBarData.length — see that
        field's own docstring for the exact meaning per shape. For "00"
        and "51" this is the final total; for "11"/"21" it is only the
        known-in-Python "B" portion, NOT the final cutting length (the
        hook length(s) that complete it are resolved in Excel via
        dim_a_lookup_key / dim_c_lookup_key, never in Python — see
        shape_resolver.py). excel_exporter.py writes the true total as
        an Excel formula (=SUM of A:E), not from this field.

    dim_a_mm, dim_b_mm, dim_c_mm, dim_d_mm : float, optional
        Known-in-Python A/B/C/D values for this bar's shape, where
        applicable. None where that letter is unused by this shape, or
        where the value is a hook length deferred to Excel (see below).

    dim_a_lookup_key, dim_c_lookup_key : str, optional
        Set only when the corresponding dimension is a hook length this
        module deliberately does not compute (e.g. "11_T16") —
        excel_exporter.py writes an INDEX/MATCH formula against the
        editable "Hook Parameters" sheet using this key, rather than a
        Python-computed number. Editing a hook length in the future
        means editing that Excel sheet, never this code.

    location : str, optional
        Deprecated — was used for "top", "bottom", "stirrup" classification
        before position was introduced. Kept to avoid breaking existing
        validation and calculation code that may reference it.
    """

    mark:              str
    diameter:          int
    shape:             Optional[str]
    length:            Optional[float]
    quantity:          int
    steel_grade:       Optional[str] = "HY"
    location:          Optional[str] = None
    position:          Optional[str] = None
    beam_id:           Optional[str] = None
    dim_a_mm:          Optional[float] = None
    dim_b_mm:          Optional[float] = None
    dim_c_mm:          Optional[float] = None
    dim_d_mm:          Optional[float] = None
    dim_a_lookup_key:  Optional[str] = None
    dim_c_lookup_key:  Optional[str] = None