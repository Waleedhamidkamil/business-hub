"""
PO Tool - desktop entry point.

Run:
    python app.py

This launches a pywebview window (Edge WebView2 on Windows) hosting the
HTML/JS app under templates/index.html. The JS calls into backend.api.Api
via pywebview's js_api bridge.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _hide_console() -> None:
    """Hide the black cmd/console window that pops up next to the app on
    Windows when launched via python.exe / py.exe.

    We detach from (and hide) the console window owned by this process. When
    the app is started with pythonw.exe (e.g. the bundled .vbs launcher) there
    is no console at all and this is a harmless no-op. When started from an
    existing terminal we only hide if the console belongs to us, so we never
    nuke the user's own shell window.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        # Only hide the console if THIS process owns it (its pid is the
        # console window's owner). Avoids hiding a parent terminal.
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == kernel32.GetCurrentProcessId():
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


# Hide the console as early as possible so the flash is minimal.
_hide_console()

# Make `backend` importable when running from this folder.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# The launch card (2026-10-20; the doors before it, 2026-10-02): drawn by
# Windows, in its own small process, on screen a fraction of a second after
# the click - long before pywebview and WebView2 can show anything. Started in
# main(), so importing this file (tests do) never puts it up. See
# backend/launch_gate.py.
from backend import launch_gate  # noqa: E402  (stdlib only at import)

_GATE = None


def _webview_storage_path(app_dir: Path) -> Path:
    """Return the stable app-level WebView2 profile directory.

    pywebview's private mode uses a temporary profile that it deletes on exit.
    WebView2 can keep BrowserMetrics locked past shutdown, causing noisy access
    denied warnings. A persistent profile avoids that delete race and lets the
    browser reuse its cache between launches.
    """
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local_app_data) if local_app_data else app_dir / "data"
    storage = root / "BusinessHub" / "WebView2"
    storage.mkdir(parents=True, exist_ok=True)
    return storage


def _warm_start_up(api) -> None:
    """Compute the dashboard's data before the page asks - backend/boot_cache.py.

    Started when the WINDOW appears, not earlier: from then until the page
    boots (~1 s) Python is idle. Started right after the company data opened,
    it competed with pywebview's own start-up for the interpreter lock and the
    window appeared ~0.3 s later (measured 2026-10-02). The launch card works
    out its own rows in its own process (2026-10-20), so nothing here feeds it."""
    from backend import boot_cache
    boot_cache.start(api)


def main() -> None:
    global _GATE
    _GATE = launch_gate.start(Path(__file__).resolve().parent)
    try:
        _main()
    except BaseException:
        if _GATE:
            _GATE.close()      # never leave the card over an error dialog
        launch_gate.reveal_window()      # nor the app window invisible
        raise


def _main() -> None:
    # Profile paths must exist before importing modules that bind config paths.
    from backend.profile_manager import manager as profile_manager

    app_dir = Path(__file__).resolve().parent
    bootstrap = profile_manager().bootstrap_profiles(
        legacy_db=app_dir / "data" / "po_tool.db",
        legacy_settings=app_dir / "data" / "settings.json",
        legacy_assets=app_dir / "static",
    )
    if not bootstrap.get("ok"):
        warning = f"Company profile setup failed; using legacy Abraj data: {bootstrap.get('error', 'unknown error')}"
        os.environ["DOCUMENTS_HUB_LEGACY_FALLBACK"] = "1"
        os.environ["DOCUMENTS_HUB_PROFILE_WARNING"] = warning
        print(f"[profile] {warning}", file=sys.stderr)

    from backend import database, migrations
    from backend.api import Api
    import webview  # pywebview - after the gate is up and the company data is open

    database.init_db()
    # One-shot data migrations (idempotent - safe to run on every launch).
    try:
        report = migrations.run_all()
        failures = []
        for step in report:
            if step.get("status") == "applied":
                print(f"[migration] {step['key']}: {step.get('notes', '')}")
            elif step.get("status") == "failed":
                failures.append(step)
                print(f"[migration] {step['key']} FAILED: {step.get('error', '')}",
                      file=sys.stderr)
        if failures:
            names = ", ".join(str(step.get("key") or "unknown") for step in failures)
            if os.environ.get("DOCUMENTS_HUB_LEGACY_FALLBACK") == "1":
                os.environ["DOCUMENTS_HUB_PROFILE_WARNING"] += f"; database migrations failed: {names}"
            else:
                raise RuntimeError(f"Database migrations failed: {names}")
    except Exception as e:
        if os.environ.get("DOCUMENTS_HUB_LEGACY_FALLBACK") == "1":
            os.environ["DOCUMENTS_HUB_PROFILE_WARNING"] += f"; migration runner error: {e}"
            print(f"[migration] legacy fallback warning: {e}", file=sys.stderr)
        else:
            raise RuntimeError(f"Database migration setup failed: {e}") from e
    launch_gate.note("company_data")
    if _GATE:
        _GATE.stage_done(1)                  # company data is open
    api = Api()
    index_path = (Path(__file__).resolve().parent / "templates" / "index.html").as_uri()
    # Phase M (item 7) - start maximized so the app feels like a real desktop
    # app, not a small floating window. Min size still guards against accidental
    # tiny resizes.
    window = webview.create_window(
        title="Business Hub",
        url=index_path,
        js_api=api,
        width=1280,
        height=820,
        min_size=(1000, 700),
        maximized=True,
    )
    # Windows shutdown race: when the window closes, WebView2/WinForms tears down
    # the UI thread while a final cross-thread call may still be pending, throwing
    # System.ComponentModel.InvalidAsynchronousStateException - and pythonnet then
    # crashes trying to marshal that .NET exception (NullReferenceException in
    # AllocateTypeObject), surfacing an ugly unhandled-exception dialog on every
    # close. Nothing is persisted at exit (all settings/DB writes are synchronous
    # during use; there are no atexit handlers), so we terminate the process the
    # instant the window closes - before that teardown can run. Guarded so an
    # unexpected pywebview event API can't break startup.
    try:
        window.events.closed += lambda: os._exit(0)
    except Exception:
        pass
    # The window loads invisibly under the launch card and fades in as the
    # card leaves (Api.gate_ready); with no card it shows as it always did.
    # See backend/launch_gate.py hold_window.
    launch_gate.hold_window(window)
    def _stage(n: int, name: str) -> None:
        launch_gate.note(name)
        if _GATE:
            _GATE.stage_done(n)

    def _shown() -> None:
        _stage(2, "window_shown")                                     # the window exists
        _warm_start_up(api)
    try:
        window.events.shown += _shown
        window.events.loaded += lambda: _stage(3, "page_loaded")     # the page has loaded
    except Exception:
        _warm_start_up(api)
    # Phase M (item 8) - devtools are off by default for a clean release
    # experience. Set ABRAJ_DEBUG=1 in the environment to re-enable them when
    # diagnosing JS errors.
    debug = os.environ.get("ABRAJ_DEBUG", "").strip() == "1"
    webview_storage = _webview_storage_path(app_dir)
    webview.start(
        debug=debug,
        private_mode=False,
        storage_path=str(webview_storage),
    )
    # Fallback: if start() returns normally, exit hard before .NET finalizers run.
    os._exit(0)


if __name__ == "__main__":
    main()
