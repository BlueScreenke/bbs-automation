from models.bar import Bar
from models.beam import Beam
from validation.bar_validator import validate_bar
from validation.beam_validator import validate_beam


def test_valid_bar_passes():
    bar = Bar(
        mark="B1",
        diameter=12,
        length=2.5,
        quantity=4,
        shape="straight"
    )
    errors = validate_bar(bar)
    assert errors == []


def test_invalid_bar_diameter_fails():
    bar = Bar(
        mark="B2",
        diameter=7,
        length=2.5,
        quantity=2,
        shape="straight"
    )
    errors = validate_bar(bar)
    assert len(errors) == 1


def test_beam_without_bars_fails():
    beam = Beam(
        id="BM1",
        span_length=5.0,
        width=300,
        depth=500,
        concrete_grade="C25",
        bars=[]
    )
    errors = validate_beam(beam)
    assert len(errors) == 1