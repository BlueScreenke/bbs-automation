def generate_cutting_list(bars, stock_length=12.0):
    """
    bars: list of tuples -> [(length, quantity), ...]
    Returns cutting patterns and waste
    """

    # Expand bars into individual lengths
    required = []
    for length, qty in bars:
        required.extend([length] * qty)

    # Sort descending (FFD algorithm)
    required.sort(reverse=True)

    stock_bars = []

    for bar_length in required:
        placed = False

        for stock in stock_bars:
            if sum(stock) + bar_length <= stock_length:
                stock.append(bar_length)
                placed = True
                break

        if not placed:
            stock_bars.append([bar_length])

    # Prepare output
    cutting_list = []
    total_waste = 0.0

    for stock in stock_bars:
        used = sum(stock)
        waste = round(stock_length - used, 3)
        total_waste += waste

        cutting_list.append({
            "cuts": stock,
            "used_length": round(used, 3),
            "waste": waste
        })

    return {
        "stock_bars_required": len(stock_bars),
        "total_waste": round(total_waste, 3),
        "patterns": cutting_list
    }