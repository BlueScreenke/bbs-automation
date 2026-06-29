from collections import defaultdict

def group_by_diameter(results):
    """
    Groups total length and weight by bar diameter
    """
    grouped = defaultdict(lambda: {
        "total_length": 0.0,
        "total_weight": 0.0
    })

    for beam in results["beams"]:
        for bar in beam["bars"]:
            dia = bar["diameter"]
            grouped[dia]["total_length"] += bar["total_length"]
            grouped[dia]["total_weight"] += bar["total_weight"]

    # Round results
    for dia in grouped:
        grouped[dia]["total_length"] = round(grouped[dia]["total_length"], 3)
        grouped[dia]["total_weight"] = round(grouped[dia]["total_weight"], 3)

    return dict(grouped)