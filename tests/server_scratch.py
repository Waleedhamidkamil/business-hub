"""Scratch data + process helpers for the office-server tests (not a test itself).

Builds a throwaway data folder under .test-work/<name>: both companies'
schemas, documents folders inside the scratch area, and one client on file.
Never touches the real data.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEED = r'''
import importlib, json, sys
from pathlib import Path
root, data = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(root))
from backend import profile_manager
from backend.profile_manager import ProfileManager
m = ProfileManager(data, root / "static")
profile_manager._MANAGER = m
# a real install has run the one-time profile setup (and left its marker)
boot = m.bootstrap_profiles(legacy_db=data / "none.db", legacy_settings=data / "none.json",
                            legacy_assets=root / "static")
assert boot.get("ok"), boot
for profile in ("eshraqat", "abraj"):
    m.activate(profile)
    s = m.settings_path(profile)
    s.parent.mkdir(parents=True, exist_ok=True)
    s.write_text(json.dumps({"documents_root": str(data.parent / ("docs-" + profile))}), encoding="utf-8")
    from backend import config, database, clients
    importlib.reload(config); importlib.reload(database)
    database.init_db()
    clients.add_client("Server Test Co")
'''


def remove_tree(path: Path) -> None:
    """rmtree that clears Windows read-only flags (the profile setup copies an
    assets/sigs folder that comes out read-only, and a plain rmtree stops on it)."""
    import stat

    def unlock(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=unlock)


def scratch(name: str) -> Path:
    """A fresh scratch data folder; returns its path."""
    work = ROOT / ".test-work" / name
    remove_tree(work)
    data = work / "data"
    data.mkdir(parents=True)
    r = subprocess.run([sys.executable, "-c", SEED, str(ROOT), str(data)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    return data


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"nothing answered at {url}")


def post(url: str, body: dict, headers: dict | None = None, timeout: float = 120):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def env_for(data: Path, **extra: str) -> dict:
    env = dict(os.environ)
    env["BUSINESSHUB_DATA"] = str(data)
    env.update(extra)
    return env
