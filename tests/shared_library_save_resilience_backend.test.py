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


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "shared-save-resilience-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)
    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")
        manager.settings_path("eshraqat").write_text(
            json.dumps({"documents_root": str(root / "documents")}),
            encoding="utf-8",
        )

        from backend import api as api_module
        from backend import client_master, config, database, profile_numbering

        for module in (config, database, profile_numbering, api_module):
            importlib.reload(module)
        database.init_db()
        # A document may only name a client that is ON FILE (2026-09-11), so
        # this suite's client has to exist before it can save anything. The
        # shared library here is the isolated one under .test-work.
        assert client_master.save_record({"name": "Shared Failure Client"})["ok"]

        def fake_document_pdf(_payload: dict, out_path: Path) -> None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"%PDF-test")

        def fake_po_pdf(_po_no: str, _date: str, _rows: list[dict],
                        out_path: Path, **_kwargs) -> None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"%PDF-test")

        api_module.quote_generator.render_quote_pdf = fake_document_pdf
        api_module.dn_generator.render_dn_pdf = fake_document_pdf
        api_module.inv_generator.render_inv_pdf = fake_document_pdf
        api_module.pdf_generator.render_pdf = fake_po_pdf

        def unavailable_shared():
            raise sqlite3.OperationalError("shared library unavailable")

        database._connect_shared = unavailable_shared
        api = api_module.Api()

        quote = api.save_quote({
            "date": "14/7/2026", "quote_type": "materials",
            "client_name": "Shared Failure Client",
            "items": [{"item": "Pipe", "description": "Test", "unit": "m", "qty": 1, "rate": 2}],
            "terms": [],
        })
        assert quote["ok"] is True, quote
        assert database.load_quote(quote["quote_no"]) is not None

        dn = api.save_dn({
            "date": "14/7/2026", "client_name": "Shared Failure Client",
            "items": [{"item": "Pipe", "description": "Test", "unit": "m", "qty": 1}],
        })
        assert dn["ok"] is True, dn
        assert database.load_dn(dn["dn_no"]) is not None

        inv = api.save_inv({
            "date": "14/7/2026", "inv_type": "materials",
            "client_name": "Shared Failure Client",
            "items": [{"item": "Pipe", "description": "Test", "unit": "m", "qty": 1, "rate": 2}],
        })
        assert inv["ok"] is True, inv
        assert database.load_inv(inv["inv_no"]) is not None

        po = api.save_po(
            "PO-0001", "14/7/2026",
            [{"name": "Pipe", "description": "Test", "unit": "m", "qty": 1, "company": "Vendor"}],
        )
        assert po["ok"] is True, po
        assert database.load_po(po["saved"][0]["po_no"]) is not None

        assert profile_numbering.peek("quote") == "QT-0002"
        assert profile_numbering.peek("dn") == "DN-0002"
        assert profile_numbering.peek("inv") == "IV-0002"
        assert profile_numbering.peek("po") == "PO-0002"
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("shared library save resilience tests passed")


if __name__ == "__main__":
    main()
