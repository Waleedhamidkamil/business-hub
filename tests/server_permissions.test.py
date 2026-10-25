import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.permissions import allows, permissions_for
from server.users import UserStore


class OfficePermissions(unittest.TestCase):
    def test_role_permissions_are_centralized(self):
        self.assertTrue(allows("admin", "users.manage"))
        self.assertTrue(allows("admin", "documents.anything"))
        self.assertFalse(allows("procurement", "users.manage"))
        self.assertIn("documents.procurement", permissions_for("procurement"))

    def test_last_admin_count_and_password_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            store = UserStore(Path(folder) / "users.json")
            store.add("waleed", "password", role="admin", display="Waleed")
            store.add("ahmed", "password", role="procurement", display="Ahmed")
            self.assertEqual(store.count_enabled_admins(), 1)
            self.assertEqual(store.verify("waleed", "password")["role"], "admin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
