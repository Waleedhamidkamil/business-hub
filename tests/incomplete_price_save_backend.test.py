"""Incomplete QUT/INV saves are DB-only and retire any previous PDF."""

from __future__ import annotations

import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api import Api, _rerender_signed


def _never_render(*_args, **_kwargs):
    raise AssertionError("an incomplete-price save must not render a PDF")


def test_quote_app_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "QUT-TEST.pdf"
        old.write_bytes(b"old pdf")
        rec = {"pdf_path": str(old), "rows": []}
        saved: dict = {}

        with (
            patch("backend.api.quote_numbering.get_next_quote_number",
                  return_value=("QUT-TEST", root, 1)),
            patch("backend.api.quote_numbering.quote_save_folder", return_value=root),
            patch("backend.api.database.load_quote", return_value=rec),
            patch("backend.api.database.inherit_costs", side_effect=lambda _a, b, _c: b),
            patch("backend.api.database.save_quote_record",
                  side_effect=lambda **kw: saved.update(kw)),
            patch("backend.api.database.set_doc_columns"),
            patch("backend.api.database.set_doc_layout"),
            patch("backend.api.database.set_submitted"),
            patch("backend.api.database.best_effort_shared"),
            patch("backend.api.signing.save_doc_overlay"),
            patch("backend.api.signing.overlay_for_save", side_effect=_never_render),
            patch("backend.api.quote_generator.render_quote_pdf", side_effect=_never_render),
            patch("backend.api.profile_numbering.enabled", return_value=False),
        ):
            result = Api().save_quote({
                "quote_no": "QUT-TEST", "is_edit": True,
                "date": "24/8/2026", "quote_type": "materials",
                "client_name": "Test", "items": [{"item": "Cement", "qty": 1}],
                "save_without_pdf": True,
            })

        assert result["ok"] is True
        assert result["path"] == ""
        assert result["incomplete_price"] is True
        assert saved["pdf_path"] == ""
        assert not old.exists(), "the old complete PDF must not survive an incomplete edit"


def test_invoice_app_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "INV-TEST.pdf"
        old.write_bytes(b"old pdf")
        rec = {"pdf_path": str(old), "rows": []}
        saved: dict = {}

        with (
            patch("backend.api.inv_numbering.inv_save_folder", return_value=root),
            patch("backend.api.database.load_inv", return_value=rec),
            patch("backend.api.database.inherit_costs", side_effect=lambda _a, b, _c: b),
            patch("backend.api.database.save_inv_record",
                  side_effect=lambda **kw: saved.update(kw)),
            patch("backend.api.database.set_doc_columns"),
            patch("backend.api.database.set_doc_layout"),
            patch("backend.api.database.set_submitted"),
            patch("backend.api.database.set_invoice_paid"),
            patch("backend.api.database.best_effort_shared"),
            patch("backend.api.database.get_cost_currency", return_value="USD"),
            patch("backend.api.database.link_invoice_dn_refs"),
            patch("backend.api.signing.save_doc_overlay"),
            patch("backend.api.signing.overlay_for_save", side_effect=_never_render),
            patch("backend.api.inv_generator.render_inv_pdf", side_effect=_never_render),
            patch("backend.api.profile_numbering.enabled", return_value=False),
            patch("backend.api._resolve_dn_docs", side_effect=_never_render),
        ):
            result = Api().save_inv({
                "inv_no": "INV-TEST", "is_edit": True,
                "date": "24/8/2026", "inv_type": "materials",
                "client_name": "Test", "items": [{"item": "Cement", "qty": 1}],
                "save_without_pdf": True,
            })

        assert result["ok"] is True
        assert result["path"] == ""
        assert result["incomplete_price"] is True
        assert saved["pdf_path"] == ""
        assert not old.exists(), "the old complete PDF must not survive an incomplete edit"


def test_database_only_rows_are_listed() -> None:
    quote_db = [{
        "quote_no": "QUT-DRAFT", "quote_date": "24/8/2026",
        "quote_type": "materials", "pdf_path": "", "client_name": "Test",
        "created_at": "2026-08-24T12:00:00", "updated_at": "2026-08-24T12:00:00",
    }]
    inv_db = [{
        "inv_no": "INV-DRAFT", "inv_date": "24/8/2026", "inv_type": "materials",
        "pdf_path": "", "client_name": "Test", "grand_total": 0,
        "sell_currency": "USD", "sell_total": 0,
        "created_at": "2026-08-24T12:00:00", "updated_at": "2026-08-24T12:00:00",
    }]
    with (
        # Even if a stale file is still discoverable, the DB-only state wins and
        # must blank its path so no action can expose it.
        patch("backend.api.quote_numbering.list_quotes_for_month", return_value=[
            {"quote_no": "QUT-DRAFT", "path": "D:/stale/QUT-DRAFT.pdf"}]),
        patch("backend.api.inv_numbering.list_invoices_for_month", return_value=[
            {"inv_no": "INV-DRAFT", "path": "D:/stale/INV-DRAFT.pdf"}]),
        patch("backend.api.database.list_quotes_in_db", return_value=quote_db),
        patch("backend.api.database.list_invoices_in_db", return_value=inv_db),
        patch("backend.api.signing.signed_doc_nos", return_value=set()),
        patch("backend.api.database.submitted_set", return_value=set()),
        patch("backend.api.database.paid_invoice_set", return_value=set()),
        patch("backend.api.database.linked_source_set", return_value=set()),
    ):
        api = Api()
        quotes = api.list_quotes_for_month(2026, 8)
        invoices = api.list_invoices_for_month(2026, 8)

    assert len(quotes) == 1 and quotes[0]["quote_no"] == "QUT-DRAFT"
    assert quotes[0]["editable"] is True and quotes[0]["incomplete_price"] is True
    assert quotes[0]["path"] == ""
    assert len(invoices) == 1 and invoices[0]["inv_no"] == "INV-DRAFT"
    assert invoices[0]["editable"] is True and invoices[0]["incomplete_price"] is True
    assert invoices[0]["path"] == ""


def test_completed_quote_restores_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = {"pdf_path": "", "rows": []}
        saved: dict = {}

        def render(_quote, out: Path) -> None:
            out.write_bytes(b"new pdf")

        with (
            patch("backend.api.quote_numbering.get_next_quote_number",
                  return_value=("QUT-DRAFT", root, 1)),
            patch("backend.api.quote_numbering.quote_save_folder", return_value=root),
            patch("backend.api.database.load_quote", return_value=rec),
            patch("backend.api.database.inherit_costs", side_effect=lambda _a, b, _c: b),
            patch("backend.api.database.save_quote_record",
                  side_effect=lambda **kw: saved.update(kw)),
            patch("backend.api.database.set_doc_columns"),
            patch("backend.api.database.set_doc_layout"),
            patch("backend.api.database.best_effort_shared"),
            patch("backend.api.signing.save_doc_overlay"),
            patch("backend.api.signing.overlay_for_save", return_value=None),
            patch("backend.api.quote_generator.render_quote_pdf", side_effect=render),
            patch("backend.api.profile_numbering.enabled", return_value=False),
        ):
            result = Api().save_quote({
                "quote_no": "QUT-DRAFT", "is_edit": True,
                "date": "24/8/2026", "quote_type": "materials",
                "client_name": "Test",
                "items": [{"item": "Cement", "qty": 1, "rate": 10}],
            })

        assert result["ok"] is True and result["incomplete_price"] is False
        assert result["path"].endswith("QUT-DRAFT.pdf")
        assert Path(result["path"]).read_bytes() == b"new pdf"
        assert str(saved["pdf_path"]).endswith("QUT-DRAFT.pdf")


def test_failed_draft_write_restores_old_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "QUT-RESTORE.pdf"
        old.write_bytes(b"still valid")
        rec = {"pdf_path": str(old), "rows": []}

        with (
            patch("backend.api.quote_numbering.get_next_quote_number",
                  return_value=("QUT-RESTORE", root, 1)),
            patch("backend.api.quote_numbering.quote_save_folder", return_value=root),
            patch("backend.api.database.load_quote", return_value=rec),
            patch("backend.api.database.inherit_costs", side_effect=lambda _a, b, _c: b),
            patch("backend.api.database.save_quote_record",
                  side_effect=RuntimeError("simulated DB failure")),
            patch("backend.api.quote_generator.render_quote_pdf", side_effect=_never_render),
            patch("backend.api.profile_numbering.enabled", return_value=False),
        ):
            result = Api().save_quote({
                "quote_no": "QUT-RESTORE", "is_edit": True,
                "date": "24/8/2026", "quote_type": "materials",
                "client_name": "Test", "items": [{"item": "Cement", "qty": 1}],
                "save_without_pdf": True,
            })

        assert result["ok"] is False
        assert old.read_bytes() == b"still valid"
        assert not list(root.glob("*.incomplete-draft"))


def test_app_only_draft_cannot_be_signed_into_a_pdf() -> None:
    with patch("backend.api.database.load_quote",
               return_value={"quote_no": "QUT-DRAFT", "pdf_path": ""}):
        try:
            _rerender_signed("qut", "QUT-DRAFT", {})
        except ValueError as exc:
            assert "complete the document pricing" in str(exc)
        else:
            raise AssertionError("signing must not create a PDF for an app-only draft")


def test_new_mode_overwrite_retires_existing_invoice_pdf() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / "INV-OVERWRITE.pdf"
        old.write_bytes(b"old pdf")
        rec = {"pdf_path": str(old), "rows": []}

        with (
            patch("backend.api.inv_numbering.inv_save_folder", return_value=root),
            patch("backend.api.database.load_inv", return_value=rec),
            patch("backend.api.database.save_inv_record"),
            patch("backend.api.database.set_submitted"),
            patch("backend.api.database.set_invoice_paid"),
            patch("backend.api.database.set_doc_columns"),
            patch("backend.api.database.set_doc_layout"),
            patch("backend.api.database.best_effort_shared"),
            patch("backend.api.database.get_cost_currency", return_value="USD"),
            patch("backend.api.signing.save_doc_overlay"),
            patch("backend.api.inv_generator.render_inv_pdf", side_effect=_never_render),
            patch("backend.api.profile_numbering.enabled", return_value=False),
        ):
            result = Api().save_inv({
                "inv_no": "INV-OVERWRITE", "is_edit": False,
                "date": "24/8/2026", "inv_type": "materials",
                "client_name": "Test", "items": [{"item": "Cement", "qty": 1}],
                "save_without_pdf": True,
            })

        assert result["ok"] is True and result["path"] == ""
        assert not old.exists()


def test_completed_invoice_runs_deferred_dn_link() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rec = {"pdf_path": "", "rows": [], "source_dn_no": "DN-SOURCE"}
        linked: list[tuple[str, str]] = []

        def render(_inv, out: Path) -> None:
            out.write_bytes(b"new pdf")

        with (
            patch("backend.api.inv_numbering.inv_save_folder", return_value=root),
            patch("backend.api.database.load_inv", return_value=rec),
            patch("backend.api.database.save_inv_record"),
            patch("backend.api.database.set_doc_columns"),
            patch("backend.api.database.set_doc_layout"),
            patch("backend.api.database.best_effort_shared"),
            patch("backend.api.database.get_cost_currency", return_value="USD"),
            patch("backend.api.database.link_invoice_dn_refs"),
            patch("backend.api.database.link_invoice_from_dn",
                  side_effect=lambda dn, inv: linked.append((dn, inv)) or {"ok": True}),
            patch("backend.api.signing.save_doc_overlay"),
            patch("backend.api.signing.overlay_for_save", return_value=None),
            patch("backend.api.inv_generator.render_inv_pdf", side_effect=render),
            patch("backend.api._resolve_dn_docs", return_value=[]),
            patch("backend.api.profile_numbering.enabled", return_value=False),
        ):
            result = Api().save_inv({
                "inv_no": "INV-DRAFT", "is_edit": True,
                "date": "24/8/2026", "inv_type": "materials",
                "client_name": "Test",
                "items": [{"item": "Cement", "qty": 1, "rate": 10}],
            })

        assert result["ok"] is True and result["path"].endswith("INV-DRAFT.pdf")
        assert linked == [("DN-SOURCE", "INV-DRAFT")]


if __name__ == "__main__":
    # Every test here mocks the whole storage layer, so there is no client store
    # to look in. The rule that a document may only name a client on file
    # (2026-09-11) has its own suite, tests/client_must_be_on_file.test.py; in
    # here it would refuse the fake client every one of these saves uses, and
    # what is being checked is incomplete pricing.
    _client_rule = patch("backend.api._known_client_error", return_value=None)
    _client_rule.start()

    test_quote_app_only()
    test_invoice_app_only()
    test_database_only_rows_are_listed()
    test_completed_quote_restores_pdf()
    test_failed_draft_write_restores_old_pdf()
    test_app_only_draft_cannot_be_signed_into_a_pdf()
    test_new_mode_overwrite_retires_existing_invoice_pdf()
    test_completed_invoice_runs_deferred_dn_link()
    _client_rule.stop()
    print("incomplete price save backend tests passed")
