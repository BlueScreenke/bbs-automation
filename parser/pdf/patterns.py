import re
from typing import Optional

def parse_diameter(text: str) -> Optional[int]:
    match = re.search(r"(?:Y|Ø|D)\s?(\d{2})", text)
    return int(match.group(1)) if match else None


def parse_spacing(text: str) -> Optional[int]:
    match = re.search(r"@\s?(\d{2,3})", text)
    return int(match.group(1)) if match else None


def parse_quantity(text: str) -> Optional[int]:
    match = re.search(r"(\d+)\s?(?:No|nos|bars)", text, re.I)
    return int(match.group(1)) if match else None