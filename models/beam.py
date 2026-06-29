from dataclasses import dataclass, field
from typing import List
from .bar import Bar


@dataclass
class Beam:
    id: str
    span_length: float        # mm
    width: float              # mm
    depth: float              # mm
    concrete_grade: str
    bars: List[Bar] = field(default_factory=list)

    def add_bar(self, bar: Bar):
        self.bars.append(bar)