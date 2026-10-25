import json
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.profile_manager import ProfileManager


def assert_profile_paths(manager: ProfileManager, root: Path) -> None:
    assert manager.profile_dir("abraj") == root / "data" / "profiles" / "abraj"
    assert manager.profile_db_path("abraj") == root / "data" / "profiles" / "abraj" / "po_tool.db"
    assert manager.settings_path("eshraqat") == root / "data" / "profiles" / "eshraqat" / "settings.json"
    assert manager.assets_dir("eshraqat") == root / "data" / "profiles" / "eshraqat" / "assets"
    assert manager.signatures_dir("eshraqat") == root / "data" / "profiles" / "eshraqat" / "assets" / "sigs"
    assert manager.shared_db_path() == root / "data" / "shared" / "library.db"


def assert_registry_and_activation(manager: ProfileManager) -> None:
    manager.create_registry("abraj")
    assert manager.active_profile_id() == "abraj"

    result = manager.activate("eshraqat")
    assert result == {"ok": True, "active_profile": "eshraqat"}
    assert manager.active_profile_id() == "eshraqat"
    assert manager.profile_db_path() == manager.profile_db_path("eshraqat")

    before = manager.registry_path.read_text(encoding="utf-8")
    for invalid in ("unknown", "", 123):
        try:
            manager.activate(invalid)  # type: ignore[arg-type]
        except ValueError as exc:
            assert "Unknown profile" in str(exc)
        else:
            raise AssertionError(f"Unknown profile must be rejected: {invalid!r}")
    assert manager.registry_path.read_text(encoding="utf-8") == before
    assert not manager.registry_path.with_suffix(".json.tmp").exists()


def assert_safe_registry_fallback(manager: ProfileManager) -> None:
    manager.registry_path.unlink()
    assert manager.active_profile_id() == "abraj"

    manager.registry_path.parent.mkdir(parents=True, exist_ok=True)
    manager.registry_path.write_text("{not-json", encoding="utf-8")
    assert manager.registry() == {}
    assert manager.active_profile_id() == "abraj"

    manager.registry_path.write_text(
        json.dumps({"version": 1, "active_profile": "unknown"}),
        encoding="utf-8",
    )
    assert manager.registry() == {}
    assert manager.active_profile_id() == "abraj"

    manager.registry_path.write_text(
        json.dumps({"version": 2, "active_profile": "abraj", "profiles": []}),
        encoding="utf-8",
    )
    assert manager.registry() == {}


def assert_concurrent_writes_are_atomic(manager: ProfileManager) -> None:
    manager.create_registry("abraj")
    peer = ProfileManager(manager.data_root, manager.static_root)
    errors: list[Exception] = []

    def switch_many(instance: ProfileManager, profile_id: str) -> None:
        try:
            for _ in range(25):
                instance.activate(profile_id)
        except Exception as exc:
            errors.append(exc)

    workers = [
        threading.Thread(target=switch_many, args=(manager, "abraj")),
        threading.Thread(target=switch_many, args=(peer, "eshraqat")),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert errors == []
    assert manager.active_profile_id() in manager.VALID_IDS
    assert list(manager.registry_path.parent.glob("profile-registry.json.*.tmp")) == []


def main() -> None:
    with TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
        root = Path(td)
        manager = ProfileManager(root / "data", root / "static")
        assert_profile_paths(manager, root)
        assert_registry_and_activation(manager)
        assert_safe_registry_fallback(manager)
        assert_concurrent_writes_are_atomic(manager)

    print("profile manager backend tests passed")


if __name__ == "__main__":
    main()
