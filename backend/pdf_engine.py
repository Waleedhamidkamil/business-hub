"""
PDF engine - renders HTML to PDF using headless Chrome or Edge.

Why this instead of WeasyPrint?
- WeasyPrint on Windows needs the GTK3 runtime (libgobject/cairo/pango DLLs).
  Wheels don't bundle it; users hit `cannot load library 'libgobject-2.0-0'`.
- Chrome/Edge are already on every Windows machine, render Arabic/RTL flawlessly
  (same Chromium engine the preview UI uses), and support --print-to-pdf natively.

Usage:
    write_pdf(html_string, out_path)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import winreg
from pathlib import Path


class PdfEngineNotFound(RuntimeError):
    pass


def _registry_app_path(name: str) -> str | None:
    """Look up an App Paths entry in HKLM/HKCU for chrome.exe or msedge.exe."""
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}",
            rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{name}",
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val and Path(val).exists():
                        return val
            except OSError:
                continue
    return None


def find_browser() -> Path:
    """
    Return a path to a usable Chromium-family browser executable.
    Order: chrome on PATH -> msedge on PATH -> registry App Paths -> common install dirs.
    """
    for name in ("chrome.exe", "msedge.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)

    for name in ("chrome.exe", "msedge.exe"):
        rp = _registry_app_path(name)
        if rp:
            return Path(rp)

    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and Path(c).exists():
            return Path(c)

    raise PdfEngineNotFound(
        "No Chromium-family browser found. Install Microsoft Edge or Google Chrome."
    )


def write_pdf(html: str, out_path: Path) -> Path:
    """
    Render `html` (a complete self-contained HTML string, CSS inlined) to PDF
    at `out_path`. Returns the path written.
    """
    browser = find_browser()
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Headless Chrome/Edge can't reliably read paths with spaces in --print-to-pdf=
    # on every version. Write to a temp dir without spaces, then move.
    workdir = Path(tempfile.gettempdir()) / f"po_tool_print_{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        html_path = workdir / "doc.html"
        html_path.write_text(html, encoding="utf-8")

        tmp_pdf = workdir / "out.pdf"

        # Each invocation gets its own user-data-dir so we never collide with
        # the user's logged-in browser profile (which would refuse a 2nd headless launch).
        user_data = workdir / "udd"
        user_data.mkdir(exist_ok=True)

        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--no-sandbox",
            f"--user-data-dir={user_data}",
            f"--print-to-pdf={tmp_pdf}",
            html_path.as_uri(),
        ]
        # Hide console window on Windows.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000  # CREATE_NO_WINDOW

        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=60, creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("PDF render timed out (browser hung)")

        if not tmp_pdf.exists():
            # Some older Chrome builds reject --no-pdf-header-footer; retry without.
            cmd2 = [c for c in cmd if c != "--no-pdf-header-footer"]
            cmd2.insert(-2, "--print-to-pdf-no-header")  # legacy flag
            res = subprocess.run(
                cmd2, capture_output=True, text=True,
                timeout=60, creationflags=creationflags,
            )

        if not tmp_pdf.exists():
            raise RuntimeError(
                f"Browser did not produce a PDF.\n"
                f"Command: {' '.join(cmd)}\n"
                f"stdout: {res.stdout[:500]}\nstderr: {res.stderr[:500]}"
            )

        # Move into final location (handles spaces in path).
        if out_path.exists():
            out_path.unlink()
        shutil.move(str(tmp_pdf), str(out_path))
        return out_path
    finally:
        # Best-effort cleanup of the temp workdir.
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass
