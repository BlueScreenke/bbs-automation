from constants.rebar_table import weight_per_meter

def calculate_bar(bar):
    """
    Returns calculated quantities for a single bar
    """
    total_length = bar.length * bar.quantity
    unit_weight = weight_per_meter(bar.diameter)
    total_weight = total_length * unit_weight

    return {
        "mark": bar.mark,
        "diameter": bar.diameter,
        "length_each": bar.length,
        "quantity": bar.quantity,
        "total_length": round(total_length, 3),
        "unit_weight": round(unit_weight, 3),
        "total_weight": round(total_weight, 3),
    }