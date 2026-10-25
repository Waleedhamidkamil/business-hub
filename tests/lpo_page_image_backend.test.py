"""A page of an LPO, as an image (2026-09-12).

Waleed asked whether showing the pages as images would be faster than handing
the whole PDF to pdf.js. Measured on CSSP-ADM-0010, a 614 KB two-page scan:

    the PDF, base64 for the bridge ........ 819 KB
    both pages as JPEG at 130 dpi ......... 326 KB
    ONE page, which is all a first paint needs ... 199 KB

So yes. It is safe because the viewer only DISPLAYS: `acknowledge_lpo` reads
the PDF from disk, so where a stamp lands owes nothing to what was on screen.

The catch is that rasterising needs a native library the app does not otherwise
ship, so it is OPTIONAL - without it the call reports `no_rasteriser` and the
frontend falls back to pdf.js. These tests cover both paths.

Isolation: see the note in `client_lpo_save.test.py`.
"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from isolated_client_data import activate as _isolate_client_data
_isolate_client_data()

from backend import api, client_master, config, database, profile_manager  # noqa: E402

try:
    import fitz                                   # noqa: F401
    HAVE_RASTERISER = True
except Exception:
    HAVE_RASTERISER = False


def _assert_isolated():
    real = (APP_ROOT / "data").resolve()
    for label, path in (("profile", Path(config.DB_PATH).resolve()),
                        ("shared", profile_manager.shared_db_path().resolve())):
        assert not path.is_relative_to(real), (
            f"the {label} database is NOT isolated - it points at {path}.")


_assert_isolated()
database.init_db()


def _a_real_pdf(dest: Path, noisy: bool = True) -> Path:
    """A two-page SCAN, which is what every one of these LPOs really is.

    Built here so the suite does not depend on Waleed's documents. It has to be
    image-based to be representative: a vector text PDF is a couple of KB, and
    rendering THAT to an image is naturally bigger. The saving this whole
    feature rests on applies to scans - his are 600 KB of embedded photograph.
    """
    import fitz, random
    from PIL import Image
    doc = fitz.open()
    rnd = random.Random(7)
    for n in (1, 2):
        page = doc.new_page()                     # A4 by default
        # Noise so the JPEG cannot compress to nothing, the way a real scan of
        # a printed page cannot.
        img = Image.new("RGB", (900, 1270), "white")
        px = img.load()
        if noisy:
            for _ in range(90000):
                px[rnd.randrange(900), rnd.randrange(1270)] = (
                    rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        buf = io.BytesIO(); img.save(buf, "PNG")
        page.insert_image(page.rect, stream=buf.getvalue())
        page.insert_text((72, 120), f"PURCHASE ORDER - page {n}", fontsize=18)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    doc.close()
    return dest


@unittest.skipUnless(HAVE_RASTERISER, "no PDF rasteriser installed")
class LpoPageImage(unittest.TestCase):
    def setUp(self):
        _assert_isolated()
        with database._connect() as conn:
            conn.execute("DELETE FROM client_lpos")
        if not client_master.get_record("HDEC").get("record"):
            client_master.save_record({"name": "HDEC"})
        self.bridge = api.Api()
        self.pdf = _a_real_pdf(Path(config.DATA_DIR) / "docs" / "CSSP-ADM-0010.pdf")
        self.assertTrue(database.save_lpo(
            client_name="HDEC", lpo_no="CSSP-ADM-0010", kind="materials")["ok"])
        database.set_lpo_file("HDEC", "CSSP-ADM-0010", self.pdf, "file")

    def get(self, **kw):
        args = {"client_name": "HDEC", "lpo_no": "CSSP-ADM-0010", "page": 0}
        args.update(kw)
        return self.bridge.lpo_page_image(**args)

    def test_a_page_comes_back_as_an_image(self):
        r = self.get()
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["data_uri"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(0, r["page"])
        self.assertEqual(2, r["pages"], "the caller needs to know there is a page 2")

    def test_it_is_far_smaller_than_the_whole_scan(self):
        """The entire point, and it only holds for SCANS - which these are.
        A vector text PDF is already a couple of KB and rendering it to an
        image would be bigger, so the fixture is image-based like the real
        documents."""
        page = len(self.get()["data_uri"])
        whole = len(self.bridge.lpo_file_data_uri(
            "HDEC", "CSSP-ADM-0010", "file")["data_uri"])
        self.assertLess(page, whole,
                        f"one page as an image ({page // 1024} KB) is not smaller "
                        f"than the whole scan ({whole // 1024} KB)")

    def test_the_page_size_is_read_from_the_document(self):
        """Not assumed to be A4 - a Letter or A5 scan must report its own size,
        because the placement readout is in millimetres."""
        mm = self.get()["mm"]
        self.assertAlmostEqual(210, mm["w"], delta=1)
        self.assertAlmostEqual(297, mm["h"], delta=1)

    def test_the_second_page_is_a_different_image(self):
        self.assertNotEqual(self.get(page=0)["data_uri"], self.get(page=1)["data_uri"])

    def test_a_page_past_the_end_says_so(self):
        r = self.get(page=9)
        self.assertFalse(r["ok"])
        self.assertIn("2", r["error"], "the message should say how many there are")

    def test_the_second_call_is_served_from_cache(self):
        first = self.get()
        with patch("fitz.Pixmap") as never:
            second = self.get()
            self.assertFalse(never.called, "the page was rendered a second time")
        self.assertEqual(first["data_uri"], second["data_uri"])

    def test_replacing_the_document_invalidates_the_cache(self):
        """The cache key carries the file's size and modification time, so a
        replaced document can never be served as the old one - which would show
        the WRONG purchase order."""
        before = self.get()["data_uri"]
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 120), "A COMPLETELY DIFFERENT PO", fontsize=18)
        doc.save(str(self.pdf), incremental=False)
        doc.close()
        self.assertNotEqual(before, self.get()["data_uri"],
                            "a replaced document served its stale image")

    def test_an_lpo_with_no_document_says_so(self):
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-ADM-0017")
        r = self.get(lpo_no="CSSP-ADM-0017")
        self.assertFalse(r["ok"])
        self.assertIn("attached", r["error"])

    def test_an_lpo_that_is_not_on_file_says_so(self):
        r = self.get(lpo_no="NOPE-0001")
        self.assertFalse(r["ok"])

    def test_a_photographed_lpo_is_handed_over_as_it_is(self):
        """Rasterising a raster would only lose quality."""
        png = Path(config.DATA_DIR) / "docs" / "scan.png"
        import fitz
        doc = fitz.open(str(self.pdf))
        doc[0].get_pixmap(dpi=90).save(str(png))
        doc.close()
        database.save_lpo(client_name="HDEC", lpo_no="CSSP-RENT-0001")
        database.set_lpo_file("HDEC", "CSSP-RENT-0001", png, "file")
        r = self.get(lpo_no="CSSP-RENT-0001")
        self.assertTrue(r["ok"], r)
        self.assertIn("image/png", r["data_uri"])


class WithoutARasteriser(unittest.TestCase):
    """The app ships almost no native code on purpose, so this has to degrade."""

    def test_it_reports_no_rasteriser_rather_than_failing(self):
        import builtins
        real = builtins.__import__

        def no_fitz(name, *a, **kw):
            if name == "fitz":
                raise ImportError("no module named fitz")
            return real(name, *a, **kw)

        with patch.object(builtins, "__import__", side_effect=no_fitz):
            r = api.Api().lpo_page_image("HDEC", "CSSP-ADM-0010", 0)
        self.assertFalse(r["ok"])
        self.assertEqual("no_rasteriser", r.get("reason"),
                         "the frontend keys its pdf.js fallback on this")


if __name__ == "__main__":
    unittest.main(verbosity=2)
