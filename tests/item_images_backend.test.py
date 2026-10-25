"""Content-addressed store for remembered item pictures (2026-08-05).

Autocomplete now restores an item's photo. Keeping the base64 in
`quote_items.data_json` was tried and removed once already - an equipment quote
saved 200KB+ per row and the shared library ballooned - so the bytes live on
disk under their own SHA-256 and the item remembers a short ref.

The security half is why this file is longer than the feature deserves: a ref
comes back OUT of the database and is turned into a file path that the app then
reads and hands to the UI. A crafted or merely corrupted ref must never be able
to reach a file outside the store. Both `_safe_name` and `path_for`'s
containment check are exercised below, individually, so that removing either
one fails a test rather than quietly widening what is readable.

Uses a temp store; never touches the real one.
"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import item_images as ii  # noqa: E402

ONE_PX_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4"
    "2mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
OTHER_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADElEQVR4"
    "2mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


class StoreBase(unittest.TestCase):
    """Redirect the store into a temp dir for every test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "item_images"
        self._real = ii.store_dir
        ii.store_dir = lambda: root
        self.addCleanup(self._restore)

    def _restore(self):
        ii.store_dir = self._real
        self._tmp.cleanup()


class RoundTrip(StoreBase):
    def test_a_picture_survives_the_round_trip_byte_for_byte(self):
        ref = ii.store(ONE_PX_PNG)
        self.assertTrue(ref)
        self.assertEqual(ii.load(ref), ONE_PX_PNG)

    def test_the_ref_is_the_content_hash(self):
        import hashlib
        raw = base64.b64decode(ONE_PX_PNG.split(",", 1)[1])
        self.assertEqual(ii.store(ONE_PX_PNG), hashlib.sha256(raw).hexdigest() + ".png")

    def test_identical_pictures_are_stored_once(self):
        """The whole reason for hashing: fifty items, one photo, one file."""
        a, b = ii.store(ONE_PX_PNG), ii.store(ONE_PX_PNG)
        self.assertEqual(a, b)
        self.assertEqual(ii.store_size()["files"], 1)

    def test_different_pictures_get_different_refs(self):
        self.assertNotEqual(ii.store(ONE_PX_PNG), ii.store(OTHER_PNG))
        self.assertEqual(ii.store_size()["files"], 2)



class Rejections(StoreBase):
    def test_non_images_are_refused(self):
        for bad in ("", None, "not a data uri", "data:text/plain;base64,QQ==",
                    "data:image/tiff;base64,QQ==", "data:image/svg+xml;base64,QQ=="):
            self.assertIsNone(ii.store(bad), f"{str(bad)[:40]!r} should be refused")

    def test_an_oversized_picture_is_refused(self):
        big = base64.b64encode(b"\0" * (ii.MAX_BYTES + 1)).decode()
        self.assertIsNone(ii.store("data:image/png;base64," + big),
                          "one bad paste must not be able to fill the disk")

    def test_undecodable_base64_is_refused(self):
        self.assertIsNone(ii.store("data:image/png;base64,!!!!"))

    def test_an_empty_payload_is_refused(self):
        self.assertIsNone(ii.store("data:image/png;base64,"))


class PathSafety(StoreBase):
    """A ref becomes a file path. This is the part that must not be loosened."""

    TRAVERSALS = [
        "../../settings.json",
        "..\\..\\po_tool.db",
        "/etc/passwd",
        "C:\\Windows\\win.ini",
        "%2e%2e/x.png",
        "....//x.png",
        "a" * 64 + "/../../x.png",
    ]

    def test_traversal_attempts_resolve_to_nothing(self):
        for bad in self.TRAVERSALS:
            self.assertIsNone(ii.path_for(bad), f"{bad!r} must not resolve")
            self.assertEqual(ii.load(bad), "", f"{bad!r} must not load")

    def test_wrong_shaped_refs_are_refused(self):
        for bad in ("short.png", "a" * 63 + ".png", "a" * 65 + ".png",
                    "g" * 64 + ".png", "a" * 64, "a" * 64 + ".exe",
                    "a" * 64 + ".php", "", None):
            self.assertIsNone(ii.path_for(bad), f"{bad!r} must not resolve")

    def test_a_legitimate_ref_resolves_inside_the_store(self):
        ref = ii.store(ONE_PX_PNG)
        path = ii.path_for(ref)
        self.assertIsNotNone(path)
        self.assertEqual(path.parent.resolve(), ii.store_dir().resolve())

    def test_containment_holds_even_if_the_name_check_is_bypassed(self):
        """The second layer, tested on its own.

        `_safe_name` rejects everything traversal-shaped, so `path_for`'s
        containment check is unreachable in normal operation - which means
        deleting it passes every other test in this file. That is exactly the
        kind of defence that rots. Disable the first layer and confirm the
        second still refuses to escape.
        """
        real = ii._safe_name
        ii._safe_name = lambda ref: str(ref)      # permissive on purpose
        try:
            for bad in ("../secret.png", "../../po_tool.db", r"..\..\x.png"):
                self.assertIsNone(
                    ii.path_for(bad),
                    f"{bad!r} escaped the store once the name check was bypassed - "
                    "the containment check in path_for is doing nothing")
        finally:
            ii._safe_name = real

    def test_a_file_outside_the_store_is_unreachable_even_by_a_valid_name(self):
        """The containment check, isolated from the name check.

        A ref that passes `_safe_name` but resolves outside the store - via a
        symlink, a junction, or a future change to how the name is joined -
        must still be refused. Tested by planting a real file next to the store
        and confirming no ref reaches it.
        """
        outside = ii.store_dir().parent / "secret.png"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"not yours")
        for attempt in ("../secret.png", "..%2Fsecret.png", "a" * 64 + ".png"):
            path = ii.path_for(attempt)
            if path is not None:
                self.assertNotEqual(path.resolve(), outside.resolve())
            self.assertNotIn("not yours", ii.load(attempt))


class MissingFiles(StoreBase):
    def test_a_deleted_picture_degrades_to_no_picture(self):
        """Never an exception: a missing photo must not break autocomplete."""
        ref = ii.store(ONE_PX_PNG)
        ii.path_for(ref).unlink()
        self.assertEqual(ii.load(ref), "")

    def test_loading_from_an_empty_store_is_safe(self):
        self.assertEqual(ii.load("0" * 64 + ".png"), "")
        self.assertEqual(ii.store_size(), {"files": 0, "bytes": 0})

    def test_an_empty_file_is_not_a_picture(self):
        ref = ii.store(ONE_PX_PNG)
        ii.path_for(ref).write_bytes(b"")
        self.assertEqual(ii.load(ref), "",
                         "a zero-byte file must read as absent, not as an empty "
                         "data URI the browser would render broken")


class BridgeContract(unittest.TestCase):
    def test_get_item_image_never_raises(self):
        from backend.api import Api
        a = Api()
        for bad in ("../../x", "", None, "a" * 64 + ".exe"):
            self.assertEqual(a.get_item_image(bad), "")

    def test_the_bridge_swallows_a_failing_store(self):
        """The try/except only matters when load() actually blows up.

        Without forcing that, removing the guard passes every other test here -
        `load` is careful enough never to raise on its own. But it touches the
        filesystem, and a locked or unreadable directory WILL raise. An
        exception crossing the bridge surfaces in the editor as a broken pick.
        """
        from backend import api as api_mod
        from backend.api import Api
        real = api_mod.item_images.load
        api_mod.item_images.load = lambda ref: (_ for _ in ()).throw(OSError("disk gone"))
        try:
            self.assertEqual(Api().get_item_image("a" * 64 + ".png"), "",
                             "a failing store must degrade to 'no picture', not "
                             "throw across the bridge")
        finally:
            api_mod.item_images.load = real

    def test_the_editor_guards_the_new_bridge_method(self):
        """F5 without a restart = new JS, old Python.

        An unguarded call throws mid-pick and abandons the rest of the autofill.
        """
        js = (ROOT / "static/quote.js").read_text(encoding="utf8")
        self.assertIn('typeof getImage !== "function"', js)

    def test_image_refs_is_not_written_onto_the_row(self):
        js = (ROOT / "static/quote.js").read_text(encoding="utf8")
        self.assertIn('if (k === "image_refs") continue;', js,
                      "image_refs is a memory key, not a column - writing it onto "
                      "the row would persist it into rows_json as a phantom field")


if __name__ == "__main__":
    unittest.main(verbosity=2)
