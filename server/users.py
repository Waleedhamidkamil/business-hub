"""Who may log in to the office server (Phase 2, 2026-10-22).

`data/users/users.json` holds, per person: display name, role, enabled, and a
PBKDF2-SHA256 hash of the password with that person's own salt - never the
password. Names are case- and space-insensitive and must be safe folder names
(each person gets `data/users/<name>/`).

    python -m server.users add waleed --role admin --display "Waleed"
    python -m server.users passwd waleed
    python -m server.users disable ahmed
    python -m server.users list
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
from datetime import datetime
from pathlib import Path

ITERATIONS = 200_000
MIN_PASSWORD = 6
ROLES = ("admin", "accountant", "procurement")
_RESERVED = {"con", "prn", "aux", "nul", "com1", "com2", "com3", "lpt1", "lpt2", "sessions"}
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


def _norm(name: str) -> str:
    return str(name or "").strip().lower()


def _hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt_hex), ITERATIONS).hex()


class UserStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def check_new(self, name: str, role: str) -> str:
        """The account name `add` would use; raises ValueError (before anyone
        types a password) if the name, role or an existing account forbids it."""
        key = _norm(name)
        if not _NAME.match(key) or key in _RESERVED:
            raise ValueError(f"'{name}' is not a usable name (letters, digits, . _ - only)")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")
        if key in self._load()["users"]:
            raise ValueError(f"'{key}' already exists")
        return key

    def add(self, name: str, password: str, *, role: str = "admin", display: str = "") -> None:
        key = self.check_new(name, role)
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        with self._lock:
            data = self._load()
            if key in data["users"]:
                raise ValueError(f"'{key}' already exists")
            salt = secrets.token_hex(16)
            data["users"][key] = {"display": display or name.strip(), "role": role,
                                  "enabled": True, "salt": salt, "hash": _hash(password, salt),
                                  "created_at": datetime.now().isoformat(timespec="seconds")}
            self._save(data)

    def set_password(self, name: str, password: str) -> None:
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        with self._lock:
            data = self._load()
            rec = data["users"][_norm(name)]
            rec["salt"] = secrets.token_hex(16)
            rec["hash"] = _hash(password, rec["salt"])
            self._save(data)

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            data = self._load()
            data["users"][_norm(name)]["enabled"] = bool(enabled)
            self._save(data)

    def verify(self, name: str, password: str) -> dict | None:
        """The person's public record if the password is right and they are
        enabled; None otherwise (never says which part was wrong)."""
        key = _norm(name)
        rec = self._load()["users"].get(key)
        if not rec or not rec.get("enabled", True):
            _hash(password or "", "00" * 16)          # same time either way
            return None
        if not hmac.compare_digest(_hash(password or "", rec["salt"]), rec["hash"]):
            return None
        return {"user": key, "display": rec.get("display", key), "role": rec.get("role", "")}

    def get(self, name: str) -> dict | None:
        rec = self._load()["users"].get(_norm(name))
        if not rec:
            return None
        return {"user": _norm(name), "display": rec.get("display"), "role": rec.get("role"),
                "enabled": rec.get("enabled", True)}

    def list(self) -> list[dict]:
        return [self.get(k) for k in sorted(self._load()["users"])]

    def count_enabled_admins(self) -> int:
        return sum(1 for rec in self._load()["users"].values()
                   if rec.get("enabled", True) and rec.get("role") == "admin")


def default_store() -> UserStore:
    from . import data_dir
    return UserStore(data_dir() / "users" / "users.json")


def _cli(argv: list[str]) -> int:
    store = default_store()
    if not argv or argv[0] not in ("add", "passwd", "disable", "enable", "list"):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "list":
        for u in store.list():
            print(f"  {u['user']:16s} {u['role']:12s} {'enabled' if u['enabled'] else 'DISABLED':9s} {u['display']}")
        return 0
    name = argv[1]
    if cmd in ("disable", "enable"):
        store.set_enabled(name, cmd == "enable")
        print(f"{name}: {cmd}d")
        return 0
    role = argv[argv.index("--role") + 1] if "--role" in argv else "admin"
    display = argv[argv.index("--display") + 1] if "--display" in argv else name
    if cmd == "add":
        store.check_new(name, role)            # a bad name is said BEFORE anyone types a password
    elif store.get(name) is None:
        raise KeyError(name)
    for _ in range(3):
        pw = getpass.getpass(f"Password for {name}: ")
        if len(pw) < MIN_PASSWORD:
            print(f"Too short - at least {MIN_PASSWORD} characters. Try again.")
        elif pw != getpass.getpass("Again: "):
            print("The two passwords differ. Try again.")
        else:
            break
    else:
        print("Nothing changed.")
        return 1
    if cmd == "passwd":
        store.set_password(name, pw)
        print(f"{name}: password changed")
        return 0
    store.add(name, pw, role=role, display=display)
    print(f"{name}: added as {role}")
    return 0


def main(argv: list[str]) -> int:
    try:
        return _cli(argv)
    except ValueError as e:            # a name, role or password the store refused
        print(f"Not done: {e}")
        return 1
    except KeyError:
        print(f"Not done: there is no '{argv[1] if len(argv) > 1 else ''}' account.")
        return 1
    except IndexError:
        print(__doc__)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
