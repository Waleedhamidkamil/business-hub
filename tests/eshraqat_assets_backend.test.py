import base64
import hashlib
import importlib
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


LOGO_SHA256 = "4B0758665A0B4F6DCF6D51EE0A2A86D830F489840BCD9CF42523DFCDEF2FAD4D"
ISO_SHA256 = "A29208239818638ECE45342E6FB733FB00EE417BC2A030708811909C320565B3"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def decode_data_uri(value: str) -> bytes:
    return base64.b64decode(value.split(",", 1)[1])


def main() -> None:
    packaged_logo = APP_ROOT / "static" / "brands" / "eshraqat" / "logo.png"
    packaged_iso = APP_ROOT / "static" / "brands" / "eshraqat" / "iso-certifications.png"
    assert sha256(packaged_logo) == LOGO_SHA256
    assert sha256(packaged_iso) == ISO_SHA256

    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "eshraqat-assets-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)
    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        copied = manager.ensure_seed_assets("eshraqat")
        assert set(copied) == {"logo.png", "iso-certifications.png"}

        from backend import brand_assets, config

        importlib.reload(config)
        importlib.reload(brand_assets)
        for options in (
            config.QUOTE_ADDRESS_OPTIONS,
            config.DN_ADDRESS_OPTIONS,
            config.INV_ADDRESS_OPTIONS,
        ):
            joined = " ".join(options).upper()
            assert "CSSP" not in joined
            assert "IBRU" not in joined
        logo_uri = brand_assets.logo_data_uri(required=True)
        iso_uri = brand_assets.iso_data_uri()
        assert logo_uri.startswith("data:image/png;base64,")
        assert iso_uri and iso_uri.startswith("data:image/png;base64,")
        assert hashlib.sha256(decode_data_uri(logo_uri)).hexdigest().upper() == LOGO_SHA256
        assert hashlib.sha256(decode_data_uri(iso_uri)).hexdigest().upper() == ISO_SHA256

        manager.activate("abraj")
        importlib.reload(config)
        importlib.reload(brand_assets)
        assert brand_assets.logo_data_uri() is None
        assert brand_assets.iso_data_uri() is None
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("Eshraqat asset backend tests passed")


if __name__ == "__main__":
    main()
