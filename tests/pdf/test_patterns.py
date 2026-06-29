from parser.pdf.patterns import (
    parse_diameter,
    parse_spacing,
    parse_quantity,
)

def test_parse_diameter():
    assert parse_diameter("Y12 @ 150 c/c") == 12
    assert parse_diameter("Ø16 bars") == 16
    assert parse_diameter("No reinforcement") is None


def test_parse_spacing():
    assert parse_spacing("Y12 @ 150") == 150
    assert parse_spacing("Y16 @200 c/c") == 200
    assert parse_spacing("Y20 bars") is None


def test_parse_quantity():
    assert parse_quantity("4 No Y12 bars") == 4
    assert parse_quantity("10 nos Y16") == 10
    assert parse_quantity("Y12 @ 150") is None