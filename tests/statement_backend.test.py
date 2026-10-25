"""Monthly client statement (backend/statements.py, 2026-10-06).

Waleed sends a client one PDF a month: a summary page of that month's invoices,
then every invoice, delivery note and purchase order it names, each number a
link to its pages. What must hold:

  1. The statement covers exactly this client's ISSUED invoices of that month -
     no draft, no other client, no other month - in date order.
  2. A PAID invoice carries the client's returned copy when one is on file; an
     UNPAID one always carries the invoice we issued, even if a copy is on file.
     A paid invoice with no returned copy is named on the summary page.
  3. Its money is the Insights money (`insights._collect`), never its own sum.
  4. A purchase order dated this month but invoiced in another month is listed,
     so the month's orders can all be found.
  5. In the PDF every link jumps to the FIRST page of the document it names,
     every document is present in full, a photo becomes one page, and a
     document whose file is missing is reported and not linked.

Runs on an isolated profile (tests/isolated_client_data.py) - nothing live is
read or written. Each fixture PDF has its own page WIDTH, so a link's target
page can be identified without any text on it.
"""
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolated_client_data  # noqa: E402

ROOT = isolated_client_data.activate()

import sqlite3  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402

from backend import config, insights, pdf_lock, statements  # noqa: E402

FILES = ROOT / "statement-fixtures"
FILES.mkdir(exist_ok=True)
WIDTH = {}           # document number -> the width of its pages


def pdf(no, pages, width):
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=width, height=842)
    path = FILES / f"{no}.pdf"
    with path.open("wb") as fh:
        w.write(fh)
    WIDTH[no] = width
    return str(path)


def png(no):
    """A 40x60 grey PNG - enough to be a returned photo."""
    raw = b"".join(b"\x00" + b"\x80" * 40 for _ in range(60))
    chunk = lambda t, d: (struct.pack(">I", len(d)) + t + d
                          + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 40, 60, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    path = FILES / f"{no}.png"
    path.write_bytes(data)
    return str(path)


def row(item, qty, rate, cost):
    return ('[{"item": "%s", "description": "", "unit": "EA", "qty": "%s", '
            '"rate": "%s", "cost": "%s"}]' % (item, qty, rate, cost))


def seed():
    inv = [
        # no, date, y, m, client, pdf, rows, dn
        ("INV-A-2", "20/8/2026", 2026, 8, "ACME", pdf("INV-A-2", 1, 500), row("Rope", 10, 5, 3), "DN-A-1"),
        ("INV-A-1", "5/8/2026", 2026, 8, "ACME", pdf("INV-A-1", 2, 510), row("Cone", 20, 10, 6), ""),
        ("INV-A-3", "28/8/2026", 2026, 8, "ACME", pdf("INV-A-3", 1, 520), row("Pipe", 4, 25, ""), ""),
        ("INV-A-DRAFT", "29/8/2026", 2026, 8, "ACME", "", row("Draft", 1, 99, 1), ""),
        ("INV-B-1", "6/8/2026", 2026, 8, "OTHER CO", pdf("INV-B-1", 1, 530), row("Sand", 1, 50, 10), ""),
        ("INV-A-SEP", "3/9/2026", 2026, 9, "ACME", pdf("INV-A-SEP", 1, 540), row("Lamp", 2, 15, 9), ""),
    ]
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        for no, date, y, m, client, path, rows, dn in inv:
            c.execute("INSERT INTO invoices(created_at, updated_at, inv_no, inv_date, inv_type, year, month, client_name, "
                      "grand_total, pdf_path, rows_json, source_dn_no) VALUES('x','x',?,?,?,?,?,?,?,?,?,?)",
                      (no, date, "materials", y, m, client, 0, path, rows, dn or None))
        c.execute("INSERT INTO dns(created_at, updated_at, dn_no, dn_date, year, month, client_name, pdf_path, rows_json) "
                  "VALUES('x','x','DN-A-1','18/8/2026',2026,8,'ACME',?,'[]')",
                  # Every PDF the app makes is locked (pdf_lock.py) - the statement
                  # must still be able to read one.
                  (str(pdf_lock.lock(pdf("DN-A-1", 1, 560))),))
        for no, date, y, m, w in (("A-10", "1/8/2026", 2026, 8, 570), ("A-11", "2/8/2026", 2026, 8, 580),
                                  ("A-12", "30/8/2026", 2026, 8, 590)):
            c.execute("INSERT INTO pos(created_at, updated_at, po_no, po_date, year, month, pdf_path, rows_json) "
                      "VALUES('x','x',?,?,?,?,?,'[]')", (no, date, y, m, pdf(no, 1, w)))
        for k, (po, inv_no) in enumerate((("A-10", "INV-A-1"), ("A-11", "INV-A-1"), ("A-11", "INV-A-2"),
                           ("A-12", "INV-A-SEP"))):
            c.execute("INSERT INTO po_fulfillment(po_no, item_index, status_kind, source_kind, "
                      "source_no, marked_at) VALUES(?,?,'paid','inv',?,'2026-08-30')", (po, k, inv_no))
        # INV-A-1 paid, returned as a 3-page scan; INV-A-2 paid, returned as a
        # photo; INV-A-3 UNPAID with a returned copy on file (must not be used).
        for no in ("INV-A-1", "INV-A-2"):
            c.execute("INSERT INTO paid_invoices(inv_no, paid_at) VALUES(?, '2026-09-01')", (no,))
        c.execute("INSERT INTO signed_copies(kind, doc_no, path, attached_at) VALUES('inv','INV-A-1',?,'x')",
                  (pdf("INV-A-1.signed", 3, 600),))
        c.execute("INSERT INTO signed_copies(kind, doc_no, path, attached_at) VALUES('inv','INV-A-2',?,'x')",
                  (png("INV-A-2.signed"),))
        c.execute("INSERT INTO signed_copies(kind, doc_no, path, attached_at) VALUES('inv','INV-A-3',?,'x')",
                  (pdf("INV-A-3.signed", 1, 610),))
        c.commit()


seed()


class Gather(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.d = statements.gather("ACME", 2026, 8)
        cls.by = {r["no"]: r for r in cls.d["rows"]}

    def test_only_this_clients_issued_invoices_of_the_month_in_date_order(self):
        self.assertEqual([r["no"] for r in self.d["rows"]], ["INV-A-1", "INV-A-2", "INV-A-3"],
                         "no draft, no other client, no other month - and by date, not by number")

    def test_a_paid_invoice_uses_its_returned_copy(self):
        self.assertTrue(self.by["INV-A-1"]["copy"].endswith("INV-A-1.signed.pdf"))
        self.assertTrue(self.by["INV-A-1"]["paid_copy_used"])

    def test_an_unpaid_invoice_uses_the_one_we_issued(self):
        r = self.by["INV-A-3"]
        self.assertTrue(r["copy"].endswith("INV-A-3.pdf"),
                        "an unpaid invoice was sent with a returned copy: " + r["copy"])
        self.assertFalse(r["paid_copy_used"])

    def test_the_money_is_the_insights_money(self):
        docs, _ = insights._collect([(2026, 8)], "ACME")
        charged = sum(d["charged"] for d in docs if d["kind"] == "inv")
        spent = sum(d["spent"] for d in docs if d["kind"] == "inv")
        self.assertAlmostEqual(self.d["charged"], charged, places=6)
        self.assertAlmostEqual(self.d["cost"], spent, places=6)
        self.assertAlmostEqual(self.d["charged"], 200 + 50 + 100, places=6)
        self.assertAlmostEqual(self.d["paid_usd"], 250, places=6)
        self.assertAlmostEqual(self.d["pending_usd"], 100, places=6)

    def test_an_uncosted_invoice_is_named(self):
        self.assertEqual(self.d["uncosted"], ["INV-A-3"])

    def test_delivery_note_and_purchase_orders_are_linked_to_their_invoice(self):
        self.assertEqual(self.by["INV-A-2"]["dn"], "DN-A-1")
        self.assertEqual([p for p, _ in self.by["INV-A-1"]["pos"]], ["A-10", "A-11"])
        self.assertEqual([p for p, _ in self.by["INV-A-2"]["pos"]], ["A-11"])

    def test_an_order_of_this_month_invoiced_in_another_is_listed(self):
        self.assertEqual([x["po"] for x in self.d["outside"]], ["A-12"])
        self.assertEqual(self.d["outside"][0]["invoiced_on"][0][0], "INV-A-SEP")

    def test_each_document_is_attached_once(self):
        nos = [a["no"] for a in self.d["attach"]]
        self.assertEqual(nos, ["INV-A-1", "INV-A-2", "INV-A-3", "DN-A-1", "A-10", "A-11", "A-12"],
                         "A-11 is named by two invoices but must be attached once")


class Build(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "ACME - August 2026.pdf"
        cls.r = statements.build("ACME", 2026, 8, cls.out)
        cls.pdf = PdfReader(str(cls.out))

    @classmethod
    def tearDownClass(cls):
        cls.pdf = None
        cls.tmp.cleanup()

    def width(self, i):
        return round(float(self.pdf.pages[i].mediabox.width))

    def test_it_is_made(self):
        self.assertTrue(self.r["ok"], self.r)
        self.assertEqual((self.r["invoices"], self.r["paid"], self.r["pending"]), (3, 2, 1))

    def test_every_document_is_there_in_full(self):
        cover = self.r["cover_pages"]
        # 3-page paid scan + photo + issued INV-A-3 + DN + three POs
        self.assertEqual(self.r["pages"], cover + 3 + 1 + 1 + 1 + 3)
        self.assertEqual(len(self.pdf.pages), self.r["pages"])

    def test_every_link_jumps_to_the_first_page_of_its_document(self):
        ids = {p.indirect_reference.idnum: i for i, p in enumerate(self.pdf.pages)}
        targets = []
        for ref in self.pdf.pages[0].get("/Annots") or []:
            a = ref.get_object()
            self.assertNotIn("/A", a, "a placeholder web link was left in the PDF")
            targets.append(ids[a["/Dest"][0].idnum])
        expected = {
            "INV-A-1": WIDTH["INV-A-1.signed"],
            "INV-A-3": WIDTH["INV-A-3"],
            "DN-A-1": WIDTH["DN-A-1"],
            "A-10": WIDTH["A-10"], "A-11": WIDTH["A-11"], "A-12": WIDTH["A-12"],
        }
        got = sorted(self.width(t) for t in targets)
        # the photo page is A4 (595), every other target is its file's own width
        self.assertEqual(got, sorted(list(expected.values()) + [595, WIDTH["A-11"]]),
                         "each link must land on its own document; A-11 appears on two rows")
        for t in targets:
            self.assertTrue(t == 0 or self.width(t - 1) != self.width(t) or self.width(t) == 595,
                            f"link to page {t + 1} is not the FIRST page of its document")

    def test_a_photo_becomes_one_a4_page(self):
        a4 = [i for i in range(len(self.pdf.pages)) if self.width(i) == 595
              and round(float(self.pdf.pages[i].mediabox.height)) == 842]
        self.assertEqual(len(a4), 1, "the returned photo must be exactly one A4 page")

    def test_a_locked_document_is_still_included(self):
        self.assertTrue(pdf_lock.is_locked(FILES / "DN-A-1.pdf"), "the fixture is not locked")
        self.assertIn(WIDTH["DN-A-1"], [self.width(i) for i in range(len(self.pdf.pages))])

    def test_the_statement_itself_is_locked(self):
        self.assertTrue(self.pdf.is_encrypted, "the statement went out unlocked")

    def test_every_document_has_a_bookmark(self):
        self.assertEqual(len(self.pdf.outline), 1 + 7, "Summary plus one per document")


class Missing(unittest.TestCase):

    def test_a_missing_file_is_reported_and_not_linked(self):
        path = Path(config.DB_PATH)
        with closing(sqlite3.connect(path)) as c:
            c.execute("UPDATE dns SET pdf_path = ? WHERE dn_no = 'DN-A-1'",
                      (str(FILES / "gone.pdf"),))
            c.commit()
        try:
            with tempfile.TemporaryDirectory() as t:
                r = statements.build("ACME", 2026, 8, Path(t) / "s.pdf")
                self.assertTrue(r["ok"], r)
                self.assertEqual(r["missing"], ["DN-A-1"])
                self.assertEqual(r["documents"], 6)
                reader = PdfReader(str(Path(t) / "s.pdf"))
                # 6 documents, 7 links: A-11 is named on two invoice rows.
                self.assertEqual(len(reader.pages[0].get("/Annots") or []), 7,
                                 "a document that is not in the file must not be a link")
                text = reader.pages[0].extract_text()
                self.assertIn("File not found, not included: DN-A-1", text)
                reader = None
        finally:
            with closing(sqlite3.connect(path)) as c:
                c.execute("UPDATE dns SET pdf_path = ? WHERE dn_no = 'DN-A-1'",
                          (str(FILES / "DN-A-1.pdf"),))
                c.commit()


class Months(unittest.TestCase):

    def test_months_with_issued_invoices_newest_first(self):
        got = [(m["year"], m["month"], m["invoices"]) for m in statements.statement_months("ACME")]
        self.assertEqual(got, [(2026, 9, 1), (2026, 8, 3)], "the draft must not be counted")

    def test_a_month_with_nothing_issued_makes_no_file(self):
        with tempfile.TemporaryDirectory() as t:
            r = statements.build("ACME", 2026, 7, Path(t) / "s.pdf")
            self.assertFalse(r["ok"])
            self.assertFalse((Path(t) / "s.pdf").exists())

    def test_it_is_filed_under_reports_year_month(self):
        p = statements.output_path("ACME", 2026, 8)
        self.assertEqual(p.parts[-4:], ("REPORTS", "2026", "AUG", "ACME - August 2026.pdf"))
        self.assertEqual(statements.output_path('A/B: "C"', 2026, 9).name, "A-B- -C- - September 2026.pdf")


if __name__ == "__main__":
    unittest.main(verbosity=2)
