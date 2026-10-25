"""
Dump the structure of ABRAJ PO.xlsx so we can mirror it exactly in CSS.

Reports:
- sheet dimensions
- every non-empty cell: address, value, font (name/size/bold/color), fill color,
  alignment, border
- merged ranges
- column widths, row heights
- embedded image info
"""
import sys
from openpyxl import load_workbook

p = r"D:\PO TOOL\ABRAJ PO.xlsx"
wb = load_workbook(p, data_only=False)
ws = wb.active

print(f"=== Sheet: {ws.title}  dims={ws.dimensions}  max_row={ws.max_row}  max_col={ws.max_column}")
print(f"RTL? sheet_view.rightToLeft = {ws.sheet_view.rightToLeft}")

print("\n=== Merged ranges ===")
for r in ws.merged_cells.ranges:
    print(" ", r)

print("\n=== Column widths ===")
for letter, dim in ws.column_dimensions.items():
    if dim.width:
        print(f"  col {letter}: width={dim.width}")

print("\n=== Row heights ===")
for idx, dim in ws.row_dimensions.items():
    if dim.height:
        print(f"  row {idx}: height={dim.height}")

print("\n=== Non-empty cells ===")
for row in ws.iter_rows():
    for cell in row:
        if cell.value is None or cell.value == "":
            continue
        f = cell.font
        fill = cell.fill
        a = cell.alignment
        bg = ""
        try:
            if fill and fill.fgColor and fill.fgColor.type == "rgb":
                bg = fill.fgColor.rgb
        except Exception:
            pass
        color = ""
        try:
            if f.color and f.color.type == "rgb":
                color = f.color.rgb
        except Exception:
            pass
        print(f"  {cell.coordinate:>5}  val={cell.value!r:<40}  "
              f"font={f.name} {f.size}pt b={f.bold} color={color}  "
              f"bg={bg}  align=h:{a.horizontal} v:{a.vertical}")

print("\n=== Images ===")
for img in ws._images:
    try:
        print(f"  anchor={img.anchor._from.col},{img.anchor._from.row}  "
              f"size={img.width}x{img.height}  format={img.format}")
    except Exception as e:
        print(f"  (image, can't introspect: {e})")
