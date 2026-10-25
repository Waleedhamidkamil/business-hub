"""A rename is cross-profile safe BECAUSE it rewrites no profile at all.

The old rename walked every profile database and rewrote `client_name` on each
saved row, so it needed cross-profile atomicity to avoid half-renaming a client.
It no longer touches them: the identity is renamed once in the shared library
and the previous spelling is kept as an alias. What has to be proven now is the
opposite of what this file used to prove - that the saved documents in BOTH
profiles are untouched, and that history still finds them under either name.
"""
import importlib
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "client-rename-profiles-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)
    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("abraj")

        from backend import clients, config, database

        def switch(profile_id: str) -> None:
            manager.activate(profile_id)
            importlib.reload(config)
            importlib.reload(database)
            importlib.reload(clients)
            database.init_db()

        for profile_id, quote_no in (("abraj", "QUT-OLD-001"), ("eshraqat", "QT-0001")):
            switch(profile_id)
            database.save_quote_record(
                quote_no=quote_no,
                quote_date="14/7/2026",
                quote_type="materials",
                year=2026,
                month=7,
                client_name="Old Shared Client",
                rfq_no="",
                client_address="",
                pdf_path=root / f"{quote_no}.pdf",
                rows=[],
                terms=[],
            )

        switch("abraj")
        result = clients.rename_client("Old Shared Client", "Renamed Shared Client")
        assert result["ok"] is True, result
        assert result["new"] == "Renamed Shared Client"

        for profile_id, quote_no in (("abraj", "QUT-OLD-001"), ("eshraqat", "QT-0001")):
            switch(profile_id)
            # The document is evidence of what was sent. It keeps its wording.
            assert database.load_quote(quote_no)["client_name"] == "Old Shared Client"
            # Suggestions offer the current name only - one entry per identity,
            # not one per spelling it has ever had.
            names = {row["name"] for row in database.suggest_clients("")}
            assert "Renamed Shared Client" in names
            assert "Old Shared Client" not in names
            # Either name reaches the same history, in either profile.
            for asked in ("Old Shared Client", "Renamed Shared Client"):
                profile = clients.client_profile(asked)
                assert profile["summary"]["quotes"] == 1, (profile_id, asked)
                assert profile["name"] == "Renamed Shared Client"

        # Renaming onto a name another client owns is refused, not merged.
        switch("abraj")
        assert clients.add_client("Second Client")["ok"] is True
        clash = clients.rename_client("Renamed Shared Client", "Second Client")
        assert clash["ok"] is False
        assert "Second Client" in clash["error"]
        assert database.load_quote("QUT-OLD-001")["client_name"] == "Old Shared Client"
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("cross-profile client rename tests passed")


if __name__ == "__main__":
    main()
