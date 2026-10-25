import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import profile_manager


def main() -> None:
    old_fallback = os.environ.get("DOCUMENTS_HUB_LEGACY_FALLBACK")
    old_warning = os.environ.get("DOCUMENTS_HUB_PROFILE_WARNING")
    try:
        os.environ["DOCUMENTS_HUB_LEGACY_FALLBACK"] = "1"
        os.environ["DOCUMENTS_HUB_PROFILE_WARNING"] = "Test recovery warning"
        app_root = Path(__file__).resolve().parents[1]
        assert profile_manager.active_profile_id() == "abraj"
        assert profile_manager.profile_db_path() == app_root / "data" / "po_tool.db"
        assert profile_manager.settings_path() == app_root / "data" / "settings.json"
        assert profile_manager.assets_dir() == app_root / "static"
        assert profile_manager.shared_db_path() == app_root / "data" / "po_tool.db"

        from backend import config
        importlib.reload(config)
        assert config.DB_PATH == app_root / "data" / "po_tool.db"
        assert config.SETTINGS_PATH == app_root / "data" / "settings.json"
        assert config.ASSETS_DIR == app_root / "static"

        from backend import api as api_module
        importlib.reload(api_module)
        api = api_module.Api()
        metadata = api.get_active_profile()
        assert metadata["id"] == "abraj"
        assert metadata["warning"] == "Test recovery warning"
        switched = api.switch_profile("eshraqat")
        assert switched["ok"] is False
        assert switched["active_profile"] == "abraj"

        app_source = (app_root / "app.py").read_text(encoding="utf-8")
        fallback_pos = app_source.index('os.environ["DOCUMENTS_HUB_LEGACY_FALLBACK"] = "1"')
        backend_import_pos = app_source.index("from backend import database, migrations")
        assert fallback_pos < backend_import_pos
    finally:
        if old_fallback is None:
            os.environ.pop("DOCUMENTS_HUB_LEGACY_FALLBACK", None)
        else:
            os.environ["DOCUMENTS_HUB_LEGACY_FALLBACK"] = old_fallback
        if old_warning is None:
            os.environ.pop("DOCUMENTS_HUB_PROFILE_WARNING", None)
        else:
            os.environ["DOCUMENTS_HUB_PROFILE_WARNING"] = old_warning
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("profile legacy fallback backend tests passed")


if __name__ == "__main__":
    main()
