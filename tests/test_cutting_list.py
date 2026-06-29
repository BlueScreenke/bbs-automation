from calculation.cutting_list import generate_cutting_list


def test_cutting_list_basic():
    bars = [
        (3.0, 2),  # two bars of 3m
        (4.0, 1),  # one bar of 4m
    ]

    result = generate_cutting_list(bars, stock_length=12.0)

    assert result["stock_bars_required"] >= 1
    assert result["total_waste"] >= 0


def test_cutting_list_exact_fit():
    bars = [(6.0, 2)]

    result = generate_cutting_list(bars, stock_length=12.0)

    assert result["stock_bars_required"] == 1
    assert result["total_waste"] == 0