# main.py

from parser import parse_input
from validation.beam_validator import validate_beam

from calculation.project_calculator import calculate_project
from calculation.diameter_grouper import group_by_diameter
from calculation.cutting_list import generate_cutting_list

from export.excel_exporter import export_to_excel


def prepare_cutting_data(results):
    """
    Prepares cutting list input grouped by diameter.
    Returns:
        {
            diameter: [(length_each, quantity), ...]
        }
    """
    diameter_bars = {}

    for beam in results["beams"]:
        for bar in beam["bars"]:
            dia = bar["diameter"]
            diameter_bars.setdefault(dia, [])
            diameter_bars[dia].append(
                (bar["length_each"], bar["quantity"])
            )

    return diameter_bars


def main():
    # -----------------------------
    # 1. PARSE INPUT
    # -----------------------------
    input_file = "input/sample.txt"   # adjust if needed
    beams = parse_input(input_file)

    # -----------------------------
    # 2. VALIDATION
    # -----------------------------
    errors = []
    for beam in beams:
        errors.extend(validate_beam(beam))

    if errors:
        print("\nVALIDATION FAILED ❌")
        for err in errors:
            print(err)
        return

    print("VALIDATION PASSED ✅")

    # -----------------------------
    # 3. CALCULATION
    # -----------------------------
    results = calculate_project(beams)

    print(f"\nTOTAL PROJECT STEEL: {results['project_total_weight']} kg")

    # -----------------------------
    # 4. GROUP BY DIAMETER
    # -----------------------------
    diameter_summary = group_by_diameter(results)

    print("\nSTEEL SUMMARY BY DIAMETER")
    for dia, data in diameter_summary.items():
        print(
            f"Ø{dia} mm → "
            f"{data['total_length']} m | "
            f"{data['total_weight']} kg"
        )

    # -----------------------------
    # 5. CUTTING LIST
    # -----------------------------
    cutting_data = prepare_cutting_data(results)

    for dia, bars in cutting_data.items():
        cutting = generate_cutting_list(
            bars=bars,
            stock_length=12.0
        )

        print(f"\nCUTTING LIST – Ø{dia} mm")
        print(f"Stock bars required: {cutting['stock_bars_required']}")
        print(f"Total waste: {cutting['total_waste']} m")

        for i, pattern in enumerate(cutting["patterns"], start=1):
            print(
                f" Stock {i}: "
                f"cuts={pattern['cuts']} | "
                f"waste={pattern['waste']} m"
            )

    # -----------------------------
    # 6. EXCEL EXPORT
    # -----------------------------
    output_path = "output/bbs.xlsx"
    export_to_excel(results, output_path)

    print(f"\nBBS EXPORTED SUCCESSFULLY → {output_path} ✅")


if __name__ == "__main__":
    main()