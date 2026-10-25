import base64
import importlib
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def image_uri(payload: bytes) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def main() -> None:
    original_manager = profile_manager._MANAGER
    test_dir = Path(__file__).resolve().parent
    try:
        with TemporaryDirectory(dir=test_dir) as td:
            root = Path(td)
            manager = ProfileManager(root / "data", root / "static")
            profile_manager._MANAGER = manager
            manager.create_registry("abraj")

            from backend import config
            importlib.reload(config)
            from backend import api as api_module
            from backend import signing
            importlib.reload(signing)
            importlib.reload(api_module)
            api = api_module.Api()

            assert config.ACTIVE_PROFILE == "abraj"
            assert config.DB_PATH == manager.profile_db_path("abraj")
            assert config.SETTINGS_PATH.parent.name == "abraj"
            assert config.ASSETS_DIR.parent.name == "abraj"

            config.save_user_settings({"preferences": {"theme": "dark"}})
            uploaded = api.upload_logo(image_uri(b"abraj-logo"))
            assert uploaded["ok"] is True
            assert (manager.assets_dir("abraj") / "logo.png").read_bytes() == b"abraj-logo"
            assert signing.upload_stamp(image_uri(b"abraj-stamp"))["ok"] is True

            manager.activate("eshraqat")
            importlib.reload(config)
            assert config.ACTIVE_PROFILE == "eshraqat"
            assert config.DOCUMENTS_ROOT == Path(r"D:\ESHRAQAT DOCUMENTS")
            assert config.DB_PATH == manager.profile_db_path("eshraqat")
            assert config.SETTINGS_PATH.parent.name == "eshraqat"
            assert config.ASSETS_DIR.parent.name == "eshraqat"
            assert config.INV_BANK_INFO == [config.CASH_BANK]
            assert config.USER_PREFERENCES["theme"] == "light"
            assert signing.get_settings()["stamp"]["has_image"] is False

            config.save_user_settings({"preferences": {"theme": "light"}})
            uploaded = api.upload_logo(image_uri(b"eshraqat-logo"))
            assert uploaded["ok"] is True
            assert (manager.assets_dir("eshraqat") / "logo.png").read_bytes() == b"eshraqat-logo"
            assert signing.upload_stamp(image_uri(b"eshraqat-stamp"))["ok"] is True

            assert (manager.assets_dir("abraj") / "logo.png").read_bytes() == b"abraj-logo"
            assert (manager.signatures_dir("abraj") / "stamp.png").read_bytes() == b"abraj-stamp"
            assert json.loads(manager.settings_path("abraj").read_text(encoding="utf-8"))[
                "preferences"
            ]["theme"] == "dark"

            metadata = api.get_active_profile()
            assert metadata["id"] == "eshraqat"
            assert metadata["logo_data_uri"].startswith("data:image/png;base64,")
            assert "signatures" not in metadata
            assert "stamp" not in metadata
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("profile config and assets backend tests passed")


if __name__ == "__main__":
    main()
