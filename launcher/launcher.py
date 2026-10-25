"""Business Hub on a PC in the office (Phase 2, 2026-10-22).

    pythonw launcher\\launcher.py
    pythonw launcher\\launcher.py --home D:\\try\\pc2     (a second "PC" on this one, for testing:
                                                       its own login, copies and browser data)

Opens the app's window. The screens and the data live on the office server;
this window shows them, and does on THIS PC what must happen here: opening,
showing, copying and printing PDFs (from the person's own copy in
Documents\\Business Hub), and picking files to send. See launcher/client.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from launcher.client import LauncherApi, appdata, login_html  # noqa: E402


def _storage(home: Path | None) -> Path:
    """Its own WebView2 profile, apart from the desktop app's (both may run on one PC)."""
    if home:
        path = home / "WebView2"
    else:
        path = Path(os.environ.get("LOCALAPPDATA") or appdata()) / "BusinessHub" / "Launcher" / "WebView2"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _own_taskbar_button() -> None:
    """Its own taskbar button and icon, not grouped under Python."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BusinessHub.Launcher")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    import argparse
    import webview
    _own_taskbar_button()
    ap = argparse.ArgumentParser(description="Business Hub on an office PC")
    ap.add_argument("--home", type=Path, help="keep this 'PC's settings, copies and browser data here")
    args = ap.parse_args(argv)
    home = args.home.resolve() if args.home else None
    if home:
        api = LauncherApi(settings_path=home / "launcher.json", docs_root=home / "Documents" / "Business Hub",
                          cache_root=home / "Temp")
    else:
        api = LauncherApi()
    start = api._resume()            # "keep me logged in" and the server answers: straight in
    window = webview.create_window(
        title="Business Hub" + (f" ({home.name})" if home else ""), url=start,
        html=None if start else login_html(), js_api=api,
        width=1280, height=820, min_size=(1000, 700), maximized=not home)
    api._window = window
    try:
        window.events.closed += lambda: os._exit(0)     # as app.py: no WinForms teardown race
    except Exception:
        pass
    webview.start(debug=os.environ.get("ABRAJ_DEBUG", "").strip() == "1",
                  private_mode=False, storage_path=str(_storage(home)),
                  icon=str(HERE / "business-hub.ico"))
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
