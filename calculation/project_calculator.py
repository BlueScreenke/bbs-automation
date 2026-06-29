from calculation.beam_calculator import calculate_beam
from collections import defaultdict

def calculate_project(beams):
    project_summary = []
    total_project_weight = 0.0

    bar_mark_totals = defaultdict(lambda: {
        "diameter": None,
        "total_length": 0.0,
        "total_weight": 0.0
    })

    for beam in beams:
        beam_result = calculate_beam(beam)
        project_summary.append(beam_result)
        total_project_weight += beam_result["beam_total_weight"]

        for bar in beam_result["bars"]:
            mark = bar["mark"]
            bar_mark_totals[mark]["diameter"] = bar["diameter"]
            bar_mark_totals[mark]["total_length"] += bar["total_length"]
            bar_mark_totals[mark]["total_weight"] += bar["total_weight"]

    return {
        "beams": project_summary,
        "bar_mark_summary": bar_mark_totals,
        "project_total_weight": round(total_project_weight, 3)
    }