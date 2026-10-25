"""
LPOs received from clients (2026-07-31).

The chain is quotation (ours) -> LPO (theirs) -> delivery note (ours) ->
invoice (ours). The LPO is the only one we do NOT produce, so it is a
received-document store rather than a sixth document type: no numbering (the
client assigns it), no generator, no template.

Covers:
  1. Keyed by (client, LPO no) - the number is the CLIENT's, so two clients can
     both send "PO-001" and they must not collide.
  2. Value is USD-canonical alongside what the client wrote, exactly as
     invoices.grand_total is, so a dinar LPO can be summed with a dollar one.
  3. The status ladder and the invoiced/remaining rollup.
  4. Acknowledgement: our signature/stamp land where the viewer said, the
     client's original is never modified, and rotation is handled.
  5. Attaching a replacement document invalidates a stale acknowledged copy.

!! Runs against the LIVE profile DB (no test isolation in this project). Every
!! row uses the __LPOTEST__ sentinel and is removed by targeted DELETE, and the
!! files live in the scratch .test-work tree. See the warning in docs/11.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, config, database, lpo_ack, pdf_engine, signing  # noqa: E402

TAG = "__LPOTEST__"
CLIENT_A = f"LPO Client A {TAG}"
CLIENT_B = f"LPO Client B {TAG}"
SHARED_NO = f"PO-001-{TAG}"


def _register_test_clients():
    """Put the sentinel clients on file.

    Since 2026-09-11 `Api.save_lpo` refuses a client that is not in Clients
    Master Data, the same rule the quotation, DN and invoice saves follow. This
    suite's whole subject IS that save path, so the guard must NOT be patched
    out here - the clients are registered instead. Safe because
    `isolated_client_data.activate()` above has already redirected the shared
    library into a temporary directory; nothing reaches Waleed's real one.
    """
    from backend import client_master, shared_library
    shared_library.init_shared_db()
    for name in (CLIENT_A, CLIENT_B):
        client_master.save_record({"name": name})


_register_test_clients()
WORK = APP / ".test-work" / "client-lpo"


def cleanup():
    with sqlite3.connect(config.DB_PATH) as c:
        c.execute("DELETE FROM client_lpos WHERE client_name LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM dns WHERE dn_no LIKE ?", (f"%{TAG}%",))
        c.execute("DELETE FROM invoices WHERE inv_no LIKE ?", (f"%{TAG}%",))


def setUpModule():
    cleanup()
    WORK.mkdir(parents=True, exist_ok=True)


def tearDownModule():
    cleanup()
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)


class KeyedByClientAndNumber(unittest.TestCase):
    def tearDown(self):
        cleanup()

    def test_two_clients_may_share_an_lpo_number(self):
        """The regression this key exists to prevent."""
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000)
        database.save_lpo(CLIENT_B, SHARED_NO, total=2000)
        a = database.load_lpo(CLIENT_A, SHARED_NO)
        b = database.load_lpo(CLIENT_B, SHARED_NO)
        self.assertAlmostEqual(a["total"], 1000, places=2)
        self.assertAlmostEqual(b["total"], 2000, places=2)

    def test_resave_updates_in_place(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000, lpo_date="1/7/2026")
        database.save_lpo(CLIENT_A, SHARED_NO, total=1500, lpo_date="2/7/2026")
        rec = database.load_lpo(CLIENT_A, SHARED_NO)
        self.assertAlmostEqual(rec["total"], 1500, places=2)
        self.assertEqual(rec["lpo_date"], "2/7/2026")
        rows = [r for r in database.list_lpos() if TAG in r["client_name"]]
        self.assertEqual(len(rows), 1, "re-save must not create a second row")

    def test_client_and_number_are_required(self):
        self.assertFalse(database.save_lpo("", "X-1")["ok"])
        self.assertFalse(database.save_lpo(CLIENT_A, "  ")["ok"])

    def test_missing_lpo_loads_as_none(self):
        self.assertIsNone(database.load_lpo(CLIENT_A, "NOPE"))

    def test_delete_forgets_the_record(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=10)
        self.assertTrue(database.delete_lpo(CLIENT_A, SHARED_NO)["ok"])
        self.assertIsNone(database.load_lpo(CLIENT_A, SHARED_NO))


class ValueIsUsdCanonical(unittest.TestCase):
    def tearDown(self):
        cleanup()

    def test_usd_lpo_is_unchanged(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=5000, currency="USD")
        rec = database.load_lpo(CLIENT_A, SHARED_NO)
        self.assertEqual(rec["currency"], "USD")
        self.assertAlmostEqual(rec["total_usd"], 5000, places=2)

    def test_iqd_lpo_keeps_native_and_stores_usd(self):
        native = 7_000_000.0
        database.save_lpo(CLIENT_A, SHARED_NO, total=native, currency="IQD")
        rec = database.load_lpo(CLIENT_A, SHARED_NO)
        self.assertAlmostEqual(rec["total"], native, places=2)
        self.assertAlmostEqual(rec["total_usd"], native / config.IQD_PER_USD, places=2)
        self.assertLess(rec["total_usd"], 100_000)

    def test_unknown_currency_falls_back_to_usd(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=100, currency="EUR")
        self.assertEqual(database.load_lpo(CLIENT_A, SHARED_NO)["currency"], "USD")


class StatusLadderAndRollup(unittest.TestCase):
    def tearDown(self):
        cleanup()

    def _row(self):
        return next(r for r in database.list_lpos()
                    if r["client_name"] == CLIENT_A and r["lpo_no"] == SHARED_NO)

    def test_fresh_lpo_is_received(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000)
        r = self._row()
        self.assertEqual(r["status"], "received")
        self.assertEqual(r["invoiced_usd"], 0.0)
        self.assertAlmostEqual(r["remaining_usd"], 1000, places=2)

    def test_a_linked_dn_makes_it_delivered(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000)
        database.save_dn_record(
            dn_no=f"DN-{TAG}-1", dn_date="1/7/2026", year=2026, month=7,
            client_name=CLIENT_A, lpo_no=SHARED_NO, client_address="",
            include_prices=True, pdf_path=Path("x.pdf"),
            rows=[{"item": "A", "qty": "1", "rate": "10"}], source_quote_no=None)
        r = self._row()
        self.assertEqual(r["status"], "delivered")
        self.assertEqual(r["dns"], [f"DN-{TAG}-1"])

    def test_invoices_roll_up_and_close_it(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000)
        database.save_inv_record(
            inv_no=f"INV-{TAG}-1", inv_date="1/7/2026", inv_type="materials",
            year=2026, month=7, client_name=CLIENT_A, lpo_no=SHARED_NO,
            client_address="", payment_bank_key="", withholding_included=True,
            grand_total=400.0, pdf_path=Path("x.pdf"), rows=[])
        r = self._row()
        self.assertEqual(r["status"], "invoiced")
        self.assertAlmostEqual(r["invoiced_usd"], 400, places=2)
        self.assertAlmostEqual(r["remaining_usd"], 600, places=2)

        database.save_inv_record(
            inv_no=f"INV-{TAG}-2", inv_date="1/7/2026", inv_type="materials",
            year=2026, month=7, client_name=CLIENT_A, lpo_no=SHARED_NO,
            client_address="", payment_bank_key="", withholding_included=True,
            grand_total=600.0, pdf_path=Path("x.pdf"), rows=[])
        r = self._row()
        self.assertAlmostEqual(r["remaining_usd"], 0, places=2)
        # Fully invoiced is not finished until the invoices are SENT
        # (Waleed's rule, 2026-10-06).
        self.assertEqual(r["status"], "invoiced")
        self.assertEqual(r["next_step"]["key"], "not_sent")
        database.set_submitted("inv", f"INV-{TAG}-1", True)
        database.set_submitted("inv", f"INV-{TAG}-2", True)
        r = self._row()
        self.assertEqual(r["next_step"]["key"], "done")
        self.assertEqual(r["status"], "closed")

    def test_another_clients_same_number_does_not_leak(self):
        """The rollup is matched on client AND number, not the number alone."""
        database.save_lpo(CLIENT_A, SHARED_NO, total=1000)
        database.save_lpo(CLIENT_B, SHARED_NO, total=1000)
        database.save_inv_record(
            inv_no=f"INV-{TAG}-B", inv_date="1/7/2026", inv_type="materials",
            year=2026, month=7, client_name=CLIENT_B, lpo_no=SHARED_NO,
            client_address="", payment_bank_key="", withholding_included=True,
            grand_total=999.0, pdf_path=Path("x.pdf"), rows=[])
        a = self._row()
        self.assertEqual(a["invoiced_usd"], 0.0, "client B's invoice leaked into A")

    def test_na_lpo_references_are_ignored(self):
        """Every existing DN carries 'N/A' - those must not match anything."""
        database.save_lpo(CLIENT_A, "N/A", total=50)
        database.save_dn_record(
            dn_no=f"DN-{TAG}-NA", dn_date="1/7/2026", year=2026, month=7,
            client_name=CLIENT_A, lpo_no="N/A", client_address="",
            include_prices=False, pdf_path=Path("x.pdf"), rows=[], source_quote_no=None)
        row = next(r for r in database.list_lpos()
                   if r["client_name"] == CLIENT_A and r["lpo_no"] == "N/A")
        self.assertEqual(row["dns"], [])


class Acknowledgement(unittest.TestCase):
    """Counter-sign the client's own document at the spot the user picked."""

    @classmethod
    def setUpClass(cls):
        if not (signing._sig_dir() / signing.SIG_FILES[1]).exists():
            raise unittest.SkipTest("no signature image uploaded in this profile")
        WORK.mkdir(parents=True, exist_ok=True)
        try:
            cls.src = Path(pdf_engine.write_pdf(
                "<html><body><h1>CLIENT LPO 998</h1>"
                "<div style='page-break-after:always'></div>"
                "<p>SECOND PAGE - acknowledgement block</p></body></html>",
                WORK / "client.pdf"))
        except Exception as e:
            raise unittest.SkipTest(f"PDF engine unavailable: {e}")

    def tearDown(self):
        cleanup()

    def test_marks_land_on_the_requested_page_only(self):
        from pypdf import PdfReader
        dest = WORK / "ack1.pdf"
        lpo_ack.write_acknowledged(self.src, dest, [
            {"page": 1, "kind": "sig", "x": 0.1, "y": 0.8, "w": 0.26, "h": 0.07},
        ])
        r = PdfReader(str(dest))
        self.assertEqual(len(r.pages), 2)
        # Page 1 untouched, page 2 gained the overlay: compare drawn XObjects.
        self.assertEqual(len(r.pages[0].images), 0)
        self.assertGreaterEqual(len(r.pages[1].images), 1)

    def test_client_original_is_never_modified(self):
        before = self.src.read_bytes()
        lpo_ack.write_acknowledged(self.src, WORK / "ack2.pdf", [
            {"page": 0, "kind": "sig", "x": 0.2, "y": 0.5, "w": 0.2, "h": 0.06}])
        self.assertEqual(self.src.read_bytes(), before,
                         "the client's file must stay byte-identical")

    def test_client_content_survives(self):
        from pypdf import PdfReader
        dest = WORK / "ack3.pdf"
        lpo_ack.write_acknowledged(self.src, dest, [
            {"page": 0, "kind": "stamp", "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.09}])
        self.assertIn("CLIENT LPO", PdfReader(str(dest)).pages[0].extract_text() or "")

    def test_no_temp_overlay_files_are_left_behind(self):
        lpo_ack.write_acknowledged(self.src, WORK / "ack4.pdf", [
            {"page": 0, "kind": "sig", "x": 0.2, "y": 0.5, "w": 0.2, "h": 0.06}])
        self.assertEqual(sorted(p.name for p in WORK.glob("*._ov*")), [])
        self.assertEqual(sorted(p.name for p in WORK.glob("*._base*")), [])

    def test_placement_without_a_usable_asset_raises(self):
        with self.assertRaises(ValueError):
            lpo_ack.write_acknowledged(self.src, WORK / "ack5.pdf", [
                {"page": 0, "kind": "not-a-kind", "x": 0.1, "y": 0.1}])

    def test_reports_how_many_marks_actually_landed(self):
        """A silent partial apply is the failure you only notice when the client
        asks where the stamp is - the count is what lets the UI refuse to lie."""
        res = lpo_ack.write_acknowledged(self.src, WORK / "ack6.pdf", [
            {"page": 0, "kind": "sig", "x": 0.1, "y": 0.7, "w": 0.26, "h": 0.07},
            {"page": 0, "kind": "stamp", "x": 0.5, "y": 0.66, "w": 0.22, "h": 0.09},
        ])
        self.assertEqual(res["applied"], 2)
        self.assertEqual(res["requested"], 2)
        from pypdf import PdfReader
        self.assertEqual(len(PdfReader(res["path"]).pages[0].images), 2,
                         "both marks must reach the page, not just the last one")

    def test_a_mark_past_the_last_page_is_not_counted_as_applied(self):
        res = lpo_ack.write_acknowledged(self.src, WORK / "ack7.pdf", [
            {"page": 0, "kind": "sig", "x": 0.1, "y": 0.7, "w": 0.26, "h": 0.07},
            {"page": 99, "kind": "stamp", "x": 0.5, "y": 0.6, "w": 0.22, "h": 0.09},
        ])
        self.assertEqual(res["applied"], 1)
        self.assertEqual(res["requested"], 2)

    def test_default_mark_size_tracks_the_signing_settings(self):
        stamp = lpo_ack.default_mark_size("stamp")
        sig = lpo_ack.default_mark_size("sig")
        # Fractions of A4 (210x297mm); the stamp is the taller of the two.
        for box in (stamp, sig):
            self.assertGreater(box["w"], 0); self.assertLess(box["w"], 1)
            self.assertGreater(box["h"], 0); self.assertLess(box["h"], 1)
        self.assertGreater(stamp["h"], sig["h"])


class BridgeFlow(unittest.TestCase):
    """The path the UI actually takes."""

    def setUp(self):
        cleanup()
        WORK.mkdir(parents=True, exist_ok=True)
        self.api = api.Api()

    def tearDown(self):
        cleanup()

    def test_attach_requires_the_record_first(self):
        r = self.api.attach_lpo_file_data(CLIENT_A, SHARED_NO, "data:image/png;base64,AAAA", "x.png")
        self.assertFalse(r["ok"])
        self.assertIn("save the LPO details first", r["error"])

    def test_rejects_an_unsupported_file_type(self):
        self.api.save_lpo({"client_name": CLIENT_A, "lpo_no": SHARED_NO})
        r = self.api.attach_lpo_file_data(
            CLIENT_A, SHARED_NO, "data:application/zip;base64,AAAA", "x.zip")
        self.assertFalse(r["ok"])
        self.assertIn("PDF, PNG or JPG", r["error"])

    def test_acknowledge_needs_a_document(self):
        self.api.save_lpo({"client_name": CLIENT_A, "lpo_no": SHARED_NO})
        r = self.api.acknowledge_lpo(CLIENT_A, SHARED_NO, [{"page": 0, "kind": "sig"}])
        self.assertFalse(r["ok"])
        self.assertIn("attach the client's LPO document first", r["error"])

    def test_acknowledge_needs_a_placement(self):
        """Checked AFTER the document, because you can't place a mark on
        nothing - so attach one first to reach this branch."""
        import base64
        self.api.save_lpo({"client_name": CLIENT_A, "lpo_no": SHARED_NO})
        png = (WORK / "tiny.png")
        from PIL import Image
        Image.new("RGB", (40, 40), (255, 255, 255)).save(png)
        uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
        self.assertTrue(self.api.attach_lpo_file_data(
            CLIENT_A, SHARED_NO, uri, "tiny.png")["ok"])
        r = self.api.acknowledge_lpo(CLIENT_A, SHARED_NO, [])
        self.assertFalse(r["ok"])
        self.assertIn("place the signature or stamp", r["error"])

    def test_a_filename_lie_cannot_smuggle_a_type_in(self):
        """`_clean_suffix` alone would fall back to '.pdf' and store a zip AS a
        pdf - the attach path must reject on the MIME too."""
        self.api.save_lpo({"client_name": CLIENT_A, "lpo_no": SHARED_NO})
        r = self.api.attach_lpo_file_data(
            CLIENT_A, SHARED_NO, "data:application/zip;base64,AAAA", "invoice")
        self.assertFalse(r["ok"])

    def test_data_uri_reports_a_missing_file(self):
        self.api.save_lpo({"client_name": CLIENT_A, "lpo_no": SHARED_NO})
        r = self.api.lpo_file_data_uri(CLIENT_A, SHARED_NO)
        self.assertFalse(r["ok"])

    def test_suggest_scopes_to_the_client(self):
        database.save_lpo(CLIENT_A, SHARED_NO, total=1)
        database.save_lpo(CLIENT_B, f"OTHER-{TAG}", total=1)
        got = [x["lpo_no"] for x in self.api.suggest_lpos(CLIENT_A, "")]
        self.assertIn(SHARED_NO, got)
        self.assertNotIn(f"OTHER-{TAG}", got)


if __name__ == "__main__":
    unittest.main(verbosity=2)
