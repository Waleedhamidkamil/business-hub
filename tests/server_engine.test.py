"""One engine per person (office server Phase 2, 2026-10-22).

Each person's engine is its own process running today's Api. The point:
switching company in one person's engine must NOT switch anyone else's - each
engine keeps its own company registry under data/users/<name>/. Only the
gateway may call an engine (shared secret), and private methods are refused.
Scratch data only.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server_scratch as S  # noqa: E402

SECRET = "test-engine-secret"


def start(data, user, port):
    return subprocess.Popen([sys.executable, "-m", "server.engine", "--user", user, "--port", str(port)],
                            cwd=str(S.ROOT), env=S.env_for(data, BUSINESSHUB_ENGINE_SECRET=SECRET),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class Engines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = S.scratch("server-engine")
        cls.ports = {"waleed": S.free_port(), "ahmed": S.free_port()}
        cls.procs = {u: start(cls.data, u, p) for u, p in cls.ports.items()}
        try:
            for u, p in cls.ports.items():
                try:
                    S.wait_http(f"http://127.0.0.1:{p}/health")
                except TimeoutError:
                    cls.procs[u].kill()
                    out, err = cls.procs[u].communicate(timeout=5)
                    raise AssertionError(f"engine {u} never came up:\n{err[-3000:]}")
        except BaseException:
            # a failed setUpClass skips tearDownClass - never leave engines running
            for proc in cls.procs.values():
                proc.kill()
            raise

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs.values():
            p.kill()

    def call(self, user, method, *args, secret=SECRET):
        return S.post(f"http://127.0.0.1:{self.ports[user]}/call",
                      {"method": method, "args": list(args)}, {"X-Engine-Secret": secret})

    def active(self, user):
        status, body = self.call(user, "get_active_profile")
        self.assertEqual(status, 200, body)
        return body["result"]["id"]

    def test_switching_company_is_per_person(self):
        self.assertEqual(self.active("waleed"), "abraj")
        self.assertEqual(self.active("ahmed"), "abraj")
        status, body = self.call("ahmed", "switch_profile", "eshraqat")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.active("ahmed"), "eshraqat")
        self.assertEqual(self.active("waleed"), "abraj", "Ahmed's switch moved Waleed's company")
        shared = json.loads((self.data / "profile-registry.json").read_text(encoding="utf-8"))
        self.assertEqual(shared["active_profile"], "abraj", "an engine wrote the shared registry")
        mine = json.loads((self.data / "users" / "ahmed" / "profile-registry.json").read_text(encoding="utf-8"))
        self.assertEqual(mine["active_profile"], "eshraqat")
        self.call("ahmed", "switch_profile", "abraj")

    def test_numbers_are_held_in_the_persons_name(self):
        status, body = self.call("waleed", "save_inv", {
            "is_edit": False, "date": "22/10/2026", "inv_type": "materials",
            "client_name": "Server Test Co", "items": [{"item": "Cement", "qty": 1}],
            "save_without_pdf": True})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["result"]["ok"], body)
        import sqlite3
        conn = sqlite3.connect(self.data / "profiles" / "abraj" / "po_tool.db")
        try:
            holder = conn.execute("SELECT holder FROM number_claims WHERE doc_no=?",
                                  (body["result"]["inv_no"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(holder, "waleed")

    def test_only_the_gateway_may_call(self):
        status, _ = self.call("waleed", "get_active_profile", secret="wrong")
        self.assertEqual(status, 403)

    def test_private_and_unknown_methods_are_refused(self):
        for name in ("_connect", "__init__", "no_such_method"):
            status, body = self.call("waleed", name)
            self.assertEqual(status, 404, f"{name}: {body}")

    def test_the_method_list_is_the_public_api(self):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.ports['waleed']}/methods") as r:
            names = json.loads(r.read())["methods"]
        self.assertIn("save_inv", names)
        self.assertGreater(len(names), 150)
        self.assertFalse([n for n in names if n.startswith("_")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

