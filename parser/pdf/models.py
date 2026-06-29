from dataclasses import dataclass
from typing import Optional

@dataclass
class ParsedBarData:
    source: str
    raw_text: str
    diameter: Optional[int]
    length: Optional[float]
    spacing: Optional[int]
    quantity: Optional[int]
    bar_mark: Optional[str]
    confidence: float
    page: int