import importlib
import json
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "profile-backup-restore-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)
    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")
        esh_docs = root / "eshraqat-documents"
        abraj_docs = root / "abraj-documents"
        manager.settings_path("eshraqat").write_text(
            json.dumps({"documents_root": str(esh_docs), "brand_color": "#123456"}),
            encoding="utf-8",
        )
        manager.settings_path("abraj").parent.mkdir(parents=True, exist_ok=True)
        manager.settings_path("abraj").write_text(
            json.dumps({"documents_root": str(abraj_docs)}), encoding="utf-8",
        )

        from backend import api as api_module
        from backend import config, data_manager, database

        for module in (config, database, data_manager, api_module):
            importlib.reload(module)
        database.init_db()
        database.save_quote_record(
            quote_no="QT-0001", quote_date="14/7/2026", quote_type="materials",
            year=2026, month=7, client_name="Backup Client", rfq_no="",
            client_address="", pdf_path=esh_docs / "QUOTATIONS" / "QT-0001.pdf",
            rows=[], terms=[],
        )
        quote_pdf = esh_docs / "QUOTATIONS" / "QT-0001.pdf"
        quote_pdf.parent.mkdir(parents=True, exist_ok=True)
        quote_pdf.write_bytes(b"original-pdf")
        sig = manager.signatures_dir("eshraqat") / "signature-main.png"
        sig.write_bytes(b"signature")

        backups = root / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        result = data_manager.create_backup(str(backups))
        assert result["ok"] is True, result
        archive = Path(result["path"])
        assert archive.name.startswith("documents-hub-eshraqat-backup-")
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["profile_id"] == "eshraqat"
        assert manifest["version"] == 2
        assert "profile/po_tool.db" in names
        assert "profile/settings.json" in names
        assert "profile/assets/logo.png" in names
        assert "profile/assets/iso-certifications.png" in names
        assert "profile/assets/sigs/signature-main.png" in names
        assert "shared/library.db" in names
        assert any(name.endswith("QT-0001.pdf") and name.startswith("docs/") for name in names)

        manager.activate("abraj")
        for module in (config, database, data_manager, api_module):
            importlib.reload(module)
        database.init_db()
        mismatch = data_manager.restore_backup(str(archive))
        assert mismatch["ok"] is False
        assert "eshraqat" in mismatch["error"].lower()

        manager.activate("eshraqat")
        for module in (config, database, data_manager, api_module):
            importlib.reload(module)
        database.init_db()
        manager.settings_path("eshraqat").write_text(
            json.dumps({"documents_root": str(esh_docs), "brand_color": "#654321"}),
            encoding="utf-8",
        )
        sig.write_bytes(b"changed")
        quote_pdf.write_bytes(b"changed-pdf")
        with sqlite3.connect(config.DB_PATH) as conn:
            conn.execute("DELETE FROM quotes")
        with sqlite3.connect(manager.shared_db_path()) as conn:
            conn.execute("DELETE FROM clients WHERE name = ?", ("Backup Client",))

        api = api_module.Api()
        restored = api.dm_restore_backup(str(archive), replace_docs=True)
        assert restored["ok"] is True, restored
        assert config.BRAND_COLOR == "#123456"
        assert database.load_quote("QT-0001") is not None
        assert quote_pdf.read_bytes() == b"original-pdf"
        assert sig.read_bytes() == b"signature"
        assert any(row["name"] == "Backup Client" for row in database.suggest_clients("Backup"))

        malicious = root / "malicious.zip"
        with zipfile.ZipFile(malicious, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("../outside.txt", "bad")
        rejected = data_manager.restore_backup(str(malicious))
        assert rejected["ok"] is False
        assert not (root / "outside.txt").exists()
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("profile backup/restore tests passed")


if __name__ == "__main__":
    main()
