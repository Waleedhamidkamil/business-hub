"""One person's engine (office server Phase 2, 2026-10-22).

Today's `backend.api.Api`, in its own process, for ONE logged-in person. The
gateway starts it and is the only thing that may call it:

    BUSINESSHUB_ENGINE_SECRET=<secret>  python -m server.engine --user waleed --port 9101

Why one process per person: the app was built for one user - one "current
company", caches in memory, a registry file that says which company is open.
Inside its own process each person IS that one user, so the code works as it
always has, and one person's heavy report or crash never touches another's.

What makes it per person:
  * its own company registry, data/users/<name>/profile-registry.json, set
    BEFORE backend.config is imported (config reads the company at import);
  * BUSINESSHUB_USER=<name>, so document numbers are held in that person's
    name (number_claims, Phase 1);
  * it listens on 127.0.0.1 only and checks the gateway's secret.

POST /call {"method", "args"} -> {"ok": true, "result": ...}; GET /health; GET /methods.
"""
from __future__ import annotations

import argparse
import hmac
import inspect
import json
import os
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from server import ExclusiveHTTPServer  # noqa: E402


def _end_with(parent_pid: int) -> None:
    """Exit the moment the gateway exits, however it exits. (The gateway also
    puts its engines in a job object; this covers the moment before that.)"""
    if os.name != "nt":
        return
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    k32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    handle = k32.OpenProcess(0x00100000, 0, parent_pid)        # SYNCHRONIZE
    if not handle:
        os._exit(3)                                             # the gateway is already gone

    def watch():
        k32.WaitForSingleObject(handle, 0xFFFFFFFF)             # until it ends
        os._exit(0)

    threading.Thread(target=watch, name="end-with-gateway", daemon=True).start()


def _personal_registry(user: str) -> None:
    """Point the profile manager at this person's own registry file."""
    from server import data_dir
    from backend import profile_manager as pm
    data = data_dir()
    if data.resolve() != (pm._APP_DIR / "data").resolve():
        pm._MANAGER = pm.ProfileManager(data, APP_DIR / "static")
    m = pm._MANAGER
    shared = m.registry_path
    mine = data / "users" / user / "profile-registry.json"
    if not mine.exists():
        mine.parent.mkdir(parents=True, exist_ok=True)
        if shared.exists():
            mine.write_text(shared.read_text(encoding="utf-8"), encoding="utf-8")
    m.registry_path = mine
    m._registry_lock = pm._registry_lock(mine)
    if not mine.exists():
        m.create_registry("abraj")


def start_backend(user: str):
    """Open the company data the way app._main does, minus the window."""
    os.environ["BUSINESSHUB_USER"] = user
    _personal_registry(user)
    from backend import activity_log
    from backend.profile_manager import manager as profile_manager
    from server import data_dir
    activity_log.configure(data_dir() / "logs" / "activity.db")      # one log for the whole office
    bootstrap = profile_manager().bootstrap_profiles(
        legacy_db=data_dir() / "po_tool.db",
        legacy_settings=data_dir() / "settings.json",
        legacy_assets=APP_DIR / "static")
    if not bootstrap.get("ok"):
        raise RuntimeError(f"company profile setup failed: {bootstrap.get('error')}")
    from backend import database, migrations
    from backend.api import Api
    database.init_db()
    failed = [s for s in migrations.run_all() if s.get("status") == "failed"]
    if failed:
        raise RuntimeError("database migrations failed: " + ", ".join(str(s.get("key")) for s in failed))
    api = Api()
    try:
        from backend import boot_cache
        boot_cache.start(api)            # warm the dashboard caches, as the desktop app does
    except Exception:
        pass
    return api


def public_methods(api) -> list[str]:
    return sorted(n for n, v in inspect.getmembers(type(api), predicate=inspect.isfunction)
                  if not n.startswith("_"))


def make_handler(api, user: str, secret: str, log_path: Path):
    from backend import activity_log
    allowed = set(public_methods(api))

    def log(line: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet: errors go to engine.log
            pass

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True, "user": user, "pid": os.getpid()})
            elif self.path == "/methods":
                self._send(200, {"methods": sorted(allowed)})
            else:
                self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if self.path != "/call":
                return self._send(404, {"ok": False, "error": "not found"})
            if not hmac.compare_digest(self.headers.get("X-Engine-Secret", ""), secret):
                return self._send(403, {"ok": False, "error": "forbidden"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"ok": False, "error": "bad request"})
            name, args = body.get("method", ""), body.get("args") or []
            if name not in allowed:
                return self._send(404, {"ok": False, "error": f"no such method: {name}"})
            # who and from which PC, for the activity log (the gateway sends them)
            activity_log.set_context(person=user, pc=self.headers.get("X-PC", ""),
                                     call_id=self.headers.get("X-Call-Id", ""))
            try:
                result = getattr(api, name)(*args)
                self._send(200, {"ok": True, "result": result})
            except Exception as e:      # the Api's own methods return error dicts; this is a crash
                log(f"{name} CRASHED: {e}\n{traceback.format_exc()}")
                self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})
            finally:
                activity_log.clear_context()

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--parent-pid", type=int, default=0, help="the gateway: end when it ends")
    args = ap.parse_args(argv)
    secret = os.environ.get("BUSINESSHUB_ENGINE_SECRET", "")
    if len(secret) < 8:
        print("BUSINESSHUB_ENGINE_SECRET missing - only the gateway may start an engine", file=sys.stderr)
        return 2
    if args.parent_pid:
        _end_with(args.parent_pid)
    from server import data_dir
    log_path = data_dir() / "users" / args.user / "engine.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    api = start_backend(args.user)
    from backend import activity_log
    activity_log.record(source="server", action="engine_ready", outcome="info", person=args.user, pc="",
                        summary=f"{args.user}'s engine is ready", message=f"pid {os.getpid()}")
    server = ExclusiveHTTPServer(("127.0.0.1", args.port), make_handler(api, args.user, secret, log_path))
    print(f"engine {args.user} on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
