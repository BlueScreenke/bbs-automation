from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter


def export_to_excel(results, output_path="output/bbs.xlsx"):
    wb = Workbook()

    _create_bbs_sheet(wb, results)
    _create_beam_summary_sheet(wb, results)
    _create_project_summary_sheet(wb, results)

    wb.save(output_path)


# -------------------------------
# SHEET 1: MAIN BBS
# -------------------------------
def _create_bbs_sheet(wb, results):
    ws = wb.active
    ws.title = "BBS"

    headers = [
        "Bar Mark", "Diameter (mm)", "Length Each (m)",
        "Quantity", "Total Length (m)", "Unit Weight (kg/m)",
        "Total Weight (kg)"
    ]

    ws.append(headers)
    _style_header(ws, len(headers))

    row = 2
    for beam in results["beams"]:
        for bar in beam["bars"]:
            ws.append([
                bar["mark"],
                bar["diameter"],
                bar["length_each"],
                bar["quantity"],
                bar["total_length"],
                bar["unit_weight"],
                bar["total_weight"]
            ])
            row += 1

    _auto_size_columns(ws)


# -------------------------------
# SHEET 2: BEAM SUMMARY
# -------------------------------
def _create_beam_summary_sheet(wb, results):
    ws = wb.create_sheet("Beam Summary")

    headers = ["Beam ID", "Total Reinforcement Weight (kg)"]
    ws.append(headers)
    _style_header(ws, len(headers))

    for beam in results["beams"]:
        ws.append([
            beam["beam_id"],
            beam["beam_total_weight"]
        ])

    _auto_size_columns(ws)


# -------------------------------
# SHEET 3: PROJECT SUMMARY
# -------------------------------
def _create_project_summary_sheet(wb, results):
    ws = wb.create_sheet("Project Summary")

    ws.append(["Project Total Steel Weight (kg)"])
    ws["A1"].font = Font(bold=True)

    ws.append([results["project_total_weight"]])
    ws["A2"].font = Font(bold=True)

    ws.append([])
    ws.append(["Bar Mark", "Diameter (mm)", "Total Length (m)", "Total Weight (kg)"])
    _style_header(ws, 4, start_row=4)

    for mark, data in results["bar_mark_summary"].items():
        ws.append([
            mark,
            data["diameter"],
            round(data["total_length"], 3),
            round(data["total_weight"], 3)
        ])

    _auto_size_columns(ws)


# -------------------------------
# HELPERS
# -------------------------------
def _style_header(ws, num_cols, start_row=1):
    for col in range(1, num_cols + 1):
        cell = ws[f"{get_column_letter(col)}{start_row}"]
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")


def _auto_size_columns(ws):
    for column_cells in ws.columns:
        length = max(len(str(cell.value)) if cell.value else 0 for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = length + 3