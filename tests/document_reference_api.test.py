"""Bridge contract for app-only document References."""
from __future__ import annotations

import sys
import tempfile
import unittest
import inspect
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.api import Api


class DocumentReferenceApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.api = Api()
        # This suite mocks the whole storage layer, so there is no client store
        # to look in. The rule that a document may only name a client on file
        # (2026-09-11) has its own suite, tests/client_must_be_on_file.test.py;
        # here it is only in the way of what is being checked, which is the
        # reference field.
        client = patch("backend.api._known_client_error", return_value=None)
        client.start(); self.addCleanup(client.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_renderers_never_receive_reference(self):
        cases = (
            ("preview_quote", "backend.api.quote_generator.render_quote_pdf"),
            ("preview_dn", "backend.api.dn_generator.render_dn_pdf"),
            ("preview_inv", "backend.api.inv_generator.render_inv_pdf"),
        )
        with patch("backend.api._resolve_dn_docs", return_value=[]):
            for method_name, renderer_name in cases:
                with self.subTest(method=method_name), patch(renderer_name) as renderer:
                    result = getattr(self.api, method_name)({
                        "quote_no": "QUT-REF", "dn_no": "DN-REF", "inv_no": "INV-REF",
                        "reference": "Internal only", "items": [],
                    })
                    self.assertTrue(result["ok"], result)
                    rendered = renderer.call_args.args[0]
                    self.assertNotIn("reference", rendered)

    def test_save_methods_persist_reference_but_render_only_printable_data(self):
        with ExitStack() as stack:
            stack.enter_context(patch("backend.api.profile_numbering.enabled", return_value=False))
            stack.enter_context(patch("backend.api.database.best_effort_shared", return_value=True))
            stack.enter_context(patch("backend.api.signing.overlay_for_save", return_value=None))
            stack.enter_context(patch("backend.api.signing.save_doc_overlay"))
            stack.enter_context(patch("backend.api.database.load_quote", return_value=None))
            stack.enter_context(patch("backend.api.quote_numbering.quote_save_folder", return_value=self.root))
            stack.enter_context(patch("backend.api.quote_numbering.get_next_quote_number", return_value=("QUT-REF", self.root, 1)))
            quote_render = stack.enter_context(patch("backend.api.quote_generator.render_quote_pdf"))
            quote_save = stack.enter_context(patch("backend.api.database.save_quote_record"))

            result = self.api.save_quote({
                "date": "1/1/2099", "quote_type": "materials",
                "client_name": "Client", "items": [], "terms": [],
                "reference": "Internal only",
            })
            self.assertTrue(result["ok"], result)
            self.assertIn("reference", quote_save.call_args.kwargs)
            self.assertEqual(quote_save.call_args.kwargs["reference"], "Internal only")
            self.assertNotIn("reference", quote_render.call_args.args[0])

        with ExitStack() as stack:
            stack.enter_context(patch("backend.api.profile_numbering.enabled", return_value=False))
            stack.enter_context(patch("backend.api.database.best_effort_shared", return_value=True))
            stack.enter_context(patch("backend.api.database.load_dn", return_value=None))
            stack.enter_context(patch("backend.api.database.get_cost_currency", return_value="USD"))
            stack.enter_context(patch("backend.api.dn_numbering.dn_save_folder", return_value=self.root))
            stack.enter_context(patch("backend.api.signing.overlay_for_save", return_value=None))
            stack.enter_context(patch("backend.api.signing.save_doc_overlay"))
            dn_render = stack.enter_context(patch("backend.api.dn_generator.render_dn_pdf"))
            dn_save = stack.enter_context(patch("backend.api.database.save_dn_record"))

            result = self.api.save_dn({
                "dn_no": "DN-REF", "date": "1/1/2099", "client_name": "Client",
                "items": [], "reference": "Internal only",
            })
            self.assertTrue(result["ok"], result)
            self.assertIn("reference", dn_save.call_args.kwargs)
            self.assertEqual(dn_save.call_args.kwargs["reference"], "Internal only")
            self.assertNotIn("reference", dn_render.call_args.args[0])

        with ExitStack() as stack:
            stack.enter_context(patch("backend.api.profile_numbering.enabled", return_value=False))
            stack.enter_context(patch("backend.api.database.best_effort_shared", return_value=True))
            stack.enter_context(patch("backend.api.database.load_inv", return_value=None))
            stack.enter_context(patch("backend.api._find_pdf_by_stem", return_value=None))
            stack.enter_context(patch("backend.api.inv_numbering.inv_save_folder", return_value=self.root))
            stack.enter_context(patch("backend.api.database.doc_iqd_per_usd", return_value=1400.0))
            stack.enter_context(patch("backend.api.database.get_cost_currency", return_value="USD"))
            stack.enter_context(patch("backend.api.database.set_submitted"))
            stack.enter_context(patch("backend.api.database.set_invoice_paid"))
            inv_save = stack.enter_context(patch("backend.api.database.save_inv_record"))

            result = self.api.save_inv({
                "inv_no": "INV-REF", "date": "1/1/2099", "inv_type": "materials",
                "client_name": "Client", "items": [], "save_without_pdf": True,
                "reference": "Internal only",
            })
            self.assertTrue(result["ok"], result)
            self.assertIn("reference", inv_save.call_args.kwargs)
            self.assertEqual(inv_save.call_args.kwargs["reference"], "Internal only")

        with ExitStack() as stack:
            stack.enter_context(patch("backend.api.profile_numbering.enabled", return_value=False))
            stack.enter_context(patch("backend.api.po_numbering.folder_for_date", return_value=self.root))
            stack.enter_context(patch("backend.api.signing.overlay_for_save", return_value=None))
            po_render = stack.enter_context(patch("backend.api.pdf_generator.render_pdf"))
            stack.enter_context(patch("backend.api.pdf_generator.po_page_count", return_value=1))
            po_save = stack.enter_context(patch("backend.api.database.save_po_record"))

            self.assertIn("reference", inspect.signature(self.api.save_po).parameters)
            result = self.api.save_po(
                "PO-REF", "1/1/2099", [], reference="Internal only")
            self.assertTrue(result["ok"], result)
            self.assertIn("reference", po_save.call_args.kwargs)
            self.assertEqual(po_save.call_args.kwargs["reference"], "Internal only")
            self.assertNotIn("Internal only", repr(po_render.call_args))


if __name__ == "__main__":
    unittest.main()
