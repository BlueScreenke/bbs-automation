from dataclasses import dataclass
from typing import Optional


@dataclass
class Bar:
    mark: str                 # e.g. B1, T2
    diameter: int             # mm (8, 10, 12, 16)
    shape: str                # straight, L, U, crank
    length: float             # mm (single bar length)
    quantity: int
    steel_grade: Optional[str] = "HY"
    location: Optional[str] = None  # top, bottom, stirrup