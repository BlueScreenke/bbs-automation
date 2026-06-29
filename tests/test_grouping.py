from calculation.diameter_grouper import group_by_diameter


def test_group_by_diameter():
    mock_results = {
        "beams": [
            {
                "bars": [
                    {"diameter": 12, "total_length": 10, "total_weight": 8},
                    {"diameter": 12, "total_length": 5, "total_weight": 4},
                    {"diameter": 16, "total_length": 6, "total_weight": 9},
                ]
            }
        ]
    }

    grouped = group_by_diameter(mock_results)

    assert grouped[12]["total_length"] == 15
    assert grouped[12]["total_weight"] == 12
    assert grouped[16]["total_length"] == 6