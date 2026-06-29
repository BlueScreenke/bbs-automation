REBAR_DIAMETERS = {6, 8, 10, 12, 16, 20, 25, 32}

def weight_per_meter(diameter_mm: int) -> float:
    """
    Standard steel weight formula (kg/m)
    """
    return (diameter_mm ** 2) / 162