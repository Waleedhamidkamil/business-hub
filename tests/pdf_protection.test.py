"""Every PDF the app makes is protected (2026-10-07).

Waleed: the stamp and signature on our documents "might get stolen". Three
layers, each pinned here on real renders of the four document types:

  1. LOCKED (backend/pdf_lock.py) - AES-256; opens with no password and prints,
     but editing, copying/extracting and commenting are not permitted; a
     machine switch turns it off.
  2. A VERIFICATION CODE on every page (backend/doc_verify.py) that changes
     when anything printed changes - and not when a cost does - and that
     `verify` recognises as genuine, as an older genuine version, or as fake.
  3. The stamp and the signature RUN INTO the printed name (signing.py) at
     both ends of the random placement, so a screenshot cannot take them clean.

Runs on an isolated profile (tests/isolated_client_data.py): nothing live is
read or written, and the secrets file it creates is a temporary one.
"""
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolated_client_data  # noqa: E402

ROOT = isolated_client_data.activate()

import fitz  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from backend import (config, doc_verify, dn_generator, inv_generator, pdf_generator,  # noqa: E402
                     pdf_lock, quote_generator, signing)

OUT = Path(tempfile.mkdtemp(prefix="pdf-protection-"))
STAMP_PX, SIGN_PX = (465, 290), (550, 150)       # unique sizes, to find them in the PDF


def png(path, size):
    w, h = size
    raw = b"".join(b"\x00" + b"\x20\x40\xa0\xff" * w for _ in range(h))
    chunk = lambda t, d: (struct.pack(">I", len(d)) + t + d
                          + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


png(signing._sig_dir() / "stamp.png", STAMP_PX)
png(signing._sig_dir() / "sig_1.png", SIGN_PX)


def overlay(kind, dy):
    """Signature 1 + stamp with the vertical roll forced to one end of its range."""
    r = signing.DEFAULTS["randomness"]
    slot = {"sig_index": 1, "stamp": True, "stamp_dx_mm": 0, "stamp_rot": 0, "sig_dx_mm": 0,
            "sig_rot": 0, "stamp_vs_sig_y_mm": 0,
            "stamp_dy_mm": dy * r["stamp_dy_mm"], "sig_dy_mm": dy * r["sig_dy_mm"]}
    return {"slots": {s: dict(slot) for s in signing.slots_for(kind)},
            "size": dict(signing.DEFAULTS["size"])}


def lines(n, qty="20"):
    return [{"item": f"Traffic Cone {i}", "description": "", "unit": "EA", "qty": qty,
             "rate": "10", "cost": "6"} for i in range(1, n + 1)]


def render(kind, path, dy=0.0, items=None, no=None):
    items = items or lines(1)
    if kind == "qut":
        quote_generator.render_quote_pdf({
            "quote_no": no or "QT-T1", "date": "5/8/2026", "quote_type": "materials",
            "client_name": "TDC", "client_address": "CSSP", "currency": "USD",
            "terms": ["Currency : USD"], "items": items, "signing_overlay": overlay(kind, dy)}, path)
    elif kind == "dn":
        dn_generator.render_dn_pdf({
            "dn_no": no or "DN-T1", "date": "5/8/2026", "client_name": "TDC", "client_address": "CSSP",
            "include_prices": False, "items": items, "signing_overlay": overlay(kind, dy)}, path)
    elif kind == "inv":
        inv_generator.render_inv_pdf({
            "inv_no": no or "INV-T1", "date": "5/8/2026", "inv_type": "materials", "client_name": "TDC",
            "lpo_no": "N/A", "client_address": "CSSP", "payment_bank_key": "cash",
            "withholding_included": True, "items": items, "signing_overlay": overlay(kind, dy)}, path)
    else:
        pdf_generator.render_pdf(no or "A-T1", "5/8/2026",
                                 [{"name": "Split unit", "description": "", "unit": "EA",
                                   "qty": "1", "company": "TDC"}], path,
                                 signing_overlay=overlay("po", dy))
    return path


KINDS = ("qut", "dn", "inv", "po")
FILES = {k: render(k, OUT / f"{k}.pdf") for k in KINDS}


def write_secrets(block):
    path = pdf_lock._secrets_path()
    raw = pdf_lock._read()
    raw[pdf_lock.SECTION] = {**(raw.get(pdf_lock.SECTION) or {}), **block}
    path.write_text(json.dumps(raw), encoding="utf-8")


class Locked(unittest.TestCase):

    def test_every_document_type_is_locked_with_aes_256(self):
        for k, f in FILES.items():
            with self.subTest(kind=k):
                r = PdfReader(str(f))
                self.assertTrue(r.is_encrypted, f"{k} went out unlocked")
                self.assertEqual(int(r.trailer["/Encrypt"]["/V"]), 5, "not AES-256")

    def test_it_opens_without_a_password_and_reads(self):
        for k, f in FILES.items():
            with self.subTest(kind=k):
                d = fitz.open(str(f))
                self.assertFalse(d.needs_pass, "a client would be asked for a password")
                self.assertTrue(d[0].get_text().strip())

    def test_printing_allowed_editing_copying_commenting_not(self):
        p = int(PdfReader(str(FILES["inv"])).trailer["/Encrypt"]["/P"])
        self.assertTrue(p & pdf_lock.P_PRINT and p & pdf_lock.P_PRINT_HIGH, "printing blocked")
        for name in ("P_MODIFY", "P_COPY", "P_ANNOTATE", "P_ASSEMBLE", "P_FILL"):
            self.assertFalse(p & getattr(pdf_lock, name), f"{name} is allowed")

    def test_the_owner_password_is_kept_out_of_git(self):
        self.assertTrue(pdf_lock.settings()["owner_password"], "no owner password was made")
        self.assertEqual(pdf_lock._secrets_path().name, "secrets.json")
        gi = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/secrets.json", gi)

    def test_the_switch_turns_it_off(self):
        write_secrets({"lock": False})
        try:
            f = render("dn", OUT / "unlocked.pdf")
            self.assertFalse(PdfReader(str(f)).is_encrypted)
        finally:
            write_secrets({"lock": True})

    def test_signing_images_are_not_tracked_by_git(self):
        gi = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/profiles/*/assets/sigs/", gi)


class Code(unittest.TestCase):

    def text(self, f):
        return [p.get_text() for p in fitz.open(str(f))]

    def test_every_page_carries_the_code(self):
        f = render("inv", OUT / "long.pdf", items=lines(45), no="INV-LONG")
        pages = self.text(f)
        self.assertGreater(len(pages), 1, "the fixture should run to several pages")
        for i, t in enumerate(pages):
            self.assertRegex(t, r"Verification: INV-LONG \| [A-Z2-7]{4}-[A-Z2-7]{4}",
                             f"page {i + 1} has no verification line")
        for k, f in FILES.items():
            with self.subTest(kind=k):
                self.assertIn("Verification: ", self.text(f)[0])

    def test_a_printed_change_changes_the_code_a_cost_does_not(self):
        base = {"inv_no": "INV-X", "date": "5/8/2026", "client_name": "TDC", "items": lines(2)}
        code = doc_verify.code_for(doc_verify.facts("inv", base))
        self.assertRegex(code, r"^[A-Z2-7]{4}-[A-Z2-7]{4}$")
        more = dict(base, items=lines(2, qty="21"))
        self.assertNotEqual(code, doc_verify.code_for(doc_verify.facts("inv", more)),
                            "an edited quantity kept the same code")
        for field, value in (("date", "6/8/2026"), ("client_name", "HDEC"), ("inv_no", "INV-Y")):
            self.assertNotEqual(code, doc_verify.code_for(doc_verify.facts("inv", dict(base, **{field: value}))),
                                f"changing {field} kept the same code")
        cheaper = dict(base, items=[dict(r, cost="1") for r in base["items"]])
        self.assertEqual(code, doc_verify.code_for(doc_verify.facts("inv", cheaper)),
                         "a cost is not printed - changing it must not break a sent document")

    def code_on(self, f):
        import re
        return re.search(r"\| ([A-Z2-7]{4}-[A-Z2-7]{4})", self.text(f)[0]).group(1)

    def test_verify_genuine_older_fake_and_unknown(self):
        first = self.code_on(render("qut", OUT / "v1.pdf", no="QT-V"))
        r = doc_verify.verify("QT-V", first.replace("-", "").lower())
        self.assertTrue(r["genuine"] and r["current"], r)

        second = self.code_on(render("qut", OUT / "v2.pdf", items=lines(1, qty="99"), no="QT-V"))
        self.assertNotEqual(first, second)
        old = doc_verify.verify("QT-V", first)
        self.assertTrue(old["genuine"], "an older genuine copy was called fake")
        self.assertFalse(old["current"], "an older copy was called the latest")
        self.assertEqual(old["latest"]["total"], 990.0, "the latest figures must be shown")

        fake = doc_verify.verify("QT-V", "AAAA-AAAA")
        self.assertFalse(fake["genuine"], "an invented code was accepted")
        self.assertEqual(fake["document"]["total"], 990.0, "a fake must be shown the real figures")

        self.assertFalse(doc_verify.verify("QT-NOPE", first)["genuine"],
                         "a code from another document was accepted")
        self.assertFalse(doc_verify.verify("QT-V", "12")["ok"])


class Overlap(unittest.TestCase):
    """The stamp and the signature both run into the printed title/name - at
    the LOW and the HIGH end of the random placement, on every template."""

    def measure(self, f):
        d = fitz.open(str(f))
        for page in reversed(list(d)):
            found = {}
            for i in page.get_image_info():
                k = {STAMP_PX: "stamp", SIGN_PX: "sign"}.get((i["width"], i["height"]))
                if k:
                    found[k] = fitz.Rect(i["bbox"])
            if len(found) == 2:
                break
        self.assertEqual(len(found), 2, "stamp or signature missing")
        x0 = min(r.x0 for r in found.values()); x1 = max(r.x1 for r in found.values())
        top = min(r.y0 for r in found.values())
        text = []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                r = fitz.Rect(ln["bbox"]); t = "".join(s["text"] for s in ln["spans"]).strip()
                if (t and r.x1 > x0 and r.x0 < x1 and r.y0 > top
                        and "Page" not in t and "Verification" not in t):
                    text.append(r)
        self.assertTrue(text, "no printed name found under the signature")
        return found, min(r.y0 for r in text), max(r.y1 for r in text)

    def test_both_run_into_the_name_at_both_ends_of_the_roll(self):
        for kind in KINDS:
            for dy in (-1.0, 1.0):
                with self.subTest(kind=kind, roll=dy):
                    f = render(kind, OUT / f"ov-{kind}-{dy}.pdf", dy=dy)
                    found, name_top, name_bottom = self.measure(f)
                    for what, r in found.items():
                        self.assertGreater(r.y1, name_top + 1,
                                           f"the {what} ends above the name - it can be "
                                           "screenshot clean")
                        self.assertLess(r.y1, name_bottom + 6,
                                        f"the {what} hangs below the name block")


if __name__ == "__main__":
    unittest.main(verbosity=2)
