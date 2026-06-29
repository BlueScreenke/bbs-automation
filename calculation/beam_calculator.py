from calculation.bar_calculator import calculate_bar

def calculate_beam(beam):
    """
    Aggregates all bars in a beam
    """
    bar_results = []
    beam_weight = 0.0

    for bar in beam.bars:
        result = calculate_bar(bar)
        beam_weight += result["total_weight"]
        bar_results.append(result)

    return {
        "beam_id": beam.id,
        "bars": bar_results,
        "beam_total_weight": round(beam_weight, 3)
    }