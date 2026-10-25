"""The gateway - the office server's one door (Phase 2, 2026-10-22).

What must hold:
  * nothing but the screens is reachable without logging in, and the signature
    and stamp images kept in static/ are never served at all;
  * a wrong password gets nothing, and repeated wrong passwords lock the door
    for a while;
  * two people are routed to two engines (one's company switch never moves
    the other's), and each person's previews are theirs alone;
  * /files hands out ONLY documents and previews - never the database, the
    accounts file, or anything outside, however the path is dressed up;
  * /upload keeps the file inside that person's upload folder;
  * the calls that act on a PC (open / print a PDF, file pickers) are refused
    by the server - the launcher does them;
  * a crashed engine is started again on the next click, but a call that
    reached an engine and lost it is never sent twice (it may have saved);
  * sessions survive a gateway restart, and logging out ends them;
  * when the gateway stops, its engines stop with it; a second gateway cannot
    share its port.
Scratch data only.
"""
import json
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server_scratch as S  # noqa: E402

sys.path.insert(0, str(S.ROOT))
from server import users  # noqa: E402

PASSWORDS = {"waleed": "test-password-waleed", "ahmed": "test-password-ahmed"}
INVOICE = {"inv_no": "INV-PREVIEW-1", "date": "22/10/2026", "inv_type": "materials",
           "client_name": "Server Test Co", "items": [{"item": "Cement", "qty": 1, "rate": 5}]}


def start_gateway(data, port, *extra):
    return subprocess.Popen([sys.executable, "-m", "server.gateway", "--host", "127.0.0.1",
                             "--port", str(port), *extra],
                            cwd=str(S.ROOT), env=S.env_for(data),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def stop(proc) -> None:
    proc.kill()
    proc.communicate(timeout=30)        # waits, and closes its pipes


def pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


class Gateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = S.scratch("server-gateway")
        store = users.UserStore(cls.data / "users" / "users.json")
        store.add("waleed", PASSWORDS["waleed"], role="admin", display="Waleed")
        store.add("ahmed", PASSWORDS["ahmed"], role="procurement", display="Ahmed")
        cls.port = S.free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.gw = start_gateway(cls.data, cls.port)
        try:
            S.wait_http(cls.base + "/health")
        except BaseException:
            cls.gw.kill()
            raise AssertionError("gateway never came up:\n" + cls.gw.communicate(timeout=5)[1][-3000:])
        cls.tokens = {u: cls.login(u)[1]["token"] for u in PASSWORDS}

    @classmethod
    def tearDownClass(cls):
        stop(cls.gw)

    @classmethod
    def login(cls, user, password=None):
        return S.post(cls.base + "/auth/login", {"user": user, "password": password or PASSWORDS[user]})

    def api(self, user, method, *args, token=None):
        tok = token if token is not None else self.tokens[user]
        return S.post(f"{self.base}/api/{method}", {"args": list(args)}, {"Authorization": f"Bearer {tok}"})

    def get(self, path, token=None, headers=None):
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h), timeout=60) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def file_url(self, path, info=False):
        return ("/files/info" if info else "/files") + "?path=" + urllib.parse.quote(str(path))

    def engines(self):
        return json.loads((self.data / "server" / "engines.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- doors
    def test_screens_are_public_but_data_is_not(self):
        status, body, _ = self.get("/templates/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"Business Hub", body)
        status, body, headers = self.get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertEqual(self.get("/static/app.js", headers={"If-None-Match": headers["ETag"]})[0], 304)
        self.assertEqual(self.api("waleed", "get_active_profile", token="")[0], 401)
        self.assertEqual(self.api("waleed", "get_active_profile", token="made-up")[0], 401)

    def test_signatures_and_stamps_are_never_served(self):
        for path in ("/static/sigs/sig_1.png", "/static/sigs/stamp.png", "/static/SIGS./sig_1.png",
                     "/static/sigs%2Fsig_1.png", "/static/brands/eshraqat/logo.png"):
            self.assertEqual(self.get(path)[0], 404, path)
            self.assertEqual(self.get(path, self.tokens["waleed"])[0], 404, path)

    def test_no_path_escapes_the_screens_folder(self):
        for bad in ("/static/../data/users/users.json", "/templates/..%2Fdata%2Fusers%2Fusers.json",
                    "/static/../server/users.py", "/templates/..%2Fserver%2Fusers.py",
                    "/static/..\\server\\users.py", "/templates/C:%5CWindows%5Cwin.ini"):
            status, body, _ = self.get(bad)
            self.assertIn(status, (400, 403, 404), bad)
            self.assertNotIn(b"hash", body)

    def test_wrong_password_gets_nothing(self):
        status, body = self.login("waleed", "not-it")
        self.assertEqual(status, 401)
        self.assertNotIn("token", body)

    def test_two_people_two_engines(self):
        self.assertEqual(self.api("ahmed", "switch_profile", "eshraqat")[0], 200)
        try:
            self.assertEqual(self.api("ahmed", "get_active_profile")[1]["result"]["id"], "eshraqat")
            self.assertEqual(self.api("waleed", "get_active_profile")[1]["result"]["id"], "abraj",
                             "Ahmed's switch moved Waleed's company")
        finally:
            self.api("ahmed", "switch_profile", "abraj")

    def test_the_method_list_is_the_engines_list(self):
        status, body, _ = self.get("/api-methods")
        listed = json.loads(body)
        forwarded, local = set(listed["methods"]), set(listed["local"])
        self.assertIn("save_inv", forwarded)
        self.assertIn("open_path", local)
        self.assertFalse(forwarded & local)
        self.api("waleed", "get_today")
        port = self.engines()["waleed"]["port"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/methods") as r:
            engine = set(json.loads(r.read())["methods"])
        self.assertEqual(forwarded | local, engine, "the gateway's list drifted from what the engine runs")

    def test_calls_that_act_on_a_pc_are_refused(self):
        for name in ("open_path", "print_file", "dm_pick_file", "reveal_in_folder"):
            status, body = self.api("waleed", name, r"C:\Windows\win.ini")
            self.assertEqual(status, 403, f"{name}: {body}")

    def test_backup_and_restore_are_done_at_the_server(self):
        before = sorted(p.name for p in self.data.rglob("*.zip"))
        for name, arg in (("dm_create_backup", str(self.data)), ("dm_restore_backup", str(self.data / "x.zip"))):
            status, body = self.api("waleed", name, arg)
            self.assertEqual(status, 403, f"{name}: {body}")
        self.assertEqual(sorted(p.name for p in self.data.rglob("*.zip")), before)

    # ---------------------------------------------------------------- files
    def test_files_hands_out_documents_and_previews_only(self):
        docs = self.data.parent / "docs-abraj" / "INVOICES"
        docs.mkdir(parents=True, exist_ok=True)
        pdf = docs / "INV-TEST-001.pdf"
        pdf.write_bytes(b"%PDF-1.4 test document")
        self.assertEqual(self.get(self.file_url(pdf))[0], 401, "a document without logging in")
        status, body, headers = self.get(self.file_url(pdf), self.tokens["waleed"])
        self.assertEqual(status, 200)
        self.assertEqual(body, b"%PDF-1.4 test document")
        self.assertEqual(headers.get("X-Relative-Path"), "docs-abraj/INVOICES/INV-TEST-001.pdf")

        status, body, _ = self.get(self.file_url(pdf, info=True), self.tokens["waleed"])
        info = json.loads(body)
        self.assertEqual((status, info["relative"], info["size"], info["folder"]),
                         (200, "docs-abraj/INVOICES/INV-TEST-001.pdf", 22, False))

        arabic = docs / "شركة الاختبار" / "INV 002.pdf"
        arabic.parent.mkdir(exist_ok=True)
        arabic.write_bytes(b"%PDF-1.4 arabic")
        status, body, headers = self.get(self.file_url(arabic), self.tokens["waleed"])
        self.assertEqual((status, body), (200, b"%PDF-1.4 arabic"))
        self.assertEqual(urllib.parse.unquote(headers["X-Relative-Path"]),
                         "docs-abraj/INVOICES/شركة الاختبار/INV 002.pdf")

        secrets = [self.data / "users" / "users.json", self.data / "profiles" / "abraj" / "po_tool.db",
                   self.data / "server" / "sessions.json", Path(r"C:\Windows\win.ini"),
                   self.data.parent / "docs-abraj" / ".." / "data" / "users" / "users.json", "INVOICES"]
        for p in secrets:
            for info in (False, True):
                status, body, _ = self.get(self.file_url(p, info), self.tokens["waleed"])
                self.assertEqual(status, 403, f"{p} was handed out")
                self.assertNotIn(b"hash", body)

    def test_each_persons_previews_are_their_own(self):
        paths = {}
        for user in ("waleed", "ahmed"):
            status, body = self.api(user, "preview_inv", INVOICE)
            self.assertEqual(status, 200, body)
            self.assertTrue(body["result"]["ok"], body)
            paths[user] = body["result"]["path"]
        self.assertNotEqual(paths["waleed"], paths["ahmed"], "two people previewing one number share a file")
        st, pdf, headers = self.get(self.file_url(paths["waleed"]), self.tokens["waleed"])
        self.assertEqual(st, 200)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(headers.get("X-Relative-Path", "").startswith("_preview/"))
        self.assertEqual(self.get(self.file_url(paths["ahmed"]), self.tokens["waleed"])[0], 403,
                         "Waleed was handed Ahmed's preview")

    def test_upload_stays_in_the_persons_folder(self):
        data = b"col1,col2\n1,2\n"
        uploads = (self.data / "users" / "waleed" / "uploads").resolve()
        for name in ("..\\..\\..\\..\\evil.csv", "../../../../evil2.csv", "C:\\evil3.csv"):
            req = urllib.request.Request(self.base + "/upload?name=" + urllib.parse.quote(name),
                                         data=data, method="POST",
                                         headers={"Authorization": f"Bearer {self.tokens['waleed']}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = json.loads(r.read())
            except urllib.error.HTTPError as e:
                self.fail(f"{name}: {e.code} {e.read()[:300]}")
            saved = Path(body["path"])
            self.assertEqual(saved.read_bytes(), data)
            self.assertEqual(saved.resolve().parent, uploads, name)
            self.assertTrue(saved.name.endswith(name.replace("\\", "/").split("/")[-1]), name)
        strays = [p for p in self.data.parent.rglob("evil*.csv") if p.resolve().parent != uploads]
        self.assertEqual(strays, [], "an upload landed outside the person's folder")
        req = urllib.request.Request(self.base + "/upload?name=x.csv", data=data, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(cm.exception.code, 401)

    # ---------------------------------------------------------------- life
    def test_a_crashed_engine_comes_back(self):
        self.assertEqual(self.api("waleed", "get_today")[0], 200)
        pid = self.engines()["waleed"]["pid"]
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        time.sleep(1)
        status, body = self.api("waleed", "get_today")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertNotEqual(self.engines()["waleed"]["pid"], pid)

    def test_a_disabled_person_is_shut_out(self):
        store = users.UserStore(self.data / "users" / "users.json")
        store.set_enabled("ahmed", False)
        try:
            self.assertEqual(self.api("ahmed", "get_today")[0], 401, "a switched-off person still got in")
            self.assertEqual(self.login("ahmed")[0], 401)
        finally:
            store.set_enabled("ahmed", True)
        self.assertEqual(self.api("ahmed", "get_today")[0], 200)

    def test_a_second_gateway_cannot_share_the_port(self):
        other = start_gateway(self.data, self.port, "--no-prestart")
        try:
            _, err = other.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            stop(other)
            self.fail("a second gateway opened the same port")
        self.assertNotEqual(other.returncode, 0)
        self.assertIn("already running", err)
        self.assertEqual(self.get("/health")[0], 200)

    def test_the_activity_log_says_who_did_what_from_which_pc(self):
        headers = {"Authorization": f"Bearer {self.tokens['waleed']}", "X-PC-Name": "TEST-PC"}
        S.post(self.base + "/api/get_today", {"args": []}, headers)
        st, body = S.post(self.base + "/api/save_inv", {"args": [{
            "is_edit": False, "date": "22/10/2026", "inv_type": "materials",
            "client_name": "Nobody Such Co", "items": [{"item": "Cement", "qty": 1, "rate": 5}]}]}, headers)
        self.assertEqual(st, 200, body)
        self.assertFalse(body["result"]["ok"], "an unknown client should be refused")
        db = self.data / "logs" / "activity.db"
        rows, end = [], time.time() + 15
        while time.time() < end and not any(r["action"] == "save_inv" for r in rows):
            time.sleep(0.3)
            if db.exists():
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                rows = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
                conn.close()
        saved = [r for r in rows if r["action"] == "save_inv"]
        self.assertTrue(saved, "the refused save never reached the log")
        r = saved[-1]
        self.assertEqual((r["person"], r["pc"], r["company"], r["source"], r["outcome"]),
                         ("waleed", "TEST-PC (127.0.0.1)", "abraj", "call", "refused"))
        logins = [x for x in rows if x["action"] == "login" and x["person"] == "waleed"]
        self.assertIn("ok", {x["outcome"] for x in logins})
        st, listed = self.api("waleed", "activity_list", {"person": "waleed"})
        self.assertTrue(listed["result"]["ok"], listed)
        self.assertIn("save_inv", {x["action"] for x in listed["result"]["rows"]})
        st, refused = self.api("ahmed", "activity_list", {})
        self.assertFalse(refused["result"]["ok"], "a non-admin read the activity log")

    def test_z_sessions_survive_a_restart_and_logout_ends_them(self):
        cls = type(self)
        self.api("waleed", "get_today")
        engine_pids = [e["pid"] for e in self.engines().values()]
        self.assertTrue(engine_pids)
        stop(cls.gw)
        time.sleep(2)
        self.assertFalse([p for p in engine_pids if pid_alive(p)], "engines outlived the gateway")
        cls.gw = start_gateway(self.data, self.port)
        S.wait_http(self.base + "/health")
        st, body = S.post(self.base + "/auth/check", {}, {"Authorization": f"Bearer {self.tokens['ahmed']}"})
        self.assertEqual(st, 200, "a restart logged everyone out")
        st, _ = S.post(self.base + "/auth/logout", {}, {"Authorization": f"Bearer {self.tokens['ahmed']}"})
        self.assertEqual(st, 200)
        self.assertEqual(self.api("ahmed", "get_today")[0], 401, "logout did not end the session")

    def test_zz_repeated_wrong_passwords_do_not_force_a_wait(self):
        for _ in range(3):
            self.login("waleed", "wrong-again")
        status, body = self.login("waleed")
        self.assertEqual(status, 200, body)


class PcSideCallsStayListed(unittest.TestCase):
    """A server method that opens a window, a file dialog, Explorer or the
    clipboard would do it on the SERVER's screen. Every such method must be on
    the gateway's LOCAL_ONLY list (and so in the launcher): add a new one there
    and to launcher/client.py LOCAL_CALLS with a PC-side version."""

    TOUCHES_A_SCREEN = re.compile(r"os\.startfile|[\"']explorer[\"']|Set-Clipboard|webview\.|"
                                  r"create_file_dialog|reveal_window|evaluate_js")

    def test_every_method_that_acts_on_a_screen_is_local_only(self):
        import ast
        from server import gateway
        src = (S.ROOT / "backend" / "api.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)] + \
                [n for n in api.body if isinstance(n, ast.FunctionDef)]
        helpers = {f.name for f in funcs
                   if f.name.startswith("_") and self.TOUCHES_A_SCREEN.search(ast.get_source_segment(src, f))}
        calls_helper = re.compile(r"\b(" + "|".join(map(re.escape, helpers)) + r")\(") if helpers else None
        touching = set()
        for f in api.body:
            if isinstance(f, ast.FunctionDef) and not f.name.startswith("_"):
                body = ast.get_source_segment(src, f)
                if self.TOUCHES_A_SCREEN.search(body) or (calls_helper and calls_helper.search(body)):
                    touching.add(f.name)
        self.assertGreaterEqual(len(touching), 9)
        self.assertEqual(sorted(touching), sorted(gateway.LOCAL_ONLY),
                         "a method acts on the machine's screen but the server would run it (or the list is stale)")


class SelfTestOnlyFromTheServer(unittest.TestCase):
    """/selftest/pdf re-makes real documents for a minute: the office network
    must never be able to start it."""

    def test_only_the_server_itself(self):
        from server import gateway
        for host in ("127.0.0.1", "127.0.0.2", "::1"):
            self.assertTrue(gateway.from_this_pc(host), host)
        for host in ("192.168.1.20", "10.0.0.5", "::ffff:192.168.1.20", "0.0.0.0", "", "1270.0.0.1"):
            self.assertFalse(gateway.from_this_pc(host), host)


class SessionsKeepNoKeys(unittest.TestCase):
    def test_tokens_are_not_kept_and_old_ones_expire(self):
        from server import gateway
        work = S.ROOT / ".test-work" / "sessions"
        S.remove_tree(work)
        path = work / "sessions.json"
        token = gateway.Sessions(path).create("waleed")
        self.assertNotIn(token, path.read_text(encoding="utf-8"), "the file holds a usable token")
        self.assertEqual(gateway.Sessions(path).user_for(token), "waleed", "lost on a restart")
        aged = gateway.Sessions(path)
        for rec in aged._data.values():
            rec["seen"] -= gateway.SESSION_DAYS * 86400 + 1
        self.assertIsNone(aged.user_for(token), "a session past its days still works")
        self.assertIsNone(gateway.Sessions(path).user_for(token), "an expired session came back")


class _AliveProc:
    pid = 0

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class NeverSentTwice(unittest.TestCase):
    """In-process, against stand-in engines: what the pool does when an engine
    vanishes before a call, and when it vanishes in the middle of one."""

    def setUp(self):
        from server import gateway
        self.G = gateway
        self.work = S.ROOT / ".test-work" / "never-sent-twice"
        S.remove_tree(self.work)
        self.pool = gateway.EnginePool(self.work, lambda line: None)
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()

    def stand_in(self, answer: bool):
        hits = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.rfile.read(int(self.headers["Content-Length"])))
                if answer:
                    out = b'{"ok": true, "result": "saved"}'
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                else:                               # gone half way: no answer at all
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.close_connection = True

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return self.G.Engine("waleed", _AliveProc(), srv.server_address[1]), hits

    def test_lost_half_way_is_not_sent_again(self):
        eng, hits = self.stand_in(answer=False)
        self.pool._engines["waleed"] = eng
        starts = []
        self.pool._start = lambda user: starts.append(user) or eng
        status, body = self.pool.call("waleed", "save_inv", [{"client_name": "X"}])
        self.assertEqual(status, 502)
        self.assertTrue(json.loads(body)["engine_lost"])
        self.assertEqual(len(hits), 1, "a call that may have saved was sent again")
        self.assertEqual(starts, [])

    def test_not_listening_is_started_again_and_sent_once(self):
        dead = self.G.Engine("waleed", _AliveProc(), S.free_port())     # nothing listens there
        self.pool._engines["waleed"] = dead
        fresh, hits = self.stand_in(answer=True)
        starts = []
        self.pool._start = lambda user: starts.append(user) or fresh
        status, body = self.pool.call("waleed", "save_inv", [{}])
        self.assertEqual((status, json.loads(body)["result"]), (200, "saved"))
        self.assertEqual((starts, len(hits)), (["waleed"], 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
