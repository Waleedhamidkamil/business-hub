"""
Pull the logo PNG straight out of ABRAJ PO.xlsx and save it to static/logo.png.

The xlsx embeds its images at xl/media/image1.png - we just unzip and copy.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from backend import config

DEFAULT_XLSX = r"D:\PO TOOL\ABRAJ PO.xlsx"


def main(xlsx_path: str = DEFAULT_XLSX) -> None:
    out_dir = config.STATIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(xlsx_path) as z:
        media = [n for n in z.namelist() if n.startswith("xl/media/")]
        if not media:
            print("No images embedded in workbook.")
            return
        # Largest is the logo
        biggest = max(media, key=lambda n: z.getinfo(n).file_size)
        data = z.read(biggest)
        ext = Path(biggest).suffix.lower() or ".png"
        out = out_dir / f"logo{ext}"
        out.write_bytes(data)
        print(f"Extracted: {biggest}  ->  {out}  ({len(data)} bytes)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX)
