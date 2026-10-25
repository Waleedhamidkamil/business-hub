"""Office server accounts (Phase 2, 2026-10-22).

Who may log in. Passwords are never stored - only a PBKDF2-SHA256 hash with a
per-user salt - so the file is useless to whoever copies it. A disabled user is
refused even with the right password (someone who left the company).
Scratch folder only.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import users  # noqa: E402


class Users(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.store = users.UserStore(Path(self.td.name) / "users.json")

    def tearDown(self):
        self.td.cleanup()

    def test_right_password_only(self):
        self.store.add("waleed", "s3cret-pass", role="admin", display="Waleed")
        self.assertEqual(self.store.verify("waleed", "s3cret-pass")["role"], "admin")
        self.assertIsNone(self.store.verify("waleed", "wrong"))
        self.assertIsNone(self.store.verify("nobody", "s3cret-pass"))

    def test_names_ignore_case_and_spaces(self):
        self.store.add("Ahmed", "pass-1234")
        self.assertIsNotNone(self.store.verify("  ahmed ", "pass-1234"))

    def test_the_file_never_holds_the_password(self):
        self.store.add("waleed", "s3cret-pass")
        text = (Path(self.td.name) / "users.json").read_text(encoding="utf-8")
        self.assertNotIn("s3cret-pass", text)
        rec = json.loads(text)["users"]["waleed"]
        self.assertTrue(rec["hash"] and rec["salt"])

    def test_a_disabled_user_is_refused(self):
        self.store.add("ahmed", "pass-1234")
        self.store.set_enabled("ahmed", False)
        self.assertIsNone(self.store.verify("ahmed", "pass-1234"))
        self.store.set_enabled("ahmed", True)
        self.assertIsNotNone(self.store.verify("ahmed", "pass-1234"))

    def test_short_passwords_and_duplicates_are_refused(self):
        with self.assertRaises(ValueError):
            self.store.add("x", "123")
        self.store.add("waleed", "s3cret-pass")
        with self.assertRaises(ValueError):
            self.store.add("WALEED", "another-pass")

    def test_password_change(self):
        self.store.add("waleed", "s3cret-pass")
        self.store.set_password("waleed", "new-pass-99")
        self.assertIsNone(self.store.verify("waleed", "s3cret-pass"))
        self.assertIsNotNone(self.store.verify("waleed", "new-pass-99"))

    def test_usernames_are_safe_folder_names(self):
        for bad in ("../x", "a/b", "a\\b", "", "con"):
            with self.assertRaises(ValueError):
                self.store.add(bad, "pass-1234")


if __name__ == "__main__":
    unittest.main(verbosity=2)
