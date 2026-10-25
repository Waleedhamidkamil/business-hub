import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import shared_library
from backend.profile_manager import ProfileManager


# The four item catalogs (items/quote_items/dn_items/inv_items) were dropped
# 2026-08-20 and are no longer in the shared schema, so they are no longer
# migration targets to assert on.
SHARED_TABLES = (
    "companies",
    "clients",
    "column_memory",
)


def seed_legacy_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE quotes (quote_no TEXT PRIMARY KEY, rows_json TEXT NOT NULL);
            INSERT INTO quotes VALUES ('QUT-MT-LEGACY-001', '[]');

            CREATE TABLE items (
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT, unit TEXT, last_company TEXT,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT,
                PRIMARY KEY (name)
            );
            INSERT INTO items VALUES ('Pipe', 'Steel pipe', 'm', 'HDEC', 2, '2026-07-01');

            CREATE TABLE companies (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT
            );
            INSERT INTO companies VALUES ('HDEC', 3, '2026-07-01');

            CREATE TABLE clients (
                name TEXT PRIMARY KEY COLLATE NOCASE,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT
            );
            INSERT INTO clients VALUES ('HDEC', 4, '2026-07-01');

            CREATE TABLE quote_items (
                quote_type TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                data_json TEXT,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT,
                PRIMARY KEY (quote_type, name)
            );
            INSERT INTO quote_items VALUES ('materials', 'Pipe', '{"description":"Steel pipe"}', 1, '2026-07-01');

            CREATE TABLE dn_items (
                name TEXT NOT NULL COLLATE NOCASE,
                data_json TEXT,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT,
                PRIMARY KEY (name)
            );
            INSERT INTO dn_items VALUES ('Pipe', '{"unit":"m"}', 1, '2026-07-01');

            CREATE TABLE inv_items (
                inv_type TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE,
                data_json TEXT,
                use_count INTEGER NOT NULL DEFAULT 0, last_used_at TEXT,
                PRIMARY KEY (inv_type, name)
            );
            INSERT INTO inv_items VALUES ('materials', 'Pipe', '{"rate":"5"}', 1, '2026-07-01');

            CREATE TABLE column_memory (
                doc_type TEXT NOT NULL, sub_type TEXT NOT NULL DEFAULT '',
                col_key TEXT NOT NULL, value TEXT NOT NULL COLLATE NOCASE,
                use_count INTEGER NOT NULL DEFAULT 1, last_used_at TEXT NOT NULL,
                PRIMARY KEY (doc_type, sub_type, col_key, value)
            );
            INSERT INTO column_memory VALUES ('quote', 'materials', 'item', 'Pipe', 1, '2026-07-01');
            """
        )
        conn.commit()
    finally:
        conn.close()


def table_count(path: Path, table: str) -> int:
    conn = sqlite3.connect(path)
    try:
        count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    finally:
        conn.close()
    return count


def seed_legacy_files(root: Path) -> tuple[Path, Path, Path]:
    legacy_db = root / "legacy" / "po_tool.db"
    legacy_settings = root / "legacy" / "settings.json"
    legacy_static = root / "legacy-static"
    seed_legacy_db(legacy_db)
    legacy_settings.write_text('{"preferences":{"theme":"dark"}}', encoding="utf-8")
    legacy_static.mkdir(parents=True, exist_ok=True)
    (legacy_static / "logo.jpg").write_bytes(b"legacy-logo-jpeg")
    (legacy_static / "sigs").mkdir()
    (legacy_static / "sigs" / "stamp.png").write_bytes(b"legacy-stamp")
    return legacy_db, legacy_settings, legacy_static


def assert_successful_idempotent_migration(root: Path) -> None:
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    source_db_bytes = legacy_db.read_bytes()
    manager = ProfileManager(root / "data", root / "static")

    result = manager.bootstrap_profiles(
        legacy_db=legacy_db,
        legacy_settings=legacy_settings,
        legacy_assets=legacy_static,
    )
    assert result["ok"] is True, result
    assert result["already_applied"] is False
    assert manager.active_profile_id() == "abraj"
    assert manager.profile_db_path("abraj").exists()
    assert manager.profile_db_path("eshraqat").exists()
    assert manager.settings_path("abraj").read_bytes() == legacy_settings.read_bytes()
    assert (manager.assets_dir("abraj") / "logo.jpg").read_bytes() == b"legacy-logo-jpeg"
    assert (manager.signatures_dir("abraj") / "stamp.png").read_bytes() == b"legacy-stamp"
    assert table_count(manager.profile_db_path("abraj"), "quotes") == 1
    for table in SHARED_TABLES:
        assert table_count(manager.shared_db_path(), table) == 1, table
    # The four item catalogs were dropped 2026-08-20; the data_sig backfill they
    # used to receive during migration no longer applies.

    assert legacy_db.exists()
    assert legacy_db.read_bytes() == source_db_bytes
    assert legacy_settings.exists()
    assert (legacy_static / "logo.jpg").exists()

    again = manager.bootstrap_profiles(
        legacy_db=legacy_db,
        legacy_settings=legacy_settings,
        legacy_assets=legacy_static,
    )
    assert again["ok"] is True
    assert again["already_applied"] is True
    assert table_count(manager.profile_db_path("abraj"), "quotes") == 1
    assert table_count(manager.shared_db_path(), "clients") == 1

    conn = sqlite3.connect(manager.shared_db_path())
    try:
        conn.execute("DELETE FROM clients")
        conn.commit()
    finally:
        conn.close()
    logical_loss = manager.bootstrap_profiles(
        legacy_db=legacy_db,
        legacy_settings=legacy_settings,
        legacy_assets=legacy_static,
    )
    assert logical_loss["ok"] is False
    assert "logical data loss" in logical_loss["error"].lower()

    manager.shared_db_path().write_bytes(b"corrupt")
    corrupt = manager.bootstrap_profiles(
        legacy_db=legacy_db,
        legacy_settings=legacy_settings,
        legacy_assets=legacy_static,
    )
    assert corrupt["ok"] is False
    assert "integrity" in corrupt["error"].lower()


def assert_retired_catalog_drop_is_not_data_loss(root: Path) -> None:
    """After migration, dropping the four retired catalogs from the profile DB
    (and a stray shared-store `master_items` sitting in the legacy file) must NOT
    trip the integrity check. This is exactly the state a real machine reached on
    2026-08-20 when the catalogs were dropped - bootstrap must still succeed."""
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    manager = ProfileManager(root / "data", root / "static")

    first = manager.bootstrap_profiles(
        legacy_db=legacy_db, legacy_settings=legacy_settings, legacy_assets=legacy_static,
    )
    assert first["ok"] is True, first

    # Drop the retired catalogs from the Abraj profile DB, as the real retirement did.
    conn = sqlite3.connect(manager.profile_db_path("abraj"))
    try:
        for table in ("items", "quote_items", "dn_items", "inv_items"):
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.commit()
    finally:
        conn.close()
    # A stray shared-store table only ever appears in the legacy file (dev scripts).
    conn = sqlite3.connect(legacy_db)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS master_items (item_id TEXT PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    again = manager.bootstrap_profiles(
        legacy_db=legacy_db, legacy_settings=legacy_settings, legacy_assets=legacy_static,
    )
    assert again["ok"] is True, again
    assert again["already_applied"] is True


def assert_failed_verification_does_not_activate(root: Path) -> None:
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    manager = ProfileManager(root / "data", root / "static")
    real_copy_catalogs = shared_library.copy_catalogs

    def copy_with_mismatch(source_db: Path, target_db: Path) -> dict[str, int]:
        counts = real_copy_catalogs(source_db, target_db)
        conn = sqlite3.connect(target_db)
        try:
            conn.execute("DELETE FROM clients")
            conn.commit()
        finally:
            conn.close()
        return counts

    shared_library.copy_catalogs = copy_with_mismatch
    try:
        result = manager.bootstrap_profiles(
            legacy_db=legacy_db,
            legacy_settings=legacy_settings,
            legacy_assets=legacy_static,
        )
    finally:
        shared_library.copy_catalogs = real_copy_catalogs

    assert result["ok"] is False
    assert "count mismatch" in result["error"].lower()
    assert not manager.registry_path.exists()
    assert not manager.profile_dir("abraj").exists()
    assert not manager.profile_dir("eshraqat").exists()
    assert not manager.shared_db_path().exists()


def assert_active_legacy_writer_blocks_migration(root: Path) -> None:
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    manager = ProfileManager(root / "data", root / "static")
    writer = sqlite3.connect(legacy_db)
    try:
        writer.execute("BEGIN IMMEDIATE")
        result = manager.bootstrap_profiles(
            legacy_db=legacy_db,
            legacy_settings=legacy_settings,
            legacy_assets=legacy_static,
        )
    finally:
        writer.rollback()
        writer.close()
    assert result["ok"] is False
    assert not manager.registry_path.exists()


def assert_interrupted_publication_recovers(root: Path) -> None:
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    manager = ProfileManager(root / "data", root / "static")
    abandoned_stage = root / "data" / "profiles" / ".staging-abandoned"
    abandoned_stage.mkdir(parents=True)
    manager.profile_dir("abraj").mkdir(parents=True)
    (manager.profile_dir("abraj") / "partial.txt").write_text("partial", encoding="utf-8")
    manager.migration_pending_path.parent.mkdir(parents=True, exist_ok=True)
    manager.migration_pending_path.write_text(
        '{"version":1,"stage_root":"' + str(abandoned_stage).replace("\\", "\\\\") + '"}',
        encoding="utf-8",
    )

    result = manager.bootstrap_profiles(
        legacy_db=legacy_db,
        legacy_settings=legacy_settings,
        legacy_assets=legacy_static,
    )
    assert result["ok"] is True, result
    assert not manager.migration_pending_path.exists()
    assert not (manager.profile_dir("abraj") / "partial.txt").exists()
    assert table_count(manager.profile_db_path("abraj"), "quotes") == 1


def assert_overlapping_bootstraps_serialize(root: Path) -> None:
    legacy_db, legacy_settings, legacy_static = seed_legacy_files(root)
    managers = [
        ProfileManager(root / "data", root / "static"),
        ProfileManager(root / "data", root / "static"),
    ]

    def run(manager: ProfileManager) -> dict:
        return manager.bootstrap_profiles(
            legacy_db=legacy_db,
            legacy_settings=legacy_settings,
            legacy_assets=legacy_static,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, managers))
    assert all(result["ok"] is True for result in results), results
    assert sorted(result["already_applied"] for result in results) == [False, True]
    assert table_count(managers[0].profile_db_path("abraj"), "quotes") == 1


def main() -> None:
    test_dir = Path(__file__).resolve().parent
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_successful_idempotent_migration(Path(td))
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_retired_catalog_drop_is_not_data_loss(Path(td))
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_failed_verification_does_not_activate(Path(td))
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_active_legacy_writer_blocks_migration(Path(td))
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_interrupted_publication_recovers(Path(td))
    with TemporaryDirectory(dir=test_dir, ignore_cleanup_errors=True) as td:
        assert_overlapping_bootstraps_serialize(Path(td))
    print("profile migration backend tests passed")


if __name__ == "__main__":
    main()
