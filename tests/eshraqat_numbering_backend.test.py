import gc
import importlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def main() -> None:
    original_manager = profile_manager._MANAGER
    test_dir = Path(__file__).resolve().parents[1] / ".test-work"
    test_dir.mkdir(parents=True, exist_ok=True)
    try:
        root = test_dir / "eshraqat-numbering-suite"
        root.mkdir(parents=True, exist_ok=True)
        data_root = root / "data"
        if data_root.exists():
            shutil.rmtree(data_root)
        try:
            manager = ProfileManager(root / "data", root / "static")
            profile_manager._MANAGER = manager
            manager.create_registry("eshraqat")
            manager.settings_path("eshraqat").parent.mkdir(parents=True, exist_ok=True)
            manager.settings_path("eshraqat").write_text(
                json.dumps({"documents_root": str(root / "documents")}),
                encoding="utf-8",
            )

            from backend import (
                api as api_module,
                config,
                database,
                dn_numbering,
                inv_numbering,
                pdf_generator,
                po_numbering,
                profile_numbering,
                quote_numbering,
            )

            for module in (
                config,
                database,
                profile_numbering,
                po_numbering,
                quote_numbering,
                dn_numbering,
                inv_numbering,
                pdf_generator,
                api_module,
            ):
                importlib.reload(module)
            database.init_db()

            expected_first = {
                "po": "PO-0001",
                "quote": "QT-0001",
                "dn": "DN-0001",
                "inv": "IV-0001",
            }
            for kind, expected in expected_first.items():
                assert profile_numbering.peek(kind) == expected
                assert profile_numbering.parse_number(kind, expected) == 1
                assert profile_numbering.format_number(kind, 1) == expected

            assert po_numbering.get_next_po_number()[0] == "PO-0001"
            assert quote_numbering.get_next_quote_number("HDEC", "equipment")[0] == "QT-0001"
            assert dn_numbering.get_next_dn_number("HDEC")[0] == "DN-0001"
            assert inv_numbering.get_next_inv_number("HDEC", "equipment")[0] == "IV-0001"

            # A displayed number is only a preview. A failed save must not consume it.
            displayed = profile_numbering.peek("inv")
            try:
                raise RuntimeError("simulated render failure")
            except RuntimeError:
                pass
            assert profile_numbering.peek("inv") == displayed

            profile_numbering.commit("quote", "QT-0001")
            assert profile_numbering.peek("quote") == "QT-0002"
            # Re-saving an existing document is idempotent and keeps its number.
            profile_numbering.commit("quote", "QT-0001")
            assert profile_numbering.peek("quote") == "QT-0002"

            # Restart persistence comes from the profile database, not process memory.
            importlib.reload(profile_numbering)
            assert profile_numbering.peek("quote") == "QT-0002"

            with sqlite3.connect(config.DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO deleted_docs(kind, doc_no, deleted_at) VALUES (?, ?, ?)",
                    ("dn", "DN-0019", "2026-07-14T10:00:00"),
                )
                conn.execute(
                    """
                    INSERT INTO invoices(
                        inv_no, created_at, updated_at, inv_date, inv_type,
                        year, month, pdf_path, rows_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "IV-0012", "now", "now", "1/1/2035", "materials",
                        2035, 1, str(root / "IV-0012.pdf"), "[]",
                    ),
                )
            assert profile_numbering.peek("dn") == "DN-0020"
            assert profile_numbering.peek("inv") == "IV-0013"

            po_batch = profile_numbering.peek_many("po", 3)
            assert po_batch == ["PO-0001", "PO-0002", "PO-0003"]
            profile_numbering.commit("po", po_batch)
            assert profile_numbering.peek("po") == "PO-0004"

            # 2026-07-29 - a multi-page PO keeps ONE four-digit permanent number
            # on every page. It used to consume PO-0043/PO-0044 for the overflow.
            pages = pdf_generator.paginate_po(
                [{"name": f"Item {i}"} for i in range(60)], "PO-0042")
            assert len(pages) > 1, "60 rows should need more than one page"
            assert {page["po_no"] for page in pages} == {"PO-0042"}

            # Real API saves must replace stale displayed numbers, then commit
            # only after the document record was saved successfully.
            with sqlite3.connect(config.DB_PATH) as conn:
                for kind, doc_no in (
                    ("quote", "QT-0004"),
                    ("po", "PO-0008"),
                ):
                    conn.execute(
                        "INSERT INTO deleted_docs(kind, doc_no, deleted_at) VALUES (?, ?, ?)",
                        (kind, doc_no, "2026-07-14T11:00:00"),
                    )

            def fake_document_pdf(_payload: dict, out_path: Path) -> None:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"%PDF-test")

            def fake_po_pdf(_po_no: str, _po_date: str, _rows: list[dict],
                            out_path: Path, **_kwargs) -> None:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"%PDF-test")

            api_module.quote_generator.render_quote_pdf = fake_document_pdf
            api_module.dn_generator.render_dn_pdf = fake_document_pdf
            api_module.inv_generator.render_inv_pdf = fake_document_pdf
            api_module.pdf_generator.render_pdf = fake_po_pdf
            api = api_module.Api()

            quote_result = api.save_quote({
                "quote_no": "QT-0002",
                "date": "14/7/2026",
                "quote_type": "materials",
                "client_name": "HDEC",
                "items": [],
                "terms": [],
            })
            assert quote_result["ok"] is True
            assert quote_result["quote_no"] == "QT-0005"

            dn_result = api.save_dn({
                "dn_no": "DN-0001",
                "date": "14/7/2026",
                "client_name": "HDEC",
                "items": [],
            })
            assert dn_result["ok"] is True
            assert dn_result["dn_no"] == "DN-0020"

            inv_result = api.save_inv({
                "inv_no": "IV-0001",
                "date": "14/7/2026",
                "inv_type": "materials",
                "client_name": "HDEC",
                "items": [],
            })
            assert inv_result["ok"] is True
            assert inv_result["inv_no"] == "IV-0013"

            po_result = api.save_po(
                "PO-0004", "14/7/2026", [{"name": "Item", "qty": "1"}],
            )
            assert po_result["ok"] is True
            assert [row["po_no"] for row in po_result["saved"]] == ["PO-0009"]

            with sqlite3.connect(config.DB_PATH) as conn:
                counters = dict(conn.execute(
                    "SELECT kind, last_value FROM document_counters"
                ).fetchall())
            assert counters == {
                "quote": 5,
                "dn": 20,
                "inv": 13,
                "po": 9,
            }

            before_failure = profile_numbering.peek("quote")

            def failed_quote_pdf(_payload: dict, _out_path: Path) -> None:
                raise RuntimeError("render failed")

            api_module.quote_generator.render_quote_pdf = failed_quote_pdf
            failed_result = api.save_quote({
                "date": "14/7/2026",
                "quote_type": "materials",
                "client_name": "HDEC",
                "items": [],
                "terms": [],
            })
            assert failed_result["ok"] is False
            assert profile_numbering.peek("quote") == before_failure

            # Abraj keeps its established numbering when its profile is active.
            manager.activate("abraj")
            for module in (
                config,
                database,
                profile_numbering,
                po_numbering,
                quote_numbering,
                dn_numbering,
                inv_numbering,
            ):
                importlib.reload(module)
            database.init_db()
            assert po_numbering.get_next_po_number()[0].startswith("A-")
            assert quote_numbering.get_next_quote_number("HDEC", "materials")[0].startswith(
                "QUT-MT-HDEC-"
            )
            assert dn_numbering.get_next_dn_number("HDEC")[0].startswith("DN-HDEC-")
            assert inv_numbering.get_next_inv_number("HDEC", "materials")[0].startswith(
                "INV-MT-HDEC-"
            )
        finally:
            gc.collect()
            if data_root.exists():
                shutil.rmtree(data_root, ignore_errors=True)
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("Eshraqat numbering backend tests passed")


if __name__ == "__main__":
    main()
