from models.beam import Beam
from parser.bar_extractor import BarExtractor


class BeamBuilder:
    def __init__(self):
        self.extractor = BarExtractor()

    def build_beam(self, text: str) -> Beam:

        bars = self.extractor.extract_bars(text)

        beam = Beam(
            id="Beam-01",
            span_length=0,
            width=0,
            depth=0,
            concrete_grade="UNKNOWN"
        )

        for bar in bars:
            beam.add_bar(bar)

        return beam