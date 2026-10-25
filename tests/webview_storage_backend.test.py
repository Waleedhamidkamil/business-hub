from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


def main() -> None:
    fake_root = Path("C:/Users/Test/AppData/Local")
    with patch.dict(os.environ, {"LOCALAPPDATA": str(fake_root)}), \
            patch.object(Path, "mkdir") as mkdir:
        storage = app._webview_storage_path(Path("D:/BusinessHub"))
    assert storage == fake_root / "BusinessHub" / "WebView2"
    mkdir.assert_called_once_with(parents=True, exist_ok=True)

    # The start-up body lives in app._main since 2026-10-02: app.main now only
    # puts the launch gate up and makes sure an error takes it down again.
    source = inspect.getsource(app._main)
    assert "private_mode=False" in source
    assert "storage_path=str(webview_storage)" in source
    print("webview persistent storage tests passed")


if __name__ == "__main__":
    main()
