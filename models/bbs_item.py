from dataclasses import dataclass


@dataclass
class BBSItem:
    bar_mark: str
    diameter: int
    shape: str
    length: float
    quantity: int
    total_length: float
    unit_weight: float
    total_weight: float