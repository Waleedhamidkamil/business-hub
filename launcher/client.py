"""The launcher's side of the office server - what runs on each person's PC
(Phase 2, 2026-10-22).

The screens come from the server (/templates, /static). Every bridge call they
make lands here, in `LauncherApi`, which pywebview exposes as
window.pywebview.api:

  * the server's methods are forwarded to the gateway (POST /api/<name>). The
    list comes from GET /api-methods after login - nothing is hard-coded, so a
    new method on the server needs no new launcher;
  * nine act on THIS PC (open / show / copy / print a PDF, the file pickers,
    the launch card). A file comes down into the person's own copy,
    Documents\\Business Hub\\<company folder>\\..., a preview into the temp
    folder - never anywhere else, whatever the server says;
  * every document the person saves (quotation, DN, invoice, PO, statement)
    is copied down to that folder at once: the server's copy is the master,
    this one is theirs.

Nothing from the app's backend is imported: a PC has only this folder, Python
and pywebview.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

VERSION = "2026-10-22"
DEFAULT_PORT = 8765
CONNECT_TIMEOUT = 8
CALL_TIMEOUT = 600                 # a big report or a long PDF batch
CREATE_NO_WINDOW = 0x08000000
# The calls the server refuses because they act on a PC (server/gateway.py
# LOCAL_ONLY); each is a method of LauncherApi below.
LOCAL_CALLS = ("open_path", "reveal_in_folder", "copy_file_to_clipboard", "print_file",
               "dm_pick_file", "dm_pick_folder", "bulk_pick_spreadsheet",
               "attach_doc_signed_copy", "gate_ready")
LAUNCHER_CALLS = ("launcher_state", "launcher_login", "launcher_resume", "launcher_logout")
# Saves whose PDF the person keeps a copy of.
KEEP_A_COPY = ("save_quote", "save_dn", "save_inv", "save_po", "make_client_statement")
AT_THE_SERVER = "In the office version this is done at the server itself."
PC_NAME = (os.environ.get("COMPUTERNAME") or socket.gethostname() or "")[:40]
FLUSH_EVERY = 30                   # seconds between sending this PC's notes to the activity log
SPREADSHEET_TYPES = ("Spreadsheets (*.xlsx;*.xlsm;*.csv;*.tsv;*.txt)", "Excel (*.xlsx;*.xlsm)",
                     "CSV and TSV (*.csv;*.tsv;*.txt)",
                     "All supported (*.xlsx;*.xlsm;*.xls;*.xlsb;*.ods;*.csv;*.tsv;*.txt)")
SIGNED_COPY_TYPES = ("Signed copy (*.pdf;*.png;*.jpg;*.jpeg)",)
_MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def login_html() -> str:
    return Path(__file__).with_name("login.html").read_text(encoding="utf-8")


class ServerError(RuntimeError):
    """Something between this PC and the server went wrong; the message is
    written for the person reading it."""


class NetworkError(ServerError):
    """The server could not be reached, or the line broke mid-way."""


class FileMissing(ServerError):
    """The server has no such file."""


class NotAllowed(ServerError):
    """The server will not hand that file out."""


class PcTrouble(RuntimeError):
    """This PC could not do it (no PDF viewer, the clipboard, Explorer)."""


def _pc_outcome(e: BaseException) -> str:
    """Whose fault, for the activity log."""
    if isinstance(e, NetworkError):
        return "network"
    if isinstance(e, NotAllowed):
        return "refused"
    if isinstance(e, ServerError):
        return "server_error"
    if isinstance(e, (PcTrouble, OSError)):
        return "warning"
    return "app_error"


# ---------------------------------------------------------------- this PC
def appdata() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Business Hub"


def documents_folder() -> Path:
    """The person's real Documents folder (it may have been moved, e.g. to OneDrive)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            value, _ = winreg.QueryValueEx(k, "Personal")
        return Path(os.path.expandvars(value))
    except OSError:
        return Path.home() / "Documents"


def local_target(relative: str, docs_root: Path, cache_root: Path) -> Path:
    """Where a server file lands on this PC: documents under docs_root, previews
    under cache_root\\preview. Never anywhere else, whatever `relative` says."""
    parts = str(relative or "").replace("\\", "/").split("/")
    bad = (not parts or any(p in ("", ".", "..") or p != p.rstrip(". ")
                            or any(c in p for c in ':*?"<>|\x00') for p in parts)
           or (parts[0] == "_preview" and len(parts) < 2))
    if not bad:
        if parts[0] == "_preview":
            base, rest = cache_root / "preview", parts[1:]
        else:
            base, rest = docs_root, parts
        target = base.joinpath(*rest)
        if Path(os.path.realpath(target)).is_relative_to(Path(os.path.realpath(base))):
            return target
    raise ServerError(f"The server named a file this PC will not write: {relative!r}")


def normalize_server(text: str) -> str:
    """'businesshub', 'businesshub:8765' or 'http://192.168.1.20:8765/' -> 'http://host:port'."""
    t = (text or "").strip().rstrip("/")
    if not t:
        raise ValueError("Type the server's name or address.")
    if "://" not in t:
        t = "http://" + t
    try:
        u = urllib.parse.urlsplit(t)
        port = u.port or DEFAULT_PORT
    except ValueError:
        raise ValueError(f"'{text}' is not a server address.") from None
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"'{text}' is not a server address.")
    return f"{u.scheme}://{u.hostname}:{port}"


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows' own scrambling for one Windows user on one PC (DPAPI): the saved
    session is useless copied anywhere else."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = (ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob))
    fn.restype = wintypes.BOOL
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):   # UI_FORBIDDEN
        raise OSError(ctypes.get_last_error(), "Windows could not protect the saved login")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(text: str) -> str:
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(blob: str) -> str:
    return _dpapi(base64.b64decode(blob), False).decode("utf-8")


def load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))    # Notepad may add a BOM
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Log:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, line: str) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
            except OSError:
                pass


# ---------------------------------------------------------------- the server
class Server:
    """HTTP to the gateway, as one logged-in person."""

    def __init__(self, base: str, token: str = ""):
        self.base, self.token = base, token

    @property
    def app_url(self) -> str:
        return self.base + "/templates/index.html"

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict | None = None, timeout: float = CALL_TIMEOUT):
        """(status, body bytes, headers). Raises ServerError when the server
        cannot be reached, or the line broke while it was working."""
        u = urllib.parse.urlsplit(self.base)
        cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
        conn = cls(u.hostname, u.port, timeout=CONNECT_TIMEOUT)
        h = dict(headers or {})
        h.setdefault("X-PC-Name", PC_NAME)          # the activity log names the PC
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        try:
            try:
                conn.connect()
            except OSError as e:
                raise NetworkError(f"Can't reach the office server ({self.base}). "
                                  "Check the network, then try again.") from e
            try:
                conn.sock.settimeout(timeout)
                conn.request(method, path, body=body, headers=h)
                resp = conn.getresponse()
                return resp.status, resp.read(), resp.headers
            except (OSError, http.client.HTTPException) as e:
                raise NetworkError("The line to the server broke while it was working on this. "
                                  "Check whether it was done before trying again.") from e
        finally:
            conn.close()

    def json_call(self, method: str, path: str, payload=None, timeout: float = CALL_TIMEOUT):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        status, raw, _ = self.request(method, path, body,
                                      {"Content-Type": "application/json"} if body is not None else None,
                                      timeout)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return status, data


def _quote_path(path: str) -> str:
    return "?path=" + urllib.parse.quote(str(path), safe="")


# ---------------------------------------------------------------- the bridge
class LauncherApi:
    """window.pywebview.api on a PC.

    pywebview lists every PUBLIC attribute and walks into public objects
    (webview/util.py get_functions), so everything that is not a bridge call
    is underscore-private here - a public Path or window would hand the page
    its methods."""

    def __init__(self, settings_path: Path | None = None, docs_root: Path | None = None,
                 cache_root: Path | None = None):
        self._settings_path = settings_path or (appdata() / "launcher.json")
        self._settings = load_settings(self._settings_path)
        self._docs_root = docs_root or (documents_folder() / "Business Hub")
        self._cache_root = cache_root or (Path(tempfile.gettempdir()) / "Business Hub")
        self._log = Log(self._settings_path.parent / "launcher.log")
        self._server: Server | None = None
        self._forwarded: frozenset[str] = frozenset()
        self._window = None
        self._notice = ""
        # what happened on this PC, kept until the server's activity log takes it
        self._pending_path = self._settings_path.parent / "pending-log.jsonl"
        self._pending_lock = threading.Lock()
        self._flusher = None
        # the PC-side actions, swapped out by the tests
        self._startfile = getattr(os, "startfile", None)
        self._run = subprocess.run
        self._pick = self._dialog

    # ---- what pywebview sees
    def __dir__(self):
        return sorted(set(object.__dir__(self)) | self.__dict__.get("_forwarded", frozenset()))

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self.__dict__.get("_forwarded", frozenset()):
            raise AttributeError(name)

        def call(*args):
            return self._call(name, args)

        call.__name__ = call.__qualname__ = name
        return call

    # ---- forwarding
    def _call(self, name: str, args) -> object:
        server = self._guard()
        try:
            status, body = server.json_call("POST", f"/api/{name}", {"args": list(args)})
        except NetworkError as e:
            self._note(name, "network", f"Could not reach the server for {name}", str(e), kind="network")
            raise
        if status == 401:
            self._back_to_login("You were logged out. Please log in again.")
            raise ServerError("Please log in again.")
        if status != 200 or not body.get("ok"):
            raise ServerError(body.get("error") or f"The server answered {status}.")
        result = body.get("result")
        if name in KEEP_A_COPY and isinstance(result, dict) and result.get("ok"):
            self._keep_copies(result)
        return result

    def _guard(self) -> Server:
        server = self._server
        if server is None:
            raise ServerError("Not logged in.")
        if self._window is not None:
            url = self._window.get_current_url()
            # None = the login page (HTML this launcher loaded itself)
            if url is not None and not url.startswith(server.base + "/"):
                self._log(f"refused a call from {url}")
                raise ServerError("This page may not use the office server.")
        return server

    def _enter(self, server: Server) -> None:
        """Logged in: learn the server's methods and start forwarding them."""
        status, body = server.json_call("GET", "/api-methods", timeout=30)
        if status != 200 or not isinstance(body.get("methods"), list):
            raise ServerError(f"The server did not list its functions ({status}).")
        unknown = sorted(set(body.get("local") or []) - set(LOCAL_CALLS))
        if unknown:            # a newer server with a PC-side call this launcher lacks
            self._log(f"the server lists PC-side calls this launcher does not have: {unknown}")
        mine = set(LOCAL_CALLS) | set(LAUNCHER_CALLS)
        self._forwarded = frozenset(n for n in body["methods"]
                                    if isinstance(n, str) and n and not n.startswith("_") and n not in mine)
        self._server = server
        self._start_flusher()

    # ---- this PC's notes for the office activity log
    def _note(self, action: str, outcome: str, summary: str = "", message: str = "", ref: str = "",
              kind: str = "pc") -> None:
        """Something that happened on this PC. Kept in a small file until the
        server takes it, so trouble during a network cut still arrives later."""
        ev = {"kind": kind, "t": time.time() * 1000, "action": action, "outcome": outcome,
              "summary": summary[:300], "message": message[:1000], "ref": ref[:80]}
        if outcome != "ok":
            self._log(f"{action}: {outcome} - {message}")
        try:
            with self._pending_lock:
                self._pending_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._pending_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _flush_notes(self) -> bool:
        """Hand the waiting notes to the server; True when none are left."""
        server = self._server
        if server is None:
            return False
        with self._pending_lock:
            try:
                lines = self._pending_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                return True
            events = []
            for line in lines[-500:]:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
            if events:
                try:
                    status, body = server.json_call("POST", "/api/log_client_events",
                                                    {"args": [{"sent": time.time() * 1000, "events": events}]},
                                                    timeout=30)
                except ServerError:
                    return False
                if status != 200 or not body.get("ok"):
                    return False
            self._pending_path.unlink(missing_ok=True)
            return True

    def _start_flusher(self) -> None:
        if self._flusher is None:
            def run():
                while True:
                    try:
                        self._flush_notes()
                    except Exception:
                        pass
                    time.sleep(FLUSH_EVERY)

            self._flusher = threading.Thread(target=run, name="activity-notes", daemon=True)
            self._flusher.start()

    def _back_to_login(self, notice: str) -> None:
        self._notice = notice
        self._server = None
        self._forwarded = frozenset()
        if self._settings.pop("token", None) is not None:
            save_settings(self._settings_path, self._settings)
        if self._window is not None:
            self._window.load_html(login_html())

    # ---- the login page
    def launcher_state(self) -> dict:
        """What the login page shows: the server, the last name, any notice."""
        s, notice = self._settings, self._notice
        self._notice = ""
        return {"server": s.get("server") or "", "user": s.get("user") or "",
                "saved_session": bool(s.get("token")), "notice": notice, "version": VERSION}

    def launcher_login(self, server_text: str, user: str, password: str, remember: bool = False) -> dict:
        try:
            base = normalize_server(server_text)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        server = Server(base)
        try:
            status, body = server.json_call("POST", "/auth/login", {"user": user, "password": password}, timeout=30)
            if status != 200 or not body.get("ok"):
                return {"ok": False, "error": body.get("error") or f"The server answered {status}."}
            server.token = body["token"]
            self._enter(server)
        except ServerError as e:
            return {"ok": False, "error": str(e)}
        self._settings.update(server=base, user=body.get("user") or user)
        self._settings.pop("token", None)
        if remember:
            try:
                self._settings["token"] = protect(server.token)
            except OSError as e:
                self._log(f"could not keep the login: {e}")
        save_settings(self._settings_path, self._settings)
        self._log(f"logged in as {body.get('user')} at {base}")
        return {"ok": True, "next": server.app_url, "display": body.get("display") or ""}

    def launcher_resume(self) -> dict:
        """'Keep me logged in': back in with the saved session, if it still works."""
        url = self._resume()
        if url:
            return {"ok": True, "next": url}
        notice, self._notice = self._notice, ""
        return {"ok": False, "error": notice or "Please log in."}

    def _resume(self) -> str | None:
        s = self._settings
        if not (s.get("server") and s.get("token")):
            return None
        try:
            server = Server(s["server"], unprotect(s["token"]))
        except (OSError, ValueError):
            s.pop("token", None)
            save_settings(self._settings_path, s)
            return None
        try:
            status, _ = server.json_call("POST", "/auth/check", {}, timeout=30)
            if status != 200:
                s.pop("token", None)
                save_settings(self._settings_path, s)
                self._notice = "Your saved login has ended. Please log in again."
                return None
            self._enter(server)
            return server.app_url
        except ServerError as e:
            self._notice = str(e)
            return None

    def launcher_logout(self) -> dict:
        server = self._server
        if server is not None:
            try:
                server.json_call("POST", "/auth/logout", {}, timeout=10)
            except ServerError:
                pass
        self._back_to_login("")
        return {"ok": True}

    # ---- files: the person's own copy
    def _ensure_local(self, path: str) -> Path:
        """The server's file, current, in this person's folder (downloaded only
        when the copy here is missing or older)."""
        server = self._guard()
        status, info = server.json_call("GET", "/files/info" + _quote_path(path), timeout=60)
        if status == 401:
            self._back_to_login("You were logged out. Please log in again.")
            raise ServerError("Please log in again.")
        if status == 404:
            raise FileMissing(f"File not found: {path}")
        if status == 403:
            raise NotAllowed(f"This file is not in the office documents folders: {path}")
        if status != 200:
            raise ServerError(info.get("error") or f"The server answered {status}.")
        target = local_target(info.get("relative", ""), self._docs_root, self._cache_root)
        if info.get("folder"):
            target.mkdir(parents=True, exist_ok=True)
            return target
        try:
            st = target.stat()
            if st.st_size == info.get("size") and abs(st.st_mtime - float(info.get("mtime") or 0)) < 2:
                return target
        except OSError:
            pass
        status, data, headers = server.request("GET", "/files" + _quote_path(path), timeout=CALL_TIMEOUT)
        if status != 200:
            if status == 404:
                raise FileMissing(f"File not found: {path}")
            raise ServerError(f"The server answered {status}.")
        mtime = float(headers.get("X-File-Mtime") or info.get("mtime") or 0)
        return self._write_local(target, data, mtime)

    def _write_local(self, target: Path, data: bytes, mtime: float) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        part.write_bytes(data)
        try:
            os.replace(part, target)
        except PermissionError:
            # the older copy is open in a PDF viewer: the new one goes beside the cache
            part.unlink(missing_ok=True)
            target = self._cache_root / "open" / f"{target.stem} {datetime.now():%H%M%S}{target.suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if mtime:
            os.utime(target, (mtime, mtime))
        return target

    def _keep_copies(self, result: dict) -> None:
        """The person's own copy of what they just saved (the server's is the master)."""
        paths = [result.get("path")] + [s.get("path") for s in result.get("saved") or [] if isinstance(s, dict)]
        paths = [p for p in paths if isinstance(p, str) and p]
        if not paths:
            return

        def run():
            for p in paths:
                try:
                    self._ensure_local(p)
                except Exception as e:
                    self._log(f"no copy of {p} on this PC: {e}")

        threading.Thread(target=run, name="keep-a-copy", daemon=True).start()

    def _upload(self, local_path: str) -> str:
        p = Path(local_path)
        status, raw, _ = self._guard().request(
            "POST", "/upload?name=" + urllib.parse.quote(p.name, safe=""), p.read_bytes(),
            {"Content-Type": "application/octet-stream"})
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        if status != 200 or not body.get("ok"):
            raise ServerError(body.get("error") or f"The upload was refused ({status}).")
        return body["path"]

    def _dialog(self, file_types: tuple) -> str | None:
        import webview
        kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        result = self._window.create_file_dialog(kind, allow_multiple=False, file_types=file_types)
        if not result:
            return None
        return str(result[0] if isinstance(result, (list, tuple)) else result)

    # ---- the nine calls that act on this PC (same answers as the desktop app's)
    def _pc_action(self, action: str, words: str, path: str, do) -> dict:
        """Run one PC-side file action; the desktop app's answer, and a note."""
        name = Path(str(path or "")).name
        try:
            do()
        except Exception as e:
            self._note(action, _pc_outcome(e), f"{words} {name}".strip(), str(e), ref=Path(name).stem)
            return {"ok": False, "error": str(e)}
        self._note(action, "ok", f"{words} {name}".strip(), ref=Path(name).stem)
        return {"ok": True}

    def open_path(self, path: str) -> dict:
        """Open the file in this PC's own app, from the person's copy."""
        return self._pc_action("open_path", "Open", path,
                               lambda: self._startfile(str(self._ensure_local(path))))

    def reveal_in_folder(self, path: str) -> dict:
        def do():
            local = self._ensure_local(path)
            cmd = ["explorer", str(local)] if local.is_dir() else ["explorer", "/select,", str(local)]
            self._run(cmd, creationflags=CREATE_NO_WINDOW)

        return self._pc_action("reveal_in_folder", "Show in its folder", path, do)

    def copy_file_to_clipboard(self, path: str) -> dict:
        """The FILE itself on the clipboard, so pasting into WhatsApp / Outlook
        attaches it - the same PowerShell call as the desktop app."""
        def do():
            ps_path = str(self._ensure_local(path)).replace("'", "''")
            try:
                self._run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           f"Set-Clipboard -Path '{ps_path}'"],
                          check=True, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            except subprocess.CalledProcessError as e:
                raise PcTrouble((e.stderr or e.stdout or str(e)).strip()) from None

        return self._pc_action("copy_file_to_clipboard", "Copy to the clipboard", path, do)

    def print_file(self, path: str) -> dict:
        return self._pc_action("print_file", "Print", path,
                               lambda: self._startfile(str(self._ensure_local(path)), "print"))

    def dm_pick_file(self, title: str = "Pick a file", file_types: list | None = None) -> dict:
        # its only users are Data Manager's restore and PDF re-link, which act
        # on the server's own disk
        return {"ok": False, "error": AT_THE_SERVER}

    def dm_pick_folder(self, title: str = "Pick a folder") -> dict:
        return {"ok": False, "error": AT_THE_SERVER}

    def bulk_pick_spreadsheet(self) -> dict:
        """Pick a spreadsheet on this PC; the server reads its copy."""
        try:
            self._guard()
            picked = self._pick(file_types=SPREADSHEET_TYPES)
            if not picked:
                return {"ok": False, "cancelled": True}
            return {"ok": True, "path": self._upload(picked)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def attach_doc_signed_copy(self, kind: str, doc_no: str) -> dict:
        """Pick the client's signed copy on this PC and send it with the document."""
        try:
            self._guard()
            picked = self._pick(file_types=SIGNED_COPY_TYPES)
            if not picked:
                return {"ok": False, "cancelled": True}
            p = Path(picked)
            mime = _MIME.get(p.suffix.lower())
            if not mime:
                return {"ok": False, "error": f"Unsupported file type: {p.suffix or 'none'}"}
            data_uri = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
            return self._call("attach_doc_signed_copy_data", (kind, doc_no, data_uri, p.name))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def gate_ready(self, marks: dict | None = None) -> dict:
        """No launch card on a PC: the page goes straight on."""
        return {"ok": True, "gate": False}
