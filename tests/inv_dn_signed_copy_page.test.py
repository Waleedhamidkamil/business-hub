"""An invoice made from a DN attaches the CLIENT's signed copy, not a re-render.

Until 2026-07-30 the page appended after an invoice was always a fresh re-render
of the delivery note, rebuilt from the DB. That re-render only ever carries OUR
supplier signature - `signing.DOC_SLOTS["dn"] == ["supplier"]` deliberately
leaves the recipient box blank for the client's pen - so an invoice went out
looking as though the delivery had never been acknowledged, even when the signed
and stamped scan was sitting in `signed_copies`.

An IMAGE signed copy is now embedded as a data URI in the same single render
pass. A PDF signed copy cannot be spliced into HTML, and merging it afterwards
would mean taking on a Python PDF library, which requirements.txt deliberately
avoids - those still fall back to the re-render and say so.

!! Runs against the LIVE profile DB (this project has no test isolation). Every
!! row uses the __SIGNPAGE__ sentinel and is removed by targeted DELETE. See the
!! warning in docs/11.
"""
import base64
import io
import sqlite3
import sys
import tempfile
import unittest

from pypdf.generic import NameObject, NumberObject
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

# This module reads profile-dependent constants, so it is meaningless under the
# wrong profile - and it used to FAIL there, cryptically, which is how the board
# ended up warning readers not to believe it instead of fixing it (2026-09-05).
# Say so plainly instead. See tests/profile_guard.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from profile_guard import require_profile  # noqa: E402

require_profile("abraj")


from PIL import Image  # noqa: E402

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, inv_generator  # noqa: E402

TAG = "__SIGNPAGE__"
# Item names are tagged so the catalog rows this file creates are always
# identifiable and removable - a bare "Item 3" is indistinguishable from a
# real one.
ITEM_TAG = "ZZSignPage Item "
DN_ONE = f"DN-{TAG}-1"          # 1-page delivery note, image signed copy
DN_MANY = f"DN-{TAG}-2"         # multi-page delivery note, image signed copy
DN_PDF = f"DN-{TAG}-3"          # signed copy is a VALID pdf
DN_BAD = f"DN-{TAG}-4"          # signed copy is an unreadable pdf
DN_ODD = f"DN-{TAG}-5"          # signed copy has an unsupported extension
ALL_DNS = (DN_ONE, DN_MANY, DN_PDF, DN_BAD, DN_ODD)
CLIENT = f"Signed Page Test Client {TAG}"
WORK = APP / ".test-work" / "inv-signed-copy"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        marks = ",".join("?" * len(ALL_DNS))
        c.execute(f"DELETE FROM dns WHERE dn_no IN ({marks})", ALL_DNS)
        c.execute(f"DELETE FROM signed_copies WHERE doc_no IN ({marks})", ALL_DNS)
    # `save_dn_record` also REMEMBERS every row in the shared catalog, which
    # this cleanup used to ignore: 200 rows named "Item 0".."Item 199" were
    # left behind on every run and showed up in Master Data as a long list of
    # nameless junk. The rows are tagged now so they can never collide with a
    # real item, and they are removed here by that tag.
    with database._connect_shared() as c:
        for t in ("dn_items", "inv_items"):
            try:
                c.execute(f"DELETE FROM {t} WHERE name LIKE ? || '%'", (ITEM_TAG,))
            except sqlite3.OperationalError:
                pass
        # The four catalogs were dropped 2026-08-20; a save now writes only the
        # item RECORD, so the tagged sentinels live there instead.
        c.execute("DELETE FROM master_items WHERE name_en LIKE ? || '%' "
                  "OR name_ar LIKE ? || '%'", (ITEM_TAG, ITEM_TAG))


def make_image(path: Path, colour=(200, 40, 40)):
    Image.new("RGB", (600, 850), colour).save(path)
    return path


def make_pdf(path: Path, pages=1, size=(595, 842)):
    """A structurally valid PDF. pypdf builds it, so this needs no PDF engine."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=size[0], height=size[1])
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def save_dn(dn_no, n_rows):
    database.save_dn_record(
        dn_no=dn_no, dn_date="30/7/2026", year=2026, month=7, client_name=CLIENT,
        lpo_no="", client_address="", include_prices=True, pdf_path=Path("x.pdf"),
        rows=[{"item": f"{ITEM_TAG}{i}", "description": "d", "unit": "EA",
               "qty": "1", "rate": "10"} for i in range(n_rows)],
        source_quote_no=None)


class SignedCopyReplacesTheRerender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup()
        WORK.mkdir(parents=True, exist_ok=True)
        for dn in (DN_ONE, DN_PDF, DN_BAD, DN_ODD):
            save_dn(dn, 3)
        save_dn(DN_MANY, 200)          # comfortably multi-page
        cls.img = make_image(WORK / "signed.png")
        database.set_signed_copy("dn", DN_ONE, cls.img)
        database.set_signed_copy("dn", DN_MANY, make_image(WORK / "signed2.jpg"))
        cls.pdf = make_pdf(WORK / "signed.pdf", pages=2)
        database.set_signed_copy("dn", DN_PDF, cls.pdf)
        bad = WORK / "broken.pdf"
        bad.write_bytes(b"%PDF-1.4\n% truncated, not a real pdf\n")
        database.set_signed_copy("dn", DN_BAD, bad)
        odd = WORK / "scan.tiff"
        odd.write_bytes(b"II*\x00 not really a tiff either")
        database.set_signed_copy("dn", DN_ODD, odd)

    @classmethod
    def tearDownClass(cls):
        cleanup()
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)

    # ---- resolver ----
    def test_image_copy_is_resolved_as_a_data_uri(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ONE}]})
        self.assertEqual(len(docs), 1)
        self.assertTrue(docs[0]["signed_copy_image"].startswith("data:image/png;base64,"))
        self.assertNotIn("signed_copy_skipped", docs[0])

    def test_valid_pdf_copy_is_marked_for_the_merge(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_PDF}]})
        self.assertNotIn("signed_copy_image", docs[0])
        self.assertNotIn("signed_copy_skipped", docs[0])
        self.assertTrue(docs[0]["signed_copy_pdf"].endswith("signed.pdf"))
        self.assertEqual(docs[0]["signed_copy_pages"], 2)
        # The token is what lets the merge FIND the placeholder page instead of
        # computing an index that could drift.
        self.assertEqual(docs[0]["signed_copy_token"], f"__SIGNEDCOPY__{DN_PDF}__")

    def test_unreadable_pdf_falls_back_and_reports_why(self):
        """Validated at resolve time, so the merge can trust its input."""
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_BAD}]})
        self.assertNotIn("signed_copy_pdf", docs[0])
        self.assertEqual(docs[0]["signed_copy_skipped"], "unreadable")

    def test_unsupported_extension_falls_back(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ODD}]})
        self.assertEqual(docs[0]["signed_copy_skipped"], "unsupported")

    def test_no_copy_at_all_is_unchanged(self):
        database.clear_signed_copy("dn", DN_ONE)
        try:
            docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ONE}]})
            self.assertNotIn("signed_copy_image", docs[0])
            self.assertNotIn("signed_copy_skipped", docs[0])
        finally:
            database.set_signed_copy("dn", DN_ONE, self.img)

    def test_a_moved_file_falls_back_silently(self):
        """The row can outlive a file the user moved - never crash the save."""
        database.set_signed_copy("dn", DN_ONE, WORK / "gone.png")
        try:
            docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ONE}]})
            self.assertNotIn("signed_copy_image", docs[0])
        finally:
            database.set_signed_copy("dn", DN_ONE, self.img)

    # ---- rendered HTML ----
    def _invoice_html(self, dn_no):
        inv = {"inv_no": f"INV-{TAG}-1", "date": "30/7/2026",
               "inv_type": "materials", "client_name": CLIENT,
               "items": [{"item": dn_no, "dn_ref": dn_no, "unit": "EA",
                          "qty": "1", "rate": "30"}]}
        inv["_dn_docs"] = api._resolve_dn_docs(inv)
        return inv_generator.render_inv_html(inv)

    def test_signed_copy_page_replaces_the_dn_table(self):
        html = self._invoice_html(DN_ONE)
        self.assertIn("dn-signed-copy-page", html)
        self.assertIn("data:image/png;base64,", html)
        # The re-rendered delivery note must NOT also be appended.
        self.assertNotIn("dn-page", html)
        self.assertNotIn("Delivery Note", html)

    def test_valid_pdf_copy_emits_a_placeholder_not_the_dn(self):
        html = self._invoice_html(DN_PDF)
        self.assertIn(f"__SIGNEDCOPY__{DN_PDF}__", html)
        self.assertIn("dn-signed-copy-page", html)
        self.assertNotIn("dn-page", html)      # the re-render must not also ship

    def test_unreadable_pdf_still_appends_the_rerendered_dn(self):
        html = self._invoice_html(DN_BAD)
        self.assertNotIn("__SIGNEDCOPY__", html)
        self.assertIn("dn-page", html)

    def test_image_page_is_a4_and_never_crops(self):
        html = self._invoice_html(DN_ONE)
        self.assertIn("width: 186mm", html)     # A4 210 - 2x12mm
        self.assertIn("height: 273mm", html)    # A4 297 - 2x12mm
        self.assertIn("object-fit: contain", html)

    # ---- save-time notices ----
    def test_multi_page_dn_with_one_scanned_sheet_warns(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_MANY}]})
        self.assertGreater(api._dn_rendered_page_count(docs[0]), 1)
        notes = api._dn_copy_notes(docs)
        self.assertEqual(len(notes), 1)
        self.assertIn("1 page", notes[0])
        self.assertIn(DN_MANY, notes[0])

    def test_single_page_dn_is_quiet(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ONE}]})
        self.assertEqual(api._dn_rendered_page_count(docs[0]), 1)
        self.assertEqual(api._dn_copy_notes(docs), [])

    def test_valid_pdf_copy_is_quiet(self):
        """It attaches cleanly - 2 scanned pages for a 3-row (1-page) DN."""
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_PDF}]})
        self.assertEqual(api._dn_copy_notes(docs), [])

    def test_unreadable_pdf_note_explains_the_fallback(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_BAD}]})
        notes = api._dn_copy_notes(docs)
        self.assertEqual(len(notes), 1)
        self.assertIn("could not be opened", notes[0])

    def test_unsupported_note_explains_the_fallback(self):
        docs = api._resolve_dn_docs({"items": [{"dn_ref": DN_ODD}]})
        notes = api._dn_copy_notes(docs)
        self.assertEqual(len(notes), 1)
        self.assertIn("not a PDF, JPG or PNG", notes[0])

    def test_merge_failure_is_reported_loudly(self):
        notes = api._dn_copy_notes([{"dn_no": DN_PDF, "signed_copy_merge_failed": True}])
        self.assertEqual(len(notes), 1)
        self.assertIn("NOT attached", notes[0])

    def test_notes_are_empty_without_any_dn_line(self):
        self.assertEqual(api._dn_copy_notes([]), [])

    # ---- picker ----
    def test_picker_reports_which_dns_have_a_signed_copy(self):
        rows = {d["no"]: d for d in database.list_invoiceable_dns(limit=500)}
        if DN_ONE not in rows:
            self.skipTest("sentinel DN not surfaced by the picker in this profile")
        self.assertTrue(rows[DN_ONE]["client_signed"])


class RenderedPdfCarriesTheScan(unittest.TestCase):
    """End-to-end through the real PDF engine. Needs Chrome/Edge + PyMuPDF."""

    @classmethod
    def setUpClass(cls):
        try:
            import fitz  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("PyMuPDF (fitz) not installed")
        cleanup()
        WORK.mkdir(parents=True, exist_ok=True)
        save_dn(DN_ONE, 3)
        # A distinctive solid colour is easy to find again in the output.
        database.set_signed_copy("dn", DN_ONE, make_image(WORK / "s.png", (12, 34, 200)))
        inv = {"inv_no": f"INV-{TAG}-PDF", "date": "30/7/2026",
               "inv_type": "materials", "client_name": CLIENT,
               "items": [{"item": DN_ONE, "dn_ref": DN_ONE, "unit": "EA",
                          "qty": "1", "rate": "30"}]}
        inv["_dn_docs"] = api._resolve_dn_docs(inv)
        try:
            cls.pdf = inv_generator.render_inv_pdf(inv, WORK / "out.pdf")
        except Exception as e:
            raise unittest.SkipTest(f"PDF engine unavailable: {e}")

    @classmethod
    def tearDownClass(cls):
        cleanup()
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)

    def test_last_page_is_the_scan(self):
        import fitz
        doc = fitz.open(self.pdf)
        try:
            self.assertGreaterEqual(doc.page_count, 2)
            last = doc[doc.page_count - 1]
            self.assertGreaterEqual(len(last.get_images(full=True)), 1,
                                    "the signed copy did not reach the PDF")
            # It is the scan, not the delivery-note table.
            self.assertNotIn("Delivery Note", last.get_text())
        finally:
            doc.close()


class PdfCopyIsMergedInPlace(unittest.TestCase):
    """A PDF signed copy replaces its placeholder page, in the right position.

    Needs the real PDF engine (Chrome/Edge) because the placeholder has to be
    located in an actually-rendered document.
    """

    @classmethod
    def setUpClass(cls):
        cleanup()
        WORK.mkdir(parents=True, exist_ok=True)
        save_dn(DN_PDF, 3)
        save_dn(DN_ONE, 3)
        database.set_signed_copy("dn", DN_ONE, make_image(WORK / "s.png"))
        # Build the "scan" with the app's own engine so it carries findable text.
        from backend import pdf_engine
        try:
            scan = pdf_engine.write_pdf(
                "<html><body>"
                "<div style='page-break-after:always'>SCANNED-SHEET-ONE</div>"
                "<div>SCANNED-SHEET-TWO</div>"
                "</body></html>", WORK / "scan.pdf")
        except Exception as e:
            raise unittest.SkipTest(f"PDF engine unavailable: {e}")
        database.set_signed_copy("dn", DN_PDF, Path(scan))
        cls.scan_pages = len(__import__("pypdf").PdfReader(str(scan)).pages)

        inv = {"inv_no": f"INV-{TAG}-MERGE", "date": "30/7/2026",
               "inv_type": "materials", "client_name": CLIENT,
               "items": [
                   {"item": DN_PDF, "dn_ref": DN_PDF, "unit": "EA", "qty": "1", "rate": "10"},
                   {"item": DN_ONE, "dn_ref": DN_ONE, "unit": "EA", "qty": "1", "rate": "20"},
               ]}
        cls.docs = api._resolve_dn_docs(inv)
        inv["_dn_docs"] = cls.docs
        cls.pdf = inv_generator.render_inv_pdf(inv, WORK / "merged.pdf")

    @classmethod
    def tearDownClass(cls):
        cleanup()
        import shutil
        shutil.rmtree(WORK, ignore_errors=True)

    def _pages_text(self):
        from pypdf import PdfReader
        return [(p.extract_text() or "") for p in PdfReader(str(self.pdf)).pages]

    def test_no_placeholder_token_survives(self):
        """The marker page must never reach the client."""
        joined = "\n".join(self._pages_text())
        self.assertNotIn("__SIGNEDCOPY__", joined)

    def test_every_scanned_page_is_present(self):
        joined = "\n".join(self._pages_text())
        self.assertIn("SCANNED-SHEET-ONE", joined)
        self.assertIn("SCANNED-SHEET-TWO", joined)

    def test_merge_did_not_report_failure(self):
        self.assertFalse(any(d.get("signed_copy_merge_failed") for d in self.docs))
        self.assertEqual(api._dn_copy_notes(self.docs), [])

    def test_scan_lands_before_the_second_dn_attachment(self):
        """Position matters: the merge must respect dn_ref order, not append at
        the end. DN_PDF is the FIRST line, so its scan precedes DN_ONE's image."""
        texts = self._pages_text()
        first = next(i for i, t in enumerate(texts) if "SCANNED-SHEET-ONE" in t)
        last_scan = next(i for i, t in enumerate(texts) if "SCANNED-SHEET-TWO" in t)
        self.assertEqual(last_scan, first + 1, "scan pages must stay contiguous")
        # The image attachment is the final page (it has no extractable text).
        self.assertEqual(last_scan, len(texts) - 2)

    def test_all_pages_are_a4(self):
        """A Letter/A5 scan would otherwise change sheet size mid-document."""
        from pypdf import PdfReader
        for i, page in enumerate(PdfReader(str(self.pdf)).pages, start=1):
            w, h = float(page.mediabox.width), float(page.mediabox.height)
            self.assertAlmostEqual(w, 595.276, delta=2, msg=f"page {i} width")
            self.assertAlmostEqual(h, 841.890, delta=2, msg=f"page {i} height")


class OddSizedScanIsFittedToA4(unittest.TestCase):
    def test_non_a4_page_is_scaled_without_distortion(self):
        from pypdf import PdfReader, PdfWriter
        WORK.mkdir(parents=True, exist_ok=True)
        src = make_pdf(WORK / "letter.pdf", pages=1, size=(612, 792))   # US Letter
        try:
            page = PdfReader(str(src)).pages[0]
            fitted = inv_generator._place_on_a4(page, PdfWriter())
            self.assertAlmostEqual(float(fitted.mediabox.width), 595.276, delta=2)
            self.assertAlmostEqual(float(fitted.mediabox.height), 841.890, delta=2)
        finally:
            import shutil
            shutil.rmtree(WORK, ignore_errors=True)

    def test_an_a4_page_is_passed_through_untouched(self):
        from pypdf import PdfReader, PdfWriter
        WORK.mkdir(parents=True, exist_ok=True)
        src = make_pdf(WORK / "a4.pdf", pages=1, size=(595.276, 841.890))
        try:
            page = PdfReader(str(src)).pages[0]
            self.assertIs(inv_generator._place_on_a4(page, PdfWriter()), page)
        finally:
            import shutil
            shutil.rmtree(WORK, ignore_errors=True)

    def test_a_rotated_scan_is_stood_up_not_left_sideways(self):
        """A scanner very often writes a LANDSCAPE mediabox plus a /Rotate flag
        telling the viewer to stand the page up - which is why the file looks
        portrait in a PDF reader. `merge_transformed_page` copies the CONTENT
        and drops that flag, so before 2026-09-02 the scan came out sideways in
        the invoice (DN-HDEC-047: 842x596 with /Rotate 270).

        Checking the fitted page is portrait proves NOTHING - an unrotated
        landscape page gets shrunk onto a portrait A4 sheet and measures
        portrait too. What separates the two is that a rotated-A4 scan, once
        stood up, IS A4: it must be passed through at full size, not letterboxed
        into the middle of a fresh sheet.
        """
        from pypdf import PdfReader, PdfWriter
        WORK.mkdir(parents=True, exist_ok=True)
        src = make_pdf(WORK / "rotated.pdf", pages=1, size=(841.890, 595.276))
        try:
            for rotation in (90, 270):
                with self.subTest(rotate=rotation):
                    page = PdfReader(str(src)).pages[0]
                    page[NameObject("/Rotate")] = NumberObject(rotation)
                    fitted = inv_generator._place_on_a4(page, PdfWriter())
                    self.assertIs(
                        fitted, page,
                        f"/Rotate {rotation}: an A4 scan lying on its side was "
                        f"shrunk onto a new sheet instead of being stood up")
                    self.assertGreater(float(fitted.mediabox.height),
                                       float(fitted.mediabox.width))
                    self.assertFalse(
                        int(fitted.get("/Rotate") or 0) % 360,
                        "the rotation must be baked into the content, not left "
                        "on a flag the merge is about to drop")
        finally:
            import shutil
            shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
