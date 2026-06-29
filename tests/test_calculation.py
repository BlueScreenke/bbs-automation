from models.bar import Bar
from calculation.bar_calculator import calculate_bar


def test_bar_weight_calculation():
    bar = Bar(
        mark="B1",
        diameter=10,
        length=2.0,
        quantity=5,
        shape="straight"
    )

    result = calculate_bar(bar)

    assert result["total_length"] == 10.0
    assert round(result["unit_weight"], 3) == round((10**2) / 162, 3)
    assert result["total_weight"] > 0