import gc
import importlib
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def create_empty_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("VACUUM")
    finally:
        conn.close()


def run(root: Path) -> None:
    manager = ProfileManager(root / "data", root / "static")
    profile_manager._MANAGER = manager
    manager.create_registry("abraj")
    for profile_id in manager.VALID_IDS:
        create_empty_db(manager.profile_db_path(profile_id))
        manager.signatures_dir(profile_id).mkdir(parents=True, exist_ok=True)

    from backend import api as api_module
    from backend import config, database
    importlib.reload(config)
    importlib.reload(database)
    importlib.reload(api_module)
    database.init_db()
    api = api_module.Api()

    result = api.switch_profile("eshraqat")
    assert result == {"ok": True, "active_profile": "eshraqat", "reload": True}
    assert profile_manager.active_profile_id() == "eshraqat"
    assert config.ACTIVE_PROFILE == "eshraqat"
    assert database.list_quotes_in_db(2026, 7) == []

    manager.profile_db_path("abraj").write_bytes(b"broken database")
    failed = api.switch_profile("abraj")
    assert failed["ok"] is False, failed
    assert failed["active_profile"] == "eshraqat"
    assert profile_manager.active_profile_id() == "eshraqat"
    assert config.ACTIVE_PROFILE == "eshraqat"

    invalid = api.switch_profile("unknown")
    assert invalid["ok"] is False
    assert profile_manager.active_profile_id() == "eshraqat"


def main() -> None:
    original_manager = profile_manager._MANAGER
    test_dir = Path(__file__).resolve().parent
    try:
        with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
            # The collector is held off for the whole run, so every connection
            # the app opens stays open and keeps abraj's -wal journal - as other
            # people's engines always will on the office server. The damaged
            # file below must be refused anyway. (With the collector free this
            # passed or failed on WHEN Python happened to collect - 2026-10-23.)
            gc.disable()
            try:
                run(Path(td))
            finally:
                gc.enable()
                gc.collect()          # close them before the folder is removed
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("profile switch backend tests passed")


if __name__ == "__main__":
    main()
