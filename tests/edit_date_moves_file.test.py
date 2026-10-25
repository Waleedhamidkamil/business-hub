"""An edited date must move the PDF with it (2026-08-02).

Reported: a quotation made on 30 July was edited, its date changed to August,
and after saving it sat in JUL as VIEW-ONLY. Diagnosis on the live data
(QUT-MT-TDC-043): re-saving pinned the PDF to its existing folder while the DB
row was written with the EDITED date's year+month. The right-hand list joins
the two on the month --

    entry["editable"] = entry["quote_no"] in db_rows      # api.list_quotes_for_month

-- so the document was visible-but-locked in the old month (file found, no DB
row) and absent from the new one (DB row, no file): editable from neither.

Three save paths share it: save_quote, save_dn, save_inv. (save_ltr was a
fourth until Official Letters were retired on 2026-08-27.)

These are pure-logic assertions over the helpers plus a source check that every
save path is wired. No DB writes, no files touched.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import api  # noqa: E402

SEP = "\\"
ROOT = "D:" + SEP + "X" + SEP + "QUOTATIONS"


def p(*parts):
    return SEP.join(parts)


class EditTargetFolder(unittest.TestCase):
    def test_same_folder_is_not_a_move(self):
        folder, stale = api._edit_target_folder(
            p(ROOT, "TDC", "2026", "MT", "JUL", "Q.pdf"),
            Path(p(ROOT, "TDC", "2026", "MT", "JUL")))
        self.assertEqual(folder.name, "JUL")
        self.assertIsNone(stale, "an unchanged date must not relocate anything")

    def test_month_change_moves_and_reports_the_old_file(self):
        old = p(ROOT, "TDC", "2026", "MT", "JUL", "Q.pdf")
        folder, stale = api._edit_target_folder(
            old, Path(p(ROOT, "TDC", "2026", "MT", "AUG")))
        self.assertEqual(folder.name, "AUG", "the file must follow the edited date")
        self.assertEqual(str(stale), old,
                         "the pre-move copy must be reported so it can be removed; "
                         "leaving it behind is what made the document view-only "
                         "in the old month")

    def test_case_and_trailing_slash_are_not_a_move(self):
        """Windows paths differ in case constantly. Treating that as a move would
        make every save relocate the file and delete a live document."""
        folder, stale = api._edit_target_folder(
            p("D:" + SEP + "X" + SEP + "quotations", "TDC", "2026", "MT", "JUL", "Q.pdf"),
            Path(p(ROOT, "TDC", "2026", "MT", "jul") + SEP))
        self.assertIsNone(stale)

    def test_client_change_also_relocates(self):
        """The folder is <Client>/<Year>/<Type>/<Month>; any part of it moving
        should take the file, not just the month."""
        folder, stale = api._edit_target_folder(
            p(ROOT, "TDC", "2026", "MT", "JUL", "Q.pdf"),
            Path(p(ROOT, "HDEC", "2026", "MT", "JUL")))
        self.assertIsNotNone(stale)

    def test_new_document_has_nothing_to_clean_up(self):
        folder, stale = api._edit_target_folder(None, Path(p(ROOT, "NEW")))
        self.assertEqual(folder.name, "NEW")
        self.assertIsNone(stale)


class DropStalePdf(unittest.TestCase):
    """Deleting the old copy is the destructive half. It must refuse whenever
    the new copy is not verifiably on disk - a duplicate is recoverable, a
    deleted-and-not-rewritten document is not."""

    def setUp(self):
        self.work = APP_ROOT / ".test-work" / "edit-move"
        self.work.mkdir(parents=True, exist_ok=True)
        self.old = self.work / "old.pdf"
        self.new = self.work / "new.pdf"
        self.old.write_bytes(b"%PDF-old")

    def tearDown(self):
        for f in (self.old, self.new):
            if f.exists():
                f.unlink()

    def test_removes_the_old_copy_once_the_new_one_exists(self):
        self.new.write_bytes(b"%PDF-new")
        api._drop_stale_pdf(self.old, self.new)
        self.assertFalse(self.old.exists())
        self.assertTrue(self.new.exists())

    def test_keeps_the_old_copy_when_the_new_one_is_missing(self):
        api._drop_stale_pdf(self.old, self.new)      # new was never written
        self.assertTrue(self.old.exists(),
                        "refusing to delete is the only safe behaviour when the "
                        "replacement is not there")

    def test_never_deletes_when_old_and_new_are_the_same_file(self):
        api._drop_stale_pdf(self.old, self.old)
        self.assertTrue(self.old.exists())

    def test_none_is_a_no_op(self):
        api._drop_stale_pdf(None, self.new)          # must not raise


class EverySavePathIsWired(unittest.TestCase):
    """All three save paths had the same pinned-folder line. A new one added
    later without the helper reintroduces the bug silently."""

    SAVES = ("save_quote", "save_dn", "save_inv")

    def setUp(self):
        self.src = (APP_ROOT / "backend" / "api.py").read_text(encoding="utf-8")

    def _body(self, name):
        """Source of one method, from its `def` to the next one."""
        start = self.src.index("    def %s(" % name)
        nxt = self.src.find("\n    def ", start + 1)
        return self.src[start:nxt if nxt > 0 else len(self.src)]

    def test_no_save_path_still_pins_the_folder(self):
        """Scoped to the SAVE methods on purpose. `_signed_copy_dest` also reads
        `Path(rec["pdf_path"]).parent`, and rightly so - the client's scanned
        copy belongs beside the DN it came from, not wherever a date says."""
        for name in self.SAVES:
            self.assertNotIn('folder = Path(rec["pdf_path"]).parent',
                             self._body(name),
                             "%s still pins the PDF to its old folder" % name)

    def test_each_save_path_uses_the_helper(self):
        for name in self.SAVES:
            self.assertIn("_edit_target_folder(", self._body(name),
                          "%s does not route through the helper" % name)
            self.assertIn("_drop_stale_pdf(", self._body(name),
                          "%s never removes the pre-move copy, so the old month "
                          "keeps a view-only ghost" % name)

    def test_all_three_use_the_helper(self):
        self.assertEqual(self.src.count("_edit_target_folder("), 4,
                         "expected 3 call sites (quote, dn, inv) plus the "
                         "definition; got a different count")

    def test_all_three_clean_up_afterwards(self):
        self.assertEqual(self.src.count("_drop_stale_pdf("), 4,
                         "every save path that can relocate must also remove the "
                         "old copy, or the old month keeps a view-only ghost")


if __name__ == "__main__":
    unittest.main(verbosity=2)
