"""Renaming and archiving a shared client leaves every profile's documents alone.

This file used to prove that a rename rewrote `client_name` on every saved row
in every profile ATOMICALLY -- roll the whole thing back if one profile refused.
That machinery is gone: renaming an identity no longer writes to any profile
database, so there is nothing to make atomic. The guarantee that replaced it is
stronger, and is what this file proves now:

  * a rename touches no saved document, in any profile;
  * the previous spelling survives as an alias, so history still groups;
  * archiving hides a client from suggestions and deletes nothing;
  * renaming onto a name another client owns is REFUSED, never silently merged.
"""
import gc
import importlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def _seed_documents(db_path: Path, prefix: str, client_name: str) -> None:
    now = "2026-07-14T12:00:00"
    rows = json.dumps([])
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO quotes(quote_no, created_at, updated_at, quote_date, quote_type, "
            "year, month, client_name, pdf_path, rows_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{prefix}-Q", now, now, "14/7/2026", "materials", 2026, 7,
             client_name, f"{prefix.lower()}-q.pdf", rows),
        )
        conn.execute(
            "INSERT INTO invoices(inv_no, created_at, updated_at, inv_date, inv_type, "
            "year, month, client_name, withholding_included, grand_total, pdf_path, rows_json) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{prefix}-I", now, now, "14/7/2026", "materials", 2026, 7,
             client_name, 1, 100, f"{prefix.lower()}-i.pdf", rows),
        )
        conn.execute(
            "INSERT INTO dns(dn_no, created_at, updated_at, dn_date, year, month, client_name, "
            "include_prices, pdf_path, rows_json) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{prefix}-D", now, now, "14/7/2026", 2026, 7, client_name, 0,
             f"{prefix.lower()}-d.pdf", rows),
        )


def _client_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        names: set[str] = set()
        for table in ("quotes", "invoices", "dns"):
            names.update(row[0] for row in conn.execute(f"SELECT client_name FROM {table}"))
        return names


def _catalog_rows(shared_path: Path) -> dict[str, int]:
    with sqlite3.connect(shared_path) as conn:
        return {
            row[0]: int(row[1] or 0)
            for row in conn.execute("SELECT name, use_count FROM clients")
        }


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "client-rename-isolation-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)

    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("abraj")

        from backend import client_master, clients, config, database

        importlib.reload(config)
        importlib.reload(database)
        importlib.reload(clients)
        database.init_db()

        # A newly-created profile may have an empty SQLite file and no document
        # tables yet. It must not block a shared catalog rename.
        clean_db = manager.profile_db_path("eshraqat")
        clean_db.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(clean_db).close()
        assert clients.add_client("Clean Old")["ok"] is True
        clean_result = clients.rename_client("Clean Old", "Clean New")
        assert clean_result["ok"] is True
        assert "Clean New" in _catalog_rows(manager.shared_db_path())

        # Initialize both profile schemas and give each company its own history
        # for the same shared client.
        database.init_db()
        _seed_documents(manager.profile_db_path("abraj"), "ABRAJ", "Legacy Client")

        manager.activate("eshraqat")
        importlib.reload(config)
        database.init_db()
        _seed_documents(manager.profile_db_path("eshraqat"), "ESH", "Legacy Client")

        manager.activate("abraj")
        importlib.reload(config)
        assert clients.add_client("Legacy Client")["ok"] is True
        renamed = clients.rename_client("Legacy Client", "Renamed Client")
        assert renamed["ok"] is True

        # Not one saved row moved, in either profile.
        assert _client_names(manager.profile_db_path("abraj")) == {"Legacy Client"}
        assert _client_names(manager.profile_db_path("eshraqat")) == {"Legacy Client"}
        # Autocomplete offers the new name only -- one entry per identity, not
        # one per spelling it has ever had. (The legacy `clients` table keeps
        # the old row as a compatibility index with its use_count; it is not
        # what the dropdown reads, and _init must not re-import it as a second
        # identity now that an alias owns the spelling.)
        suggested = {r["name"] for r in database.suggest_clients("")}
        assert "Renamed Client" in suggested
        assert "Legacy Client" not in suggested
        assert "Renamed Client" in _catalog_rows(manager.shared_db_path())
        assert client_master.normalize("Legacy Client") in {
            client_master.normalize(a) for a in client_master.aliases_for("Renamed Client")}

        # Both spellings reach the same history, and it stays scoped to the
        # active profile.
        for asked in ("Legacy Client", "Renamed Client"):
            abraj_profile = clients.client_profile(asked)
            assert abraj_profile["name"] == "Renamed Client"
            assert abraj_profile["summary"]["quotes"] == 1, asked
            assert abraj_profile["summary"]["invoices"] == 1, asked
            assert abraj_profile["summary"]["dns"] == 1, asked
        manager.activate("eshraqat")
        importlib.reload(config)
        for asked in ("Legacy Client", "Renamed Client"):
            eshraqat_profile = clients.client_profile(asked)
            assert eshraqat_profile["summary"]["quotes"] == 1, asked
            assert eshraqat_profile["summary"]["invoices"] == 1, asked
            assert eshraqat_profile["summary"]["dns"] == 1, asked
        manager.activate("abraj")
        importlib.reload(config)

        # A name another client already owns is refused. Merging identities is
        # an explicit operation, never a side effect of typing a name.
        _seed_documents(manager.profile_db_path("abraj"), "AB-M", "Merge Old")
        _seed_documents(manager.profile_db_path("eshraqat"), "ES-M", "Merge Old")
        assert clients.add_client("Merge Old")["ok"] is True
        assert clients.add_client("Merge Target")["ok"] is True
        merged = clients.rename_client("Merge Old", "Merge Target")
        assert merged["ok"] is False
        assert "Merge Target" in merged["error"]
        catalog = _catalog_rows(manager.shared_db_path())
        assert "Merge Old" in catalog and "Merge Target" in catalog
        assert "Merge Old" in _client_names(manager.profile_db_path("abraj"))
        assert "Merge Old" in _client_names(manager.profile_db_path("eshraqat"))

        # Archiving hides a client from suggestions and deletes nothing.
        archived = clients.delete_client("Merge Old")
        assert archived["ok"] is True and archived["archived"] is True
        assert archived["deleted"] == 0
        assert "Merge Old" not in {r["name"] for r in database.suggest_clients("")}
        assert "Merge Old" in _client_names(manager.profile_db_path("abraj"))
        assert "Merge Old" in _client_names(manager.profile_db_path("eshraqat"))
        assert clients.client_profile("Merge Old")["summary"]["invoices"] == 1
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])
        gc.collect()

    print("client rename isolation backend tests passed")


if __name__ == "__main__":
    main()
