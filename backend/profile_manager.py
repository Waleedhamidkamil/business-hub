"""Company-profile registry and profile-owned filesystem paths."""

from __future__ import annotations

from . import sqlite_policy

import base64
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_REGISTRY_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCKS_GUARD = threading.Lock()
_MIGRATION_LOCKS: dict[str, threading.Lock] = {}
_MIGRATION_LOCKS_GUARD = threading.Lock()


def _registry_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _REGISTRY_LOCKS_GUARD:
        return _REGISTRY_LOCKS.setdefault(key, threading.RLock())


def _migration_thread_lock(path: Path) -> threading.Lock:
    key = str(path.resolve()).casefold()
    with _MIGRATION_LOCKS_GUARD:
        return _MIGRATION_LOCKS.setdefault(key, threading.Lock())


class ProfileManager:
    """Own the active company selection without loading company data."""

    VALID_IDS = ("abraj", "eshraqat")
    PROFILE_LABELS = {
        "abraj": "Abraj Albadr",
        "eshraqat": "Eshraqat Albadr",
    }
    # Brand colour per company. config.PROFILE_ACCENT reads it from here, and
    # so does the launch gate, which runs before config can be imported.
    PROFILE_ACCENTS = {
        "abraj": "#1F3864",
        "eshraqat": "#861432",
    }

    def __init__(self, data_root: Path, static_root: Path):
        self.data_root = Path(data_root)
        self.static_root = Path(static_root)
        self.registry_path = self.data_root / "profile-registry.json"
        self.migration_marker_path = self.data_root / "profile-migration-v1.json"
        self.migration_pending_path = self.data_root / "profile-migration-pending.json"
        self.migration_lock_path = self.data_root / "profile-migration.lock"
        self._registry_lock = _registry_lock(self.registry_path)
        self._migration_thread_lock = _migration_thread_lock(self.migration_lock_path)

    def _profile_id(self, profile_id: str | None = None) -> str:
        value = self.active_profile_id() if profile_id is None else profile_id
        if not isinstance(value, str) or value not in self.VALID_IDS:
            raise ValueError(f"Unknown profile: {value}")
        return value

    def _write_registry(self, payload: dict[str, Any]) -> None:
        with self._registry_lock:
            self._write_json_atomic(self.registry_path, payload)

    @contextmanager
    def _migration_lock(self, timeout: float = 30.0):
        if not self._migration_thread_lock.acquire(timeout=timeout):
            raise TimeoutError("Another app instance is still preparing company profiles")
        handle = None
        locked = False
        try:
            self.migration_lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.migration_lock_path.open("a+b")
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            if __import__("os").name == "nt":
                import msvcrt

                while True:
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        locked = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Another app instance is still preparing company profiles")
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
            yield
        finally:
            if handle is not None:
                if locked:
                    try:
                        handle.seek(0)
                        if __import__("os").name == "nt":
                            import msvcrt
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()
            self._migration_thread_lock.release()

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary_path.write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def create_registry(self, active: str = "abraj") -> None:
        active = self._profile_id(active)
        self._write_registry({
            "version": 1,
            "active_profile": active,
            "profiles": [
                {"id": profile_id, "label": self.PROFILE_LABELS[profile_id]}
                for profile_id in self.VALID_IDS
            ],
        })

    def registry(self) -> dict[str, Any]:
        with self._registry_lock:
            try:
                raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return {}
        if not isinstance(raw, dict):
            return {}
        if raw.get("version") != 1:
            return {}
        if raw.get("active_profile") not in self.VALID_IDS:
            return {}
        profiles = raw.get("profiles")
        if not isinstance(profiles, list) or len(profiles) != len(self.VALID_IDS):
            return {}
        if any(
            not isinstance(profile, dict)
            or profile.get("id") not in self.VALID_IDS
            or not isinstance(profile.get("label"), str)
            or not profile["label"].strip()
            for profile in profiles
        ):
            return {}
        if {profile["id"] for profile in profiles} != set(self.VALID_IDS):
            return {}
        return raw

    def active_profile_id(self) -> str:
        return str(self.registry().get("active_profile", "abraj"))

    def activate(self, profile_id: str) -> dict[str, Any]:
        profile_id = self._profile_id(profile_id)
        with self._registry_lock:
            payload = self.registry()
            if not payload:
                self.create_registry(profile_id)
            else:
                payload["active_profile"] = profile_id
                self._write_registry(payload)
        return {"ok": True, "active_profile": profile_id}

    def profile_dir(self, profile_id: str | None = None) -> Path:
        return self.data_root / "profiles" / self._profile_id(profile_id)

    def profile_db_path(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "po_tool.db"

    def settings_path(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "settings.json"

    def assets_dir(self, profile_id: str | None = None) -> Path:
        return self.profile_dir(profile_id) / "assets"

    def signatures_dir(self, profile_id: str | None = None) -> Path:
        return self.assets_dir(profile_id) / "sigs"

    def ensure_seed_assets(self, profile_id: str = "eshraqat") -> list[str]:
        """Copy packaged company assets once without replacing user-owned files."""
        profile_id = self._profile_id(profile_id)
        if profile_id != "eshraqat":
            return []
        source = self.static_root / "brands" / "eshraqat"
        target = self.assets_dir(profile_id)
        copied: list[str] = []
        for name in ("logo.png", "iso-certifications.png"):
            source_path = source / name
            target_path = target / name
            if source_path.is_file() and not target_path.exists():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
                copied.append(name)
        self.signatures_dir(profile_id).mkdir(parents=True, exist_ok=True)
        return copied

    def shared_db_path(self) -> Path:
        return self.data_root / "shared" / "library.db"

    def list_profiles(self) -> list[dict[str, str]]:
        return [
            {"id": profile_id, "label": self.PROFILE_LABELS[profile_id]}
            for profile_id in self.VALID_IDS
        ]

    def logo_data_uri(self, profile_id: str | None = None) -> str | None:
        for name, mime in (
            ("logo.png", "image/png"),
            ("logo.jpg", "image/jpeg"),
            ("logo.jpeg", "image/jpeg"),
        ):
            path = self.assets_dir(profile_id) / name
            if path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{encoded}"
        return None

    def validate_profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = self._profile_id(profile_id)
        database_path = self.profile_db_path(profile_id)
        if not database_path.is_file():
            raise RuntimeError(f"Profile database is missing: {profile_id}")
        self._quick_check(database_path)

        settings: dict[str, Any] = {}
        settings_path = self.settings_path(profile_id)
        if settings_path.is_file():
            try:
                parsed = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(f"Profile settings are unreadable: {profile_id}") from exc
            if not isinstance(parsed, dict):
                raise RuntimeError(f"Profile settings must be a JSON object: {profile_id}")
            settings = parsed

        if not self.assets_dir(profile_id).is_dir():
            raise RuntimeError(f"Profile assets directory is missing: {profile_id}")
        default_root = Path(r"D:\PO TOOL") if profile_id == "abraj" else Path(r"D:\ESHRAQAT DOCUMENTS")
        documents_root = Path(settings.get("documents_root") or default_root)
        if not documents_root.parent.exists():
            raise RuntimeError(f"Documents drive or parent folder is unavailable: {documents_root.parent}")
        return {
            "id": profile_id,
            "database": database_path,
            "documents_root": documents_root,
        }

    @staticmethod
    def _database_counts_connection(conn: sqlite3.Connection) -> dict[str, int]:
        tables = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return {
            str(row[0]): int(conn.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0])
            for row in tables
        }

    @classmethod
    def _database_counts(cls, path: Path) -> dict[str, int]:
        conn = sqlite_policy.open_db(path, wal=False, row_factory=False)
        try:
            return cls._database_counts_connection(conn)
        finally:
            conn.close()

    @staticmethod
    def _copy_database(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite_policy.open_db(source, wal=False, row_factory=False)
        target_conn = sqlite_policy.open_db(target, wal=False, row_factory=False)
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
            source_conn.close()

    @staticmethod
    def _backup_connection(source_conn: sqlite3.Connection, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target_conn = sqlite_policy.open_db(target, wal=False, row_factory=False)
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()

    @staticmethod
    def _quick_check(path: Path) -> None:
        # The FILE must start like a database. quick_check alone can pass a
        # damaged file: while any connection still has the database open - in
        # this process (sqlite3's `with conn:` commits but never closes) or in
        # another person's engine on the office server - SQLite reads page 1
        # from the -wal journal instead (2026-10-23,
        # tests/profile_switch_backend.test.py). An empty file is still
        # SQLite's "new, empty database", as before.
        try:
            with open(path, "rb") as f:
                head = f.read(16)
        except OSError as exc:
            raise RuntimeError(f"SQLite database unreadable: {path}: {exc}") from exc
        if head and head != b"SQLite format 3\x00":
            raise RuntimeError(f"SQLite integrity failure for {path}: not an SQLite database (the file is damaged)")
        conn = sqlite_policy.open_db(path, wal=False, row_factory=False)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise RuntimeError(f"SQLite quick_check failed for {path}")
        except sqlite3.Error as exc:
            raise RuntimeError(f"SQLite integrity failure for {path}: {exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _create_empty_database(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite_policy.open_db(path, wal=False, row_factory=False)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()

    @staticmethod
    def _copy_legacy_assets(source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for name in ("logo.png", "logo.jpg", "logo.jpeg"):
            logo = source / name
            if logo.is_file():
                shutil.copy2(logo, target / name)
        signatures = source / "sigs"
        if signatures.is_dir():
            shutil.copytree(signatures, target / "sigs", dirs_exist_ok=True)
        else:
            (target / "sigs").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _remove_tree_checked(path: Path) -> None:
        if not path.exists():
            return
        shutil.rmtree(path)
        if path.exists():
            raise RuntimeError(f"Could not remove incomplete migration path: {path}")

    def _recover_interrupted_migration(self) -> None:
        if not self.migration_pending_path.is_file():
            return
        try:
            pending = json.loads(self.migration_pending_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Unreadable profile migration recovery journal") from exc
        if not isinstance(pending, dict) or pending.get("version") != 1:
            raise RuntimeError("Unsupported profile migration recovery journal")

        stage_root = Path(str(pending.get("stage_root") or ""))
        profiles_root = (self.data_root / "profiles").resolve()
        try:
            stage_root.resolve().relative_to(profiles_root)
        except (OSError, ValueError) as exc:
            raise RuntimeError("Unsafe profile migration recovery path") from exc
        if not stage_root.name.startswith(".staging-"):
            raise RuntimeError("Unsafe profile migration staging directory")

        if not self.migration_marker_path.is_file():
            self._remove_tree_checked(self.profile_dir("abraj"))
            self._remove_tree_checked(self.profile_dir("eshraqat"))
            self._remove_tree_checked(self.shared_db_path().parent)
        self._remove_tree_checked(stage_root)
        self.migration_pending_path.unlink()

    def _validate_completed_migration(self, legacy_db: Path) -> None:
        try:
            marker = json.loads(self.migration_marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("Profile migration integrity marker is unreadable") from exc
        if not isinstance(marker, dict) or marker.get("version") != 1:
            raise RuntimeError("Profile migration integrity marker has an unsupported version")

        abraj_db = self.profile_db_path("abraj")
        eshraqat_db = self.profile_db_path("eshraqat")
        shared_db = self.shared_db_path()
        required = (abraj_db, eshraqat_db, shared_db)
        if not all(path.is_file() for path in required):
            raise RuntimeError("Profile migration integrity check found incomplete profile data")
        for path in required:
            self._quick_check(path)

        from . import shared_library

        shared_counts = self._database_counts(shared_db)
        shared_tables = set(shared_counts)
        if not set(shared_library.SHARED_TABLES).issubset(shared_tables):
            raise RuntimeError("Profile migration integrity check found an incomplete shared catalog")

        marker_changed = False
        catalog_baseline = marker.get("catalog_baseline_counts")
        if not isinstance(catalog_baseline, dict):
            marker["catalog_baseline_counts"] = {
                table: shared_counts.get(table, 0)
                for table in shared_library.SHARED_TABLES
            }
            catalog_baseline = marker["catalog_baseline_counts"]
            marker_changed = True
        for table in shared_library.SHARED_TABLES:
            baseline = int(catalog_baseline.get(table, 0) or 0)
            if baseline > 0 and shared_counts.get(table, 0) == 0:
                raise RuntimeError(
                    f"Profile migration integrity check detected logical data loss in shared {table}"
                )

        expected_abraj_tables = marker.get("abraj_tables")
        if not isinstance(expected_abraj_tables, list) and legacy_db.is_file():
            expected_abraj_tables = list(self._database_counts(legacy_db))
        if isinstance(expected_abraj_tables, list):
            actual_abraj_tables = set(self._database_counts(abraj_db))
            # Tables that a profile DB is not expected to hold, so their absence
            # must not read as a lost migration:
            #  - the four document catalogs, deliberately dropped on 2026-08-20
            #    (the item record `master_items` is the store now);
            #  - `master_items` itself, a shared-DB record store that only ever
            #    appears in the legacy file because dev scripts created it there.
            not_profile_tables = set(shared_library.RETIRED_CATALOGS) | {"master_items"}
            expected = set(map(str, expected_abraj_tables)) - not_profile_tables
            if not expected.issubset(actual_abraj_tables):
                raise RuntimeError("Profile migration integrity check found an incomplete Abraj database")

        document_tables = {
            "pos": "po",
            "quotes": "quote",
            "dns": "dn",
            "invoices": "inv",
        }
        abraj_counts = self._database_counts(abraj_db)
        document_baseline = marker.get("document_baseline_counts")
        if not isinstance(document_baseline, dict):
            marker["document_baseline_counts"] = {
                table: abraj_counts.get(table, 0)
                for table in document_tables
            }
            document_baseline = marker["document_baseline_counts"]
            marker_changed = True
        conn = sqlite_policy.open_db(abraj_db, wal=False, row_factory=False)
        try:
            has_tombstones = "deleted_docs" in abraj_counts
            for table, kind in document_tables.items():
                baseline = int(document_baseline.get(table, 0) or 0)
                consumed = abraj_counts.get(table, 0)
                if has_tombstones:
                    consumed += int(conn.execute(
                        "SELECT COUNT(*) FROM deleted_docs WHERE kind = ?",
                        (kind,),
                    ).fetchone()[0])
                if consumed < baseline:
                    raise RuntimeError(
                        f"Profile migration integrity check detected logical data loss in Abraj {table}"
                    )
        finally:
            conn.close()

        if marker_changed:
            self._write_json_atomic(self.migration_marker_path, marker)

    def bootstrap_profiles(
        self,
        legacy_db: Path,
        legacy_settings: Path,
        legacy_assets: Path,
    ) -> dict[str, Any]:
        try:
            with self._migration_lock():
                return self._bootstrap_profiles_locked(
                    legacy_db,
                    legacy_settings,
                    legacy_assets,
                )
        except Exception as exc:
            return {
                "ok": False,
                "already_applied": False,
                "error": str(exc),
            }

    def _bootstrap_profiles_locked(
        self,
        legacy_db: Path,
        legacy_settings: Path,
        legacy_assets: Path,
    ) -> dict[str, Any]:
        """Migrate legacy Abraj data once, activating only verified results."""

        from . import shared_library

        legacy_db = Path(legacy_db)
        legacy_settings = Path(legacy_settings)
        legacy_assets = Path(legacy_assets)
        final_abraj = self.profile_dir("abraj")
        final_eshraqat = self.profile_dir("eshraqat")
        final_shared = self.shared_db_path().parent

        try:
            self._recover_interrupted_migration()
        except Exception as exc:
            return {
                "ok": False,
                "already_applied": False,
                "error": str(exc),
            }

        if self.migration_marker_path.is_file():
            try:
                self._validate_completed_migration(legacy_db)
            except Exception as exc:
                return {
                    "ok": False,
                    "already_applied": False,
                    "error": str(exc),
                }
            if not self.registry():
                self.create_registry("abraj")
            self.ensure_seed_assets("eshraqat")
            return {"ok": True, "already_applied": True}

        occupied = [path for path in (final_abraj, final_eshraqat, final_shared) if path.exists()]
        if occupied:
            return {
                "ok": False,
                "already_applied": False,
                "error": f"Profile migration destination already exists: {occupied[0]}",
            }

        stage_root = self.data_root / "profiles" / f".staging-{uuid.uuid4().hex}"
        staged_abraj = stage_root / "abraj"
        staged_eshraqat = stage_root / "eshraqat"
        staged_shared = stage_root / "shared"
        moved_paths: list[Path] = []
        legacy_lock: sqlite3.Connection | None = None

        try:
            stage_root.mkdir(parents=True, exist_ok=False)
            self._write_json_atomic(self.migration_pending_path, {
                "version": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "stage_root": str(stage_root),
            })
            staged_abraj_db = staged_abraj / "po_tool.db"
            source_counts: dict[str, int] = {}
            if legacy_db.is_file():
                legacy_lock = sqlite_policy.open_db(legacy_db, wal=False, busy_ms=250,
                                                    row_factory=False)
                legacy_lock.execute("BEGIN IMMEDIATE")
                source_counts = self._database_counts_connection(legacy_lock)
                self._copy_database(legacy_db, staged_abraj_db)
                if source_counts != self._database_counts(staged_abraj_db):
                    raise RuntimeError("Legacy database count mismatch after profile copy")
            else:
                self._create_empty_database(staged_abraj_db)

            if legacy_settings.is_file():
                staged_abraj.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_settings, staged_abraj / "settings.json")
            self._copy_legacy_assets(legacy_assets, staged_abraj / "assets")

            self._create_empty_database(staged_eshraqat / "po_tool.db")
            (staged_eshraqat / "assets" / "sigs").mkdir(parents=True, exist_ok=True)

            staged_shared_db = staged_shared / "library.db"
            source_catalog_counts = shared_library.copy_catalogs(
                staged_abraj_db,
                staged_shared_db,
            )
            target_catalog_counts = {
                table: self._database_counts(staged_shared_db).get(table, 0)
                for table in shared_library.SHARED_TABLES
            }
            if source_catalog_counts != target_catalog_counts:
                raise RuntimeError("Shared catalog count mismatch after profile copy")

            final_abraj.parent.mkdir(parents=True, exist_ok=True)
            staged_abraj.replace(final_abraj)
            moved_paths.append(final_abraj)
            staged_eshraqat.replace(final_eshraqat)
            moved_paths.append(final_eshraqat)
            self.ensure_seed_assets("eshraqat")
            final_shared.parent.mkdir(parents=True, exist_ok=True)
            staged_shared.replace(final_shared)
            moved_paths.append(final_shared)

            marker = {
                "version": 1,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "legacy_db": str(legacy_db),
                "legacy_db_size": legacy_db.stat().st_size if legacy_db.is_file() else 0,
                "abraj_tables": sorted(source_counts),
                "document_baseline_counts": {
                    table: source_counts.get(table, 0)
                    for table in ("pos", "quotes", "dns", "invoices")
                },
                "catalog_baseline_counts": dict(source_catalog_counts),
            }
            self._write_json_atomic(self.migration_marker_path, marker)
            self.migration_pending_path.unlink()
            self._remove_tree_checked(stage_root)
            self.create_registry("abraj")
            return {"ok": True, "already_applied": False}
        except Exception as exc:
            cleanup_errors = []
            for path in reversed(moved_paths):
                try:
                    self._remove_tree_checked(path)
                except Exception as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            try:
                self._remove_tree_checked(stage_root)
            except Exception as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
            try:
                if self.migration_marker_path.exists():
                    self.migration_marker_path.unlink()
            except OSError as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
            if not cleanup_errors:
                try:
                    if self.migration_pending_path.exists():
                        self.migration_pending_path.unlink()
                except OSError as cleanup_exc:
                    cleanup_errors.append(str(cleanup_exc))
            error = str(exc)
            if cleanup_errors:
                error += "; cleanup pending: " + "; ".join(cleanup_errors)
            return {
                "ok": False,
                "already_applied": False,
                "error": error,
            }
        finally:
            if legacy_lock is not None:
                try:
                    legacy_lock.rollback()
                finally:
                    legacy_lock.close()


_APP_DIR = Path(__file__).resolve().parent.parent
_MANAGER = ProfileManager(_APP_DIR / "data", _APP_DIR / "static")


def legacy_fallback_enabled() -> bool:
    return os.environ.get("DOCUMENTS_HUB_LEGACY_FALLBACK", "").strip() == "1"


def manager() -> ProfileManager:
    return _MANAGER


def active_profile_id() -> str:
    if legacy_fallback_enabled():
        return "abraj"
    return _MANAGER.active_profile_id()


def profile_dir(profile_id: str | None = None) -> Path:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        return _APP_DIR / "data"
    return _MANAGER.profile_dir(profile_id)


def profile_db_path(profile_id: str | None = None) -> Path:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        return _APP_DIR / "data" / "po_tool.db"
    return _MANAGER.profile_db_path(profile_id)


def settings_path(profile_id: str | None = None) -> Path:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        return _APP_DIR / "data" / "settings.json"
    return _MANAGER.settings_path(profile_id)


def assets_dir(profile_id: str | None = None) -> Path:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        return _APP_DIR / "static"
    return _MANAGER.assets_dir(profile_id)


def signatures_dir(profile_id: str | None = None) -> Path:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        return _APP_DIR / "static" / "sigs"
    return _MANAGER.signatures_dir(profile_id)


def shared_db_path() -> Path:
    if legacy_fallback_enabled():
        return _APP_DIR / "data" / "po_tool.db"
    return _MANAGER.shared_db_path()


def list_profiles() -> list[dict[str, str]]:
    return _MANAGER.list_profiles()


def logo_data_uri(profile_id: str | None = None) -> str | None:
    if legacy_fallback_enabled() and profile_id in (None, "abraj"):
        for name, mime in (("logo.png", "image/png"), ("logo.jpg", "image/jpeg"), ("logo.jpeg", "image/jpeg")):
            path = _APP_DIR / "static" / name
            if path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{encoded}"
        return None
    return _MANAGER.logo_data_uri(profile_id)


# The screens show the company logo small (dashboard, company pill, gate seal,
# Settings preview), but the bridge used to send the ORIGINAL file - 650 KB of
# base64 for Abraj, three times at every start (1.95 MB measured 2026-10-02).
# This is the same picture at display size, made once per logo change and kept
# under %LOCALAPPDATA%. PDFs never use it; they read the original file.
UI_LOGO_MAX_PX = 480


def logo_ui_data_uri(profile_id: str | None = None) -> str | None:
    """The company logo at display size, as a data URI. Falls back to the
    original logo if anything about the small copy fails."""
    try:
        if legacy_fallback_enabled() and profile_id in (None, "abraj"):
            folder = _APP_DIR / "static"
        else:
            folder = _MANAGER.assets_dir(profile_id)
        source = next((folder / n for n in ("logo.png", "logo.jpg", "logo.jpeg")
                       if (folder / n).is_file()), None)
        if source is None:
            return None
        st = source.stat()
        import hashlib
        key = hashlib.sha1(f"{source.resolve()}|{st.st_mtime_ns}|{st.st_size}|{UI_LOGO_MAX_PX}"
                           .encode("utf-8")).hexdigest()[:16]
        root = Path(os.environ.get("LOCALAPPDATA", "").strip() or (_APP_DIR / "data"))
        cached = root / "BusinessHub" / "ui-logos" / f"{key}.webp"
        if not cached.is_file():
            from io import BytesIO
            from PIL import Image
            with Image.open(source) as im:
                im = im.convert("RGBA")
                im.thumbnail((UI_LOGO_MAX_PX, UI_LOGO_MAX_PX), Image.LANCZOS)
                buf = BytesIO()
                im.save(buf, "WEBP", quality=90, method=4)
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            os.replace(tmp, cached)
        return "data:image/webp;base64," + base64.b64encode(cached.read_bytes()).decode("ascii")
    except Exception:
        return logo_data_uri(profile_id)


def activate_profile(profile_id: str) -> dict[str, Any]:
    return _MANAGER.activate(profile_id)
