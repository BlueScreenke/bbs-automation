import re
from models.bar import Bar


class BarExtractor:
    """
    Converts raw text into Bar objects using rule-based parsing.
    """

    # Regex patterns (start simple, improve later)
    BAR_PATTERN = r"(Y|T|R)\d+"
    DIAMETER_PATTERN = r"(Y|T|R)(\d+)"
    QUANTITY_PATTERN = r"(\d+)\s*(Y|T|R)\d+"
    SPACING_PATTERN = r"@\s*(\d+)"

    def extract_bars(self, text: str):
        bars = []

        lines = text.split("\n")

        for line in lines:
            line = line.upper()

            # Skip empty lines
            if not line.strip():
                continue

            # 1. Find bar type + diameter
            bar_type_match = re.search(self.DIAMETER_PATTERN, line)
            if not bar_type_match:
                continue

            bar_type = bar_type_match.group(1)
            diameter = int(bar_type_match.group(2))

            # 2. Quantity (default = 1)
            qty_match = re.search(self.QUANTITY_PATTERN, line)
            quantity = int(qty_match.group(1)) if qty_match else 1

            # 3. Spacing (optional metadata for later)
            spacing_match = re.search(self.SPACING_PATTERN, line)
            spacing = int(spacing_match.group(1)) if spacing_match else None

            # 4. Create Bar object (length is placeholder for now)
            bar = Bar(
                mark=f"{bar_type}{diameter}",
                diameter=diameter,
                shape="unknown",
                length=0.0,  # will be calculated later
                quantity=quantity,
                location="unknown"
            )

            bars.append(bar)

        return bars