"""The launcher - the app's window on each office PC (Phase 2, 2026-10-22).

What must hold:
  * pywebview (its OWN discovery code, run here) offers the page exactly the
    launcher's calls before login, and every server method after it - nothing
    else (a public object would hand the page its methods);
  * the nine calls the server refuses are done here, with the same parameters
    the desktop app's have, and are never sent to the server;
  * a file the server names lands in the person's own folder or the temp
    cache - never anywhere else, whatever the name says;
  * open / print / show / copy act on the person's own copy, fetched once and
    again only when the server's is newer; previews go to the temp cache;
  * every document the person saves is copied down to their folder;
  * a spreadsheet picked here is uploaded and read by the server; a signed
    copy picked here is attached on the server;
  * "keep me logged in" keeps the session scrambled for this Windows user and
    gets straight back in; a session ended on the server sends the window
    back to the login page.
Runs a real gateway on scratch data.
"""
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server_scratch as S  # noqa: E402

sys.path.insert(0, str(S.ROOT))
from launcher import client as L  # noqa: E402
from server import gateway, users  # noqa: E402

PASSWORD = "test-password-waleed"
INVOICE = {"inv_no": "INV-LAUNCH-1", "date": "22/10/2026", "inv_type": "materials",
           "client_name": "Server Test Co", "items": [{"item": "Cement", "qty": 2, "rate": 5}]}


def bridge_functions(api) -> dict:
    """What pywebview itself puts on window.pywebview.api for this object - its
    own code (webview/util.py inject_pywebview), run against a stand-in window."""
    from webview import util
    scripts, done = [], threading.Event()
    noop = types.SimpleNamespace(set=lambda: None)

    def run_js(code):
        scripts.append(code)
        if len(scripts) == 2:
            done.set()

    window = types.SimpleNamespace(
        _js_api=api, _functions={}, _expose_lock=threading.Lock(), uid="test", js_api_endpoint=None,
        text_select=False, zoomable=False, draggable=False, easy_drag=False, frameless=False, state={},
        run_js=run_js, events=types.SimpleNamespace(before_load=noop, loaded=noop, _pywebviewready=noop))
    util.inject_pywebview("edgechromium", window)
    assert done.wait(15), "pywebview never built its function list"
    found = re.search(r"_createApi\(JSON\.parse\('(.*?)'\)\);", scripts[1], re.S)
    return {f["func"]: f["params"] for f in json.loads(found.group(1))}


def make_api(work: Path) -> L.LauncherApi:
    return L.LauncherApi(settings_path=work / "appdata" / "launcher.json",
                         docs_root=work / "Documents" / "Business Hub", cache_root=work / "Temp" / "Business Hub")


class Pieces(unittest.TestCase):
    def setUp(self):
        self.work = S.ROOT / ".test-work" / "launcher-pieces"
        S.remove_tree(self.work)
        self.docs, self.cache = self.work / "docs", self.work / "cache"

    def test_a_server_file_lands_only_in_its_two_folders(self):
        self.assertEqual(L.local_target("ABRAJ/INVOICES/Co/INV-1.pdf", self.docs, self.cache),
                         self.docs / "ABRAJ" / "INVOICES" / "Co" / "INV-1.pdf")
        self.assertEqual(L.local_target("_preview/preview_INV-1.pdf", self.docs, self.cache),
                         self.cache / "preview" / "preview_INV-1.pdf")
        for bad in ("../evil.pdf", "ABRAJ/../../evil.pdf", "C:/Windows/evil.pdf", "ABRAJ/C:evil.pdf",
                    "", "ABRAJ//x.pdf", "_preview", "ABRAJ/x.pdf.", "ABRAJ/x.pdf ", "\\\\host\\share\\x.pdf",
                    "/ABRAJ/x.pdf", "ABRAJ/a?b.pdf"):
            with self.assertRaises(L.ServerError, msg=repr(bad)):
                L.local_target(bad, self.docs, self.cache)

    def test_the_server_address_can_be_typed_loosely(self):
        self.assertEqual(L.normalize_server("businesshub"), "http://businesshub:8765")
        self.assertEqual(L.normalize_server(" 192.168.1.20:9000/ "), "http://192.168.1.20:9000")
        self.assertEqual(L.normalize_server("http://BusinessHub:8765/templates/index.html"),
                         "http://businesshub:8765")
        for bad in ("", "   ", "ftp://x", "http://:80", "host:notaport"):
            with self.assertRaises(ValueError, msg=bad):
                L.normalize_server(bad)

    def test_a_saved_session_is_scrambled_for_this_windows_user(self):
        blob = L.protect("secret-token-123")
        self.assertNotIn("secret-token-123", blob)
        self.assertNotIn("secret-token-123", __import__("base64").b64decode(blob).decode("latin-1"))
        self.assertEqual(L.unprotect(blob), "secret-token-123")

    def test_pywebview_offers_only_the_bridge_calls(self):
        api = make_api(self.work)
        before = bridge_functions(api)
        self.assertEqual(set(before), set(L.LOCAL_CALLS) | set(L.LAUNCHER_CALLS),
                         "before login the page sees more (or less) than the launcher's own calls")
        api._forwarded = frozenset({"save_inv", "get_today", "open_path"})
        after = bridge_functions(api)
        self.assertEqual(set(after) - set(before), {"save_inv", "get_today"})
        self.assertEqual(after["save_inv"], [])          # all arguments pass straight through
        self.assertEqual(after["open_path"], ["path"])   # the PC's own, not the server's

    def test_the_pc_side_calls_are_exactly_what_the_server_refuses(self):
        self.assertEqual(set(L.LOCAL_CALLS), set(gateway.LOCAL_ONLY))
        import ast
        tree = ast.parse((S.ROOT / "backend" / "api.py").read_text(encoding="utf-8"))
        desktop = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
        params = {f.name: [a.arg for a in f.args.args] for f in desktop.body if isinstance(f, ast.FunctionDef)}
        import inspect
        for name in L.LOCAL_CALLS:
            mine = list(inspect.signature(getattr(L.LauncherApi, name)).parameters)
            self.assertEqual(mine, params[name], f"{name} takes different arguments from the desktop app's")


class ThroughTheGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = S.scratch("launcher")
        users.UserStore(cls.data / "users" / "users.json").add("waleed", PASSWORD, display="Waleed")
        cls.port = S.free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.gw = subprocess.Popen([sys.executable, "-m", "server.gateway", "--host", "127.0.0.1",
                                   "--port", str(cls.port), "--no-prestart"],
                                  cwd=str(S.ROOT), env=S.env_for(cls.data),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            S.wait_http(cls.base + "/health")
        except BaseException:
            cls.gw.kill()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.gw.kill()
        cls.gw.wait(timeout=30)

    def setUp(self):
        self.work = S.ROOT / ".test-work" / "launcher-pc" / self._testMethodName
        S.remove_tree(self.work)
        self.api = make_api(self.work)
        self.sent = []
        real = L.Server.request

        def spy(server, method, path, *a, **k):
            self.sent.append(path)
            return real(server, method, path, *a, **k)

        L.Server.request = spy
        self.addCleanup(setattr, L.Server, "request", real)

    def login(self, remember=False):
        r = self.api.launcher_login(self.base, "waleed", PASSWORD, remember)
        self.assertTrue(r["ok"], r)
        return r

    def preview(self):
        r = self.api.preview_inv(INVOICE)
        self.assertTrue(r["ok"], r)
        return r["path"]

    # ---------------------------------------------------------------- in and out
    def test_login_then_every_screen_call_is_offered(self):
        r = self.login()
        self.assertEqual(r["next"], self.base + "/templates/index.html")
        offered = bridge_functions(self.api)
        js = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in list((S.ROOT / "static").glob("*.js")) + list((S.ROOT / "templates").glob("*.html")))
        called = {n for n in gateway.api_method_names() if re.search(r"\b" + n + r"\s*\(", js)}
        self.assertGreater(len(called), 100)
        self.assertEqual(sorted(called - set(offered)), [], "the screens call these and the bridge lacks them")
        self.assertIn("year", self.api.get_today())

    def test_wrong_password_and_no_server_are_said_plainly(self):
        r = self.api.launcher_login(self.base, "waleed", "not-it")
        self.assertFalse(r["ok"])
        self.assertIn("Wrong name or password", r["error"])
        r = self.api.launcher_login(f"127.0.0.1:{S.free_port()}", "waleed", PASSWORD)
        self.assertFalse(r["ok"])
        self.assertIn("Can't reach the office server", r["error"])
        self.assertEqual(set(bridge_functions(self.api)), set(L.LOCAL_CALLS) | set(L.LAUNCHER_CALLS))

    def test_logged_out_on_the_server_goes_back_to_login(self):
        self.login()
        self.api._server.json_call("POST", "/auth/logout", {})
        with self.assertRaises(L.ServerError):
            self.api.get_today()
        self.assertEqual(self.api._forwarded, frozenset())
        self.assertIn("log in again", self.api.launcher_state()["notice"])

    def test_only_the_office_pages_may_use_the_bridge(self):
        self.login()
        page = {"url": "https://example.com/not-ours.html"}
        self.api._window = types.SimpleNamespace(get_current_url=lambda: page["url"])
        sent_before = len(self.sent)
        with self.assertRaises(L.ServerError):
            self.api.get_today()
        self.assertFalse(self.api.open_path(r"D:\docs\INV-1.pdf")["ok"])
        self.assertEqual(self.sent[sent_before:], [], "a foreign page reached the server")
        for ok_url in (self.base + "/templates/index.html", None):   # the app, and the login page
            page["url"] = ok_url
            self.assertIn("year", self.api.get_today())

    def test_keep_me_logged_in(self):
        self.login(remember=True)
        saved = (self.work / "appdata" / "launcher.json").read_text(encoding="utf-8")
        self.assertNotIn(self.api._server.token, saved)
        again = make_api(self.work)
        self.assertEqual(again._resume(), self.base + "/templates/index.html")
        self.assertIn("get_today", dir(again))
        r = make_api(self.work).launcher_logout()
        self.assertTrue(r["ok"])
        self.assertIsNone(make_api(self.work)._resume(), "logging out did not forget the saved session")

    # ---------------------------------------------------------------- files
    def test_open_print_show_copy_use_my_own_copy(self):
        self.login()
        path = self.preview()
        opened, ran = [], []
        self.api._startfile = lambda p, *verb: opened.append((Path(p), verb))
        self.api._run = lambda cmd, **kw: ran.append(cmd) or types.SimpleNamespace(returncode=0)
        self.assertEqual(self.api.open_path(path), {"ok": True})
        local, verb = opened[0]
        self.assertEqual(local.parent, self.work / "Temp" / "Business Hub" / "preview")
        self.assertTrue(local.read_bytes().startswith(b"%PDF"))
        fetches = sum(1 for p in self.sent if p.startswith("/files?"))
        self.assertEqual(self.api.print_file(path), {"ok": True})
        self.assertEqual(opened[1], (local, ("print",)))
        self.assertEqual(self.api.reveal_in_folder(path), {"ok": True})
        self.assertEqual(ran[0], ["explorer", "/select,", str(local)])
        self.assertEqual(self.api.copy_file_to_clipboard(path), {"ok": True})
        self.assertIn(str(local), ran[1][-1])
        self.assertEqual(sum(1 for p in self.sent if p.startswith("/files?")), fetches,
                         "an unchanged file was downloaded again")
        local_calls = [p for p in self.sent if any(p == f"/api/{n}" for n in L.LOCAL_CALLS)]
        self.assertEqual(local_calls, [], "a PC-side call was sent to the server")
        missing = self.api.open_path(str(Path(path).with_name("nope.pdf")))
        self.assertFalse(missing["ok"])
        self.assertIn("File not found", missing["error"])
        outside = self.api.open_path(r"C:\Windows\win.ini")
        self.assertFalse(outside["ok"])
        self.assertIn("not in the office documents folders", outside["error"])

    def test_a_newer_file_on_the_server_replaces_my_copy(self):
        self.login()
        docs = self.data.parent / "docs-abraj" / "INVOICES"
        docs.mkdir(parents=True, exist_ok=True)
        server_file = docs / "INV-NEWER.pdf"
        server_file.write_bytes(b"%PDF-1.4 first")
        self.api._startfile = lambda p, *verb: None
        self.api.open_path(str(server_file))
        mine = self.work / "Documents" / "Business Hub" / "docs-abraj" / "INVOICES" / "INV-NEWER.pdf"
        self.assertEqual(mine.read_bytes(), b"%PDF-1.4 first")
        time.sleep(1.1)
        server_file.write_bytes(b"%PDF-1.4 second, longer")
        self.api.open_path(str(server_file))
        self.assertEqual(mine.read_bytes(), b"%PDF-1.4 second, longer")

    def test_saving_keeps_my_own_copy(self):
        self.login()
        r = self.api.save_inv({**INVOICE, "inv_no": "", "is_edit": False})
        self.assertTrue(r["ok"], r)
        server_pdf = Path(r["path"])
        rel = server_pdf.relative_to(self.data.parent)
        mine = self.work / "Documents" / "Business Hub" / rel
        end = time.time() + 30
        while time.time() < end and not mine.exists():
            time.sleep(0.2)
        self.assertTrue(mine.exists(), f"no copy at {mine}")
        self.assertEqual(mine.read_bytes(), server_pdf.read_bytes())

    def test_a_picked_spreadsheet_is_read_by_the_server(self):
        self.login()
        sheet = self.work / "picked" / "items.csv"
        sheet.parent.mkdir(parents=True)
        sheet.write_text("Item,Qty\nCement,5\n", encoding="utf-8")
        self.api._pick = lambda file_types: str(sheet)
        picked = self.api.bulk_pick_spreadsheet()
        self.assertTrue(picked["ok"], picked)
        self.assertTrue(Path(picked["path"]).is_relative_to(self.data / "users" / "waleed" / "uploads"))
        parsed = self.api.bulk_parse_spreadsheet(picked["path"])
        self.assertTrue(parsed["ok"], parsed)
        self.assertIn("Cement", json.dumps(parsed["rows"]))
        self.api._pick = lambda file_types: None
        self.assertEqual(self.api.bulk_pick_spreadsheet(), {"ok": False, "cancelled": True})

    def test_a_signed_copy_picked_here_is_attached_there(self):
        self.login()
        saved = self.api.save_inv({**INVOICE, "inv_no": "", "is_edit": False})
        self.assertTrue(saved["ok"], saved)
        scan = self.work / "picked" / "signed scan.pdf"
        scan.parent.mkdir(parents=True)
        scan.write_bytes(Path(saved["path"]).read_bytes())          # a real PDF
        self.api._pick = lambda file_types: str(scan)
        r = self.api.attach_doc_signed_copy("inv", saved["inv_no"])
        self.assertTrue(r["ok"], r)
        self.assertIn("/api/attach_doc_signed_copy_data", self.sent)

    def test_pc_trouble_reaches_the_office_log_even_after_a_network_cut(self):
        self.login()
        self.api._startfile = lambda p, *verb: None
        self.assertTrue(self.api.open_path(self.preview())["ok"])
        self.assertFalse(self.api.open_path(str(self.data.parent / "docs-abraj" / "INVOICES" / "gone.pdf"))["ok"])
        live = self.api._server.base
        self.api._server.base = f"http://127.0.0.1:{S.free_port()}"         # the network is cut
        with self.assertRaises(L.NetworkError):
            self.api.get_today()
        self.assertFalse(self.api._flush_notes(), "notes claimed sent while the server was unreachable")
        self.api._server.base = live                                          # and back
        self.assertTrue(self.api._flush_notes())
        db = self.data / "logs" / "activity.db"
        end, rows = time.time() + 15, []
        while time.time() < end and len(rows) < 3:
            time.sleep(0.3)
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT action, outcome, person, pc, source FROM events WHERE source = 'pc'"
                                " AND person = 'waleed' ORDER BY ts").fetchall()
            conn.close()
        self.assertEqual([(a, o) for a, o, *_ in rows],
                         [("open_path", "ok"), ("open_path", "server_error"), ("get_today", "network")])
        self.assertTrue(all(L.PC_NAME in pc for *_, pc, _ in rows), rows)

    def test_backup_pickers_say_where_it_is_done(self):
        self.login()
        for r in (self.api.dm_pick_folder("x"), self.api.dm_pick_file("x", ["Backup archive (*.zip)"])):
            self.assertFalse(r["ok"])
            self.assertIn("done at the server", r["error"])
        self.assertEqual(self.api.gate_ready({}), {"ok": True, "gate": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)

