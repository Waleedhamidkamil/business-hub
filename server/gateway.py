"""The office server's one door (Phase 2, 2026-10-22).

    python -m server.gateway                      # every network card, port 8765
    python -m server.gateway --host 127.0.0.1     # this PC only

What a PC can reach, and only through here:

  /templates/*  /static/*     the screens - code only; the signature and stamp
                              images kept in static/ are never served
  /api-methods                the bridge names a launcher must offer (names only)
  /auth/login  /auth/check  /auth/logout
  /api/<method>               forwarded to that person's OWN engine
  /files?path=                a file from a company documents folder or that
  /files/info?path=           person's own previews - nothing else
  /upload?name=               a file picked on a PC, kept in that person's
                              data/users/<name>/uploads
  /health

Engines (server/engine.py) start on first use, one at a time, and start again
if one died. A call that reached an engine and then lost it is NEVER sent
again - it may already have saved - the person is told to check instead. Each
engine has its own TEMP, so two people previewing the same number never get
each other's file, and every engine ends when the gateway ends, however it
ends (a Windows job object, and each engine also watches the gateway).

Sessions: a random token per login; the server keeps only its SHA-256, in
data/server/sessions.json, so a restart logs nobody out and the file is no use
to whoever reads it. Too many wrong passwords for one name lock that name for
a while (BUSINESSHUB_LOGIN_MAX_FAILS, default 5; BUSINESSHUB_LOGIN_LOCK_SECONDS,
default 300).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import ExclusiveHTTPServer, data_dir   # noqa: E402
from server.users import UserStore                  # noqa: E402
from backend import activity_log                   # noqa: E402  (stdlib only; never opens company data)

DEFAULT_PORT = 8765
TEMPLATES = APP_DIR / "templates"
STATIC = APP_DIR / "static"
# The bridge calls that act on the person's OWN PC (open / show / copy / print
# a PDF, the file pickers, the launch card). The launcher does them locally; the
# server refuses them - run there, they would open windows on the server's screen.
LOCAL_ONLY = ("open_path", "reveal_in_folder", "copy_file_to_clipboard", "print_file",
              "dm_pick_file", "dm_pick_folder", "bulk_pick_spreadsheet",
              "attach_doc_signed_copy", "gate_ready")
# A whole-database backup or restore acts on the server's disk for EVERYONE at
# once; until the admin screen (Phase 5) they are done at the server itself.
AT_THE_SERVER = ("dm_create_backup", "dm_restore_backup")
# static/ also holds the signature and stamp images the PDFs are printed with.
# The screens never load them by URL, so the network never gets them.
PRIVATE_STATIC = ("sigs", "brands")
MAX_BODY = 200 * 1024 * 1024          # an upload, or a call carrying a scanned copy
CALL_TIMEOUT = 600                     # a big report or a long PDF batch
START_TIMEOUT = 180
SESSION_DAYS = float(os.environ.get("BUSINESSHUB_SESSION_DAYS") or 30)
MAX_FAILS = int(os.environ.get("BUSINESSHUB_LOGIN_MAX_FAILS") or 5)
LOCK_SECONDS = float(os.environ.get("BUSINESSHUB_LOGIN_LOCK_SECONDS") or 300)
WATCH_EVERY = 300                      # the watchdog's round, seconds
LOW_DISK = 5 * 1024 ** 3               # warn below 5 GB free

_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
          ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
          ".svg": "image/svg+xml", ".ico": "image/x-icon", ".webp": "image/webp",
          ".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2",
          ".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8", ".txt": "text/plain; charset=utf-8",
          ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          ".zip": "application/zip"}


def _stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _content_type(path: Path) -> str:
    return _TYPES.get(path.suffix.lower(), "application/octet-stream")


def _write_json(path: Path, payload) -> None:
    """Atomic: a crash half way never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:        # a virus scanner holding the old file for a moment
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


class Log:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, line: str) -> None:
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(f"{_stamp()} {line}\n")
            except OSError:
                pass


# ---------------------------------------------------------------- who is in
class Sessions:
    """Login tokens. Only each token's SHA-256 is kept, so the file is no key."""
    TOUCH_EVERY = 300                  # seconds between writing a session's "last seen"

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, user: str, client: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            for k in [k for k, r in self._data.items() if now - r.get("seen", 0) > SESSION_DAYS * 86400]:
                del self._data[k]
            self._data[self._key(token)] = {"user": user, "created": now, "seen": now, "client": client}
            _write_json(self.path, self._data)
        return token

    def user_for(self, token: str) -> str | None:
        if not token:
            return None
        key, now = self._key(token), time.time()
        with self._lock:
            rec = self._data.get(key)
            if not rec:
                return None
            if now - rec["seen"] > SESSION_DAYS * 86400:
                del self._data[key]
                _write_json(self.path, self._data)
                return None
            if now - rec["seen"] > self.TOUCH_EVERY:
                rec["seen"] = now
                _write_json(self.path, self._data)
            return rec["user"]

    def end(self, token: str) -> None:
        with self._lock:
            if self._data.pop(self._key(token or ""), None) is not None:
                _write_json(self.path, self._data)


class LoginGuard:
    """Too many wrong passwords for one name lock that name for a while."""

    def __init__(self):
        self._lock = threading.Lock()
        self._fails: dict[str, list] = {}          # name -> [wrong so far, locked until]

    def wait_left(self, name: str) -> int:
        with self._lock:
            until = self._fails.get(name, [0, 0.0])[1]
        return max(0, int(until - time.time() + 0.999))

    def failed(self, name: str) -> bool:
        """Count one wrong password; True when this one locks the name."""
        with self._lock:
            if len(self._fails) > 10000:            # someone typing random names
                self._fails.clear()
            count = self._fails.get(name, [0, 0.0])[0] + 1
            if count >= MAX_FAILS:
                self._fails[name] = [0, time.time() + LOCK_SECONDS]
                return True
            self._fails[name] = [count, 0.0]
            return False

    def succeeded(self, name: str) -> None:
        with self._lock:
            self._fails.pop(name, None)


# ---------------------------------------------------------------- engines
class _EndWithMe:
    """A Windows job object: when the gateway's process ends - closed, crashed
    or killed - Windows ends every engine in the job as well."""

    def __init__(self, log):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            log(f"job object unavailable (error {ctypes.get_last_error()}); engines rely on watching the gateway")
            return
        self.handle, self._k32, self._log = job, k32, log

    def add(self, proc) -> None:
        if not self.handle:
            return
        k32 = self._k32
        h = k32.OpenProcess(0x0100 | 0x0001, False, proc.pid)   # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        try:
            if not h or not k32.AssignProcessToJobObject(self.handle, h):
                import ctypes
                self._log(f"engine pid {proc.pid} not put in the job (error {ctypes.get_last_error()})")
        finally:
            if h:
                k32.CloseHandle(h)


class EngineFailed(RuntimeError):
    pass


class Engine:
    def __init__(self, user: str, proc, port: int):
        self.user, self.proc, self.port = user, proc, port
        self.started = _stamp()

    def alive(self) -> bool:
        return self.proc.poll() is None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_json(port: int, path: str, timeout: float = 2):
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return json.loads(resp.read()) if resp.status == 200 else None
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return None


class EnginePool:
    def __init__(self, data: Path, log, events=None, role_of=None):
        self.data, self.log = data, log
        self.events = events or (lambda **kw: None)   # the activity log
        self.role_of = role_of or (lambda user: "")
        self.secret = secrets.token_urlsafe(32)       # never written to disk
        self.state_path = data / "server" / "engines.json"
        self._engines: dict[str, Engine] = {}
        self._inflight: dict[str, int] = {}           # calls each engine is working on now
        self._lock = threading.Lock()                 # the table
        self._start_lock = threading.Lock()           # one engine starting at a time
        self._job = _EndWithMe(log)

    def temp_dir(self, user: str) -> Path:
        return self.data / "server" / "temp" / user

    def get(self, user: str, *, failed: Engine | None = None, quiet: bool = False) -> Engine:
        """That person's running engine, started if needed. `failed` is an
        engine a caller could not reach: it is replaced, not handed back."""
        with self._lock:
            eng = self._engines.get(user)
        if eng is not None and eng is not failed and eng.alive():
            return eng
        with self._start_lock:
            with self._lock:
                eng = self._engines.get(user)
            if eng is not None and eng is not failed and eng.alive():
                return eng                            # another click started it meanwhile
            if eng is not None:
                why = "not answering" if eng.alive() else f"exited with code {eng.proc.returncode}"
                self.log(f"engine {user} {why}; starting it again")
                if not quiet:
                    self.events(source="server", action="engine_restart", outcome="server_error", person=user,
                                pc="", summary=f"{user}'s engine stopped ({why}) - started again")
                self._stop(eng)
            eng = self._start(user)
            with self._lock:
                self._engines[user] = eng
                self._save_state()
            return eng

    def warm(self, user: str) -> None:
        """Start an engine in the background (a login, the gateway starting)."""
        def run():
            try:
                self.get(user)
            except Exception as e:
                self.log(f"engine {user} not started ahead: {e}")
        threading.Thread(target=run, name=f"warm-{user}", daemon=True).start()

    def prestart(self, users: list[str]) -> None:
        def run():
            for user in users:
                try:
                    self.get(user)
                except Exception as e:
                    self.log(f"engine {user} not started ahead: {e}")
        threading.Thread(target=run, name="prestart", daemon=True).start()

    def call(self, user: str, method: str, args: list, *, pc: str = "", call_id: str = "") -> tuple[int, bytes]:
        body = json.dumps({"method": method, "args": args}).encode("utf-8")
        eng = self.get(user)
        conn = self._connect(eng)
        if conn is None:                   # nothing was sent: safe to start it again and send once
            eng = self.get(user, failed=eng)
            conn = self._connect(eng)
            if conn is None:
                raise EngineFailed("Your engine on the server is not answering.")
        with self._lock:
            self._inflight[user] = self._inflight.get(user, 0) + 1
        try:
            conn.request("POST", "/call", body, {"Content-Type": "application/json",
                                                 "X-Engine-Secret": self.secret,
                                                 "X-PC": pc, "X-Call-Id": call_id})
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (OSError, http.client.HTTPException) as e:
            # It got the call and then went away: it may have saved. Never send again.
            self.log(f"engine {user} lost during {method}: {e!r}")
            self.events(source="server", action=method, outcome="server_error", person=user, pc=pc,
                        call_id=call_id, summary=activity_log.describe(method, args),
                        message="The server's engine stopped while working on this; it was not repeated.",
                        detail={"error": repr(e)})
            return 502, json.dumps({
                "ok": False, "engine_lost": True,
                "error": "The server stopped while doing this, so it was not repeated. "
                         "Check whether it was saved before trying again."}).encode("utf-8")
        finally:
            conn.close()
            with self._lock:
                self._inflight[user] = max(0, self._inflight.get(user, 1) - 1)

    def check_engines(self, timeout: float = 15) -> list[str]:
        """The watchdog's round: an engine that is idle and does not answer is
        stuck - start it again. One busy with a call is left alone."""
        restarted = []
        with self._lock:
            engines = list(self._engines.items())
        for user, eng in engines:
            with self._lock:
                busy = self._inflight.get(user, 0) > 0
            if busy or (eng.alive() and _get_json(eng.port, "/health", timeout=timeout) is not None):
                continue
            why = "not answering" if eng.alive() else f"stopped (code {eng.proc.returncode})"
            self.log(f"watchdog: engine {user} {why}")
            self.events(source="server", action="engine_stuck", outcome="server_error", person=user, pc="",
                        summary=f"{user}'s engine was {why} - the watchdog started it again")
            try:
                self.get(user, failed=eng, quiet=True)
                restarted.append(user)
            except Exception as e:
                self.log(f"watchdog could not restart {user}: {e}")
        return restarted

    @staticmethod
    def _connect(eng: Engine):
        conn = http.client.HTTPConnection("127.0.0.1", eng.port, timeout=CALL_TIMEOUT)
        try:
            conn.connect()
            return conn
        except OSError:
            conn.close()
            return None

    def _start(self, user: str) -> Engine:
        user_dir = self.data / "users" / user
        temp = self.temp_dir(user)
        temp.mkdir(parents=True, exist_ok=True)
        user_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(BUSINESSHUB_DATA=str(self.data), BUSINESSHUB_ENGINE_SECRET=self.secret,
                   BUSINESSHUB_ROLE=str(self.role_of(user) or ""), TEMP=str(temp), TMP=str(temp))
        port = _free_port()
        console_path = user_dir / "engine-console.log"
        with open(console_path, "ab") as console:
            console.write(f"\n--- {_stamp()} starting on port {port}\n".encode("utf-8"))
            console.flush()
            proc = subprocess.Popen(
                [sys.executable, "-m", "server.engine", "--user", user, "--port", str(port),
                 "--parent-pid", str(os.getpid())],
                cwd=str(APP_DIR), env=env, stdin=subprocess.DEVNULL, stdout=console,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._job.add(proc)
        t0 = time.time()
        while time.time() - t0 < START_TIMEOUT and proc.poll() is None:
            health = _get_json(port, "/health")
            # the pid check: it is OUR engine answering, not something else on that port
            if health and health.get("user") == user and health.get("pid") == proc.pid:
                self.log(f"engine {user} started on port {port} (pid {proc.pid}) in {time.time() - t0:.1f}s")
                self.events(source="server", action="engine_start", outcome="info", person=user, pc="",
                            summary=f"{user}'s engine started", ms=int((time.time() - t0) * 1000))
                return Engine(user, proc, port)
            time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
            why = f"did not answer within {START_TIMEOUT}s"
        else:
            why = f"exited with code {proc.returncode}"
        self.log(f"engine {user} failed to start: {why}; see {console_path}")
        self.events(source="server", action="engine_failed", outcome="server_error", person=user, pc="",
                    summary=f"{user}'s engine could not start", message=f"{why}; details in {console_path}")
        raise EngineFailed(f"Your engine on the server could not start ({why}). "
                           f"The details are in {console_path} on the server.")

    def _stop(self, eng: Engine) -> None:
        if eng.alive():
            try:
                eng.proc.kill()
                eng.proc.wait(timeout=10)
            except Exception:
                pass

    def stop_all(self) -> None:
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()
            self._save_state()
        for eng in engines:
            self._stop(eng)

    def _save_state(self) -> None:
        _write_json(self.state_path, {u: {"pid": e.proc.pid, "port": e.port, "started": e.started}
                                      for u, e in self._engines.items()})


# ---------------------------------------------------------------- files
def doc_roots(data: Path) -> list[tuple[str, Path]]:
    """(name, folder) for every company's documents folder, read fresh each
    time so a changed Settings value applies at once. A company with no folder
    set gets nothing - never a guessed default."""
    roots = []
    for settings in sorted((data / "profiles").glob("*/settings.json")):
        try:
            s = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in ("documents_root", "po_root"):
            v = str(s.get(key) or "").strip()
            if v:
                roots.append((Path(v).name, Path(v)))
    return roots


def resolve_allowed(raw: str, roots: list[tuple[str, Path]]) -> tuple[Path, str] | None:
    """The real path and its name relative to the folders' parent, when `raw`
    lies inside one of `roots` after every '..', link and junction is followed;
    None otherwise."""
    if not raw or "\x00" in raw or not Path(raw).is_absolute():
        return None
    real = Path(os.path.realpath(raw))
    for name, root in roots:
        try:
            rel = real.relative_to(Path(os.path.realpath(root)))
        except ValueError:
            continue
        return real, "/".join([name, *rel.parts])
    return None


_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def upload_name(raw: str) -> str:
    """The last part of a picked file's name, safe to save on Windows."""
    name = re.split(r"[\\/]", raw or "")[-1].strip().strip(".")
    name = _UNSAFE_NAME.sub("_", name)[-120:]
    return name or "upload.bin"


def from_this_pc(host: str) -> bool:
    """A request from the server itself (never from the office network)."""
    return host == "::1" or str(host).startswith("127.")


def api_method_names() -> list[str]:
    """Public methods of backend.api.Api, read from the source - the gateway
    never imports the backend (that would open a company's data here too)."""
    tree = ast.parse((APP_DIR / "backend" / "api.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            return sorted(n.name for n in node.body
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and not n.name.startswith("_")
                          and not any(ast.unparse(d) in ("classmethod", "property") for d in n.decorator_list))
    raise RuntimeError("class Api not found in backend/api.py")


# ---------------------------------------------------------------- the door
class Gateway(ExclusiveHTTPServer):
    def __init__(self, addr, data: Path):
        self.data = data
        self.log = Log(data / "server" / "gateway.log")
        self.users = UserStore(data / "users" / "users.json")
        self.sessions = Sessions(data / "server" / "sessions.json")
        self.guard = LoginGuard()
        names = api_method_names()
        self.forwarded = [n for n in names if n not in LOCAL_ONLY]
        self.local = [n for n in LOCAL_ONLY if n in names]
        super().__init__(addr, Handler)                 # binds: a second gateway stops here
        activity_log.configure(data / "logs" / "activity.db")
        self.pool = EnginePool(data, self.log, events=activity_log.record,
                               role_of=lambda u: (self.users.get(u) or {}).get("role", ""))

    def watchdog(self) -> None:
        """Every few minutes: stuck engines, and a disk filling up."""
        last_disk_warning = 0.0
        while True:
            time.sleep(WATCH_EVERY)
            try:
                self.pool.check_engines()
                free = shutil.disk_usage(self.data).free
                if free < LOW_DISK and time.time() - last_disk_warning > 6 * 3600:
                    last_disk_warning = time.time()
                    self.log(f"watchdog: disk nearly full ({free / 1024 ** 3:.1f} GB free)")
                    activity_log.record(source="server", action="disk_low", outcome="server_error", person="",
                                        pc="", summary=f"The server's disk is nearly full: "
                                                       f"{free / 1024 ** 3:.1f} GB left")
            except Exception as e:
                self.log(f"watchdog: {e!r}")

    def enabled(self, user: str) -> bool:
        rec = self.users.get(user)
        return bool(rec and rec.get("enabled"))

    def file_roots(self, user: str) -> list[tuple[str, Path]]:
        return doc_roots(self.data) + [("_preview", self.pool.temp_dir(user) / "po_tool_preview")]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: Gateway

    def log_message(self, fmt, *args):      # never to stderr: a full pipe would hang the server
        pass

    # ---- replies
    def _reply(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
               headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload, headers: dict | None = None) -> None:
        self._reply(status, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                    headers=headers)

    def _drain(self, limit: int = 8 << 20) -> None:
        """Read an unwanted body before answering (Windows resets a connection
        closed with unread bytes, and the person would see no answer at all)."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= limit:
            self.rfile.read(n)
        self.close_connection = True

    def _need_login(self) -> None:
        self.close_connection = True
        self._json(401, {"ok": False, "login": True, "error": "Please log in again."})

    def _user(self) -> str | None:
        h = self.headers.get("Authorization", "")
        token = h[7:].strip() if h[:7].lower() == "bearer " else ""
        user = self.server.sessions.user_for(token)
        return user if user and self.server.enabled(user) else None

    @property
    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    @property
    def _pc(self) -> str:
        """The PC as the activity log names it: 'ACCOUNTS-PC (192.168.1.21)'."""
        name = re.sub(r"[^\w .-]", "", self.headers.get("X-PC-Name", ""))[:40].strip()
        return f"{name} ({self._ip})" if name else self._ip

    # ---- GET
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path == "/health":
            return self._json(200, {"ok": True, "server": "business-hub", "time": _stamp()})
        if path == "/api-methods":
            return self._json(200, {"methods": self.server.forwarded, "local": self.server.local})
        if path in ("/files", "/files/info"):
            return self._files(url.query, info=path == "/files/info")
        if path == "/selftest/pdf":
            return self._selftest_pdf()
        if path == "/":
            return self._reply(302, b"", headers={"Location": "/templates/index.html"})
        if path.startswith("/templates/"):
            return self._screen_file(TEMPLATES, path[len("/templates/"):], ())
        if path.startswith("/static/"):
            return self._screen_file(STATIC, path[len("/static/"):], PRIVATE_STATIC)
        return self._json(404, {"ok": False, "error": "not found"})

    def _selftest_pdf(self) -> None:
        """The PDF proof (SERVER SETUP/pdf_fingerprint.py) run BY the server, so
        its documents are made with the server's own account, fonts and Chrome
        - exactly what the office will get. Only from the server itself."""
        if not from_this_pc(self.client_address[0]):
            return self._json(403, {"ok": False, "error": "Only from the server itself."})
        kit = APP_DIR.parent / "SERVER SETUP"
        ref = kit / "reference-fingerprint.json"
        if not (kit / "pdf_fingerprint.py").is_file() or not ref.is_file():
            return self._json(404, {"ok": False, "error": f"No proof kit in {kit}."})
        try:
            r = subprocess.run([sys.executable, str(kit / "pdf_fingerprint.py"), "compare", str(ref)],
                               cwd=str(kit), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ok, lines = r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-60:]
        except subprocess.TimeoutExpired:
            ok, lines = False, ["The proof did not finish within 15 minutes."]
        self.server.log(f"PDF proof run by the server: {'IDENTICAL' if ok else 'DIFFERENT'}")
        self._json(200, {"ok": ok, "output": lines})

    def _screen_file(self, base: Path, rel: str, private: tuple) -> None:
        parts = urllib.parse.unquote(rel).split("/")
        bad = any(p in ("", ".", "..") or p != p.rstrip(". ") or any(c in p for c in '\\:\x00')
                  for p in parts)
        real = None if bad else Path(os.path.realpath(base.joinpath(*parts)))
        root = Path(os.path.realpath(base))
        if (real is None or not real.is_relative_to(root) or not real.is_file()
                or real.relative_to(root).parts[0].lower() in private):
            return self._json(404, {"ok": False, "error": "not found"})
        st = real.stat()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:      # unchanged: the PC keeps its copy
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self._reply(200, real.read_bytes(), _content_type(real),
                    headers={"ETag": etag, "Cache-Control": "no-cache"})

    def _files(self, query: str, info: bool) -> None:
        user = self._user()
        if not user:
            return self._need_login()
        raw = (urllib.parse.parse_qs(query).get("path") or [""])[0]
        found = resolve_allowed(raw, self.server.file_roots(user))
        if not found:
            self.server.log(f"{user} from {self._ip}: refused file {raw!r}")
            activity_log.record(source="server", action="files", outcome="refused", person=user, pc=self._pc,
                                summary="Refused a file outside the documents folders", message=raw[:300])
            return self._json(403, {"ok": False, "error": "That file is not in the documents folders."})
        real, rel = found
        if info:
            if not real.exists():
                return self._json(404, {"ok": False, "error": "File not found."})
            st = real.stat()
            return self._json(200, {"ok": True, "relative": rel, "folder": real.is_dir(),
                                    "size": st.st_size, "mtime": st.st_mtime})
        if not real.is_file():
            return self._json(404, {"ok": False, "error": "File not found."})
        body = real.read_bytes()            # whole, so a file being rewritten never arrives half old
        self._reply(200, body, _content_type(real), headers={
            "X-Relative-Path": urllib.parse.quote(rel, safe="/"),
            "X-File-Mtime": str(real.stat().st_mtime)})

    # ---- POST
    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/upload":
            return self._upload()
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            self.close_connection = True
            return self._json(413, {"ok": False, "error": "Too large."})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"ok": False, "error": "bad request"})
        if path == "/auth/login":
            return self._login(body)
        if path == "/auth/check":
            user = self._user()
            if not user:
                return self._need_login()
            return self._json(200, {"ok": True, **(self.server.users.get(user) or {})})
        if path == "/auth/logout":
            h = self.headers.get("Authorization", "")
            token = h[7:].strip() if h[:7].lower() == "bearer " else ""
            who = self.server.sessions.user_for(token)
            self.server.sessions.end(token)
            if who:
                activity_log.record(source="login", action="logout", outcome="info", person=who, pc=self._pc,
                                    summary=f"{who} logged out")
            return self._json(200, {"ok": True})
        if path.startswith("/api/"):
            return self._api(path[len("/api/"):], body)
        return self._json(404, {"ok": False, "error": "not found"})

    def _login(self, body: dict) -> None:
        name = str(body.get("user") or "").strip().lower()
        guard, log = self.server.guard, self.server.log
        wait = guard.wait_left(name)
        if wait:
            log(f"login refused for {name!r} from {self._ip}: locked for {wait}s")
            activity_log.record(source="login", action="login", outcome="warning", person=name, pc=self._pc,
                                summary=f"Login refused for '{name}': too many wrong passwords",
                                message=f"locked for another {wait} s")
            minutes = max(1, round(wait / 60))
            return self._json(429, {"ok": False, "wait_seconds": wait,
                                    "error": f"Too many wrong passwords. Try again in {minutes} minute"
                                             f"{'s' if minutes > 1 else ''}."})
        who = self.server.users.verify(name, str(body.get("password") or ""))
        if not who:
            locked = guard.failed(name)
            log(f"login FAILED for {name!r} from {self._ip}" + (" - name locked" if locked else ""))
            activity_log.record(source="login", action="login", outcome="refused", person=name, pc=self._pc,
                                summary=f"Wrong name or password for '{name}'",
                                message="the name is now locked for a while" if locked else "")
            return self._json(401, {"ok": False, "error": "Wrong name or password."})
        guard.succeeded(name)
        token = self.server.sessions.create(who["user"], client=self._ip)
        log(f"login {who['user']} from {self._ip}")
        activity_log.record(source="login", action="login", outcome="ok", person=who["user"], pc=self._pc,
                            summary=f"{who.get('display') or who['user']} logged in")
        self.server.pool.warm(who["user"])          # the first screen should not wait for it
        return self._json(200, {"ok": True, "token": token, **who})

    def _api(self, method: str, body: dict) -> None:
        user = self._user()
        if not user:
            return self._need_login()
        if method in LOCAL_ONLY:
            return self._json(403, {"ok": False, "error": f"{method} runs on your own PC, not on the server."})
        if method in AT_THE_SERVER:
            return self._json(403, {"ok": False, "error": "Backups and restores are done at the server "
                                                          "itself in the office version."})
        if method not in self.server.forwarded:
            return self._json(404, {"ok": False, "error": f"no such method: {method}"})
        args = body.get("args") or []
        if not isinstance(args, list):
            return self._json(400, {"ok": False, "error": "args must be a list"})
        t0 = time.time()
        try:
            status, raw = self.server.pool.call(user, method, args, pc=self._pc, call_id=secrets.token_hex(6))
        except EngineFailed as e:
            activity_log.record(source="server", action=method, outcome="server_error", person=user, pc=self._pc,
                                summary=activity_log.describe(method, args), message=str(e))
            return self._json(503, {"ok": False, "error": str(e)})
        took = time.time() - t0
        if status >= 500 or took > 20:
            self.server.log(f"{user}: {method} -> {status} in {took:.1f}s")
        self._reply(status, raw)

    def _upload(self) -> None:
        user = self._user()
        if not user:
            self._drain()
            return self._need_login()
        try:
            n = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            self.close_connection = True
            return self._json(413, {"ok": False, "error": "The file is too large, or its size was not sent."})
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        name = upload_name((query.get("name") or [""])[0])
        folder = self.server.data / "users" / user / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}-{name}"
        left = n
        with open(dest, "wb") as f:
            while left:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            dest.unlink(missing_ok=True)
            self.close_connection = True
            return self._json(400, {"ok": False, "error": "The upload was cut off."})
        self._json(200, {"ok": True, "path": str(dest), "name": name, "size": n})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Business Hub office server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-prestart", action="store_true",
                    help="start each engine on first use instead of when the server starts")
    args = ap.parse_args(argv)
    data = data_dir()
    try:
        server = Gateway((args.host, args.port), data)
    except OSError as e:
        Log(data / "server" / "gateway.log")(f"start refused: port {args.port} is taken ({e})")
        print(f"Cannot open port {args.port} ({e}). Is the server already running?", file=sys.stderr)
        return 2
    server.log(f"gateway started on {args.host}:{args.port} (pid {os.getpid()})")
    if not args.no_prestart:
        server.pool.prestart([u["user"] for u in server.users.list() if u and u.get("enabled")])
    threading.Thread(target=server.watchdog, name="watchdog", daemon=True).start()
    print(f"Business Hub server on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.pool.stop_all()
        server.server_close()
        server.log("gateway stopped")
        activity_log.flush(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
