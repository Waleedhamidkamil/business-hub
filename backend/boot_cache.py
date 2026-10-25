"""Have the dashboard's data ready before the page asks for it (2026-10-02).

Measured on Waleed's machine: from the moment the page started its boot to a
painted dashboard took 1.2 s, and almost all of it was the page WAITING for
the bridge - nine read calls queued one behind another on the profile lock,
the first of them (Needs Attention, cold) alone 0.47 s. Meanwhile, for the
1.4 s before that, while WebView2 was starting, the Python side sat idle.

So: as soon as the company data is open, `warm()` runs those same calls on a
background thread, in the order the page will ask for them, and keeps each
answer. When the page then asks, `Api` hands the kept answer straight back.

What keeps this from ever serving something stale
--------------------------------------------------
* Every answer is stamped with the data's fingerprint at the moment it was
  computed: both databases' commit counters (`db_version`), the settings
  file's mtime and size, the active company, and today's date. An answer is
  handed out only while the fingerprint is UNCHANGED - any save, by this app
  or another, invalidates everything.
* Answers expire `TTL_S` seconds after warming finishes; this exists for the
  start-up only, not as a general cache.
* Only calls that READ are warmed (`CALLS` below). Nothing that writes,
  generates a file or depends on anything but the stamped data is ever here.
* After warming is done and the answers are gone or expired, `take()` is a
  dict lookup on an empty dict - zero cost for the rest of the session.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date

from . import config, db_version, profile_manager

TTL_S = 20.0

_lock = threading.Lock()
_answers: dict[str, tuple[str, object]] = {}
_expires_at: float | None = None
_ready = threading.Event()
_asked: set[str] = set()      # calls the page already made itself - never warm those
_WARM_THREAD = "boot-cache"


def calls_for(today: date) -> list[tuple[str, tuple]]:
    """What the page asks for while it boots, in the order it asks.

    Keep in step with `bootstrap()` / `initCompanyProfile()` /
    `renderDashboard()` in static/app.js - an entry here the page never asks
    for is wasted work, never a wrong answer."""
    y, m = today.year, today.month
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    return [
        ("get_active_profile", ()),
        ("get_settings", ()),
        ("dashboard_needs_attention", ()),
        ("list_quotes_for_month", (y, m)),
        ("list_invoices_for_month", (y, m)),
        ("list_dns_for_month", (y, m)),
        ("list_pos_for_month", (y, m)),
        ("dashboard_analytics", (3,)),
        ("costing_summary", (y, m)),
        # buildDashboardSearchIndex: last month too, right after the paint -
        # 0.4 s of work that would otherwise compete with the doors opening.
        *[(n, (py, pm)) for n in ("list_quotes_for_month", "list_invoices_for_month",
                                  "list_dns_for_month", "list_pos_for_month")],
    ]


def _key(name: str, args: tuple) -> str:
    return name + json.dumps(list(args), separators=(",", ":"))


def fingerprint() -> str:
    parts = [str(config.ACTIVE_PROFILE), date.today().isoformat()]
    for path in (config.DB_PATH, profile_manager.shared_db_path()):
        v = db_version.version(path)
        parts.append("?" if v is None else str(v))
    try:
        st = os.stat(config.SETTINGS_PATH)
        parts.append(f"{st.st_mtime_ns}:{st.st_size}")
    except OSError:
        parts.append("no-settings")
    return "|".join(parts)


def warm(api, today: date | None = None, on_answer=None) -> None:
    """Run the boot calls now and keep their answers. Call on a thread.

    `on_answer(name, result)` lets the caller reuse an answer as it lands
    (the launch gate's brief is built from Needs Attention this way)."""
    global _expires_at
    try:
        for name, args in calls_for(today or date.today()):
            if _key(name, args) in _asked:
                continue                       # the page got there first; don't do it twice
            stamp = fingerprint()              # BEFORE the work it vouches for
            try:
                result = getattr(api, name)(*args)
            except Exception:
                continue                       # the page will simply ask for real
            if isinstance(result, dict) and result.get("ok") is False:
                continue                       # never keep a failure
            with _lock:
                _answers[_key(name, args)] = (stamp, result)
            if on_answer:
                try:
                    on_answer(name, result)
                except Exception:
                    pass
    finally:
        with _lock:
            _expires_at = time.monotonic() + TTL_S
        _ready.set()


def take(name: str, args: tuple):
    """The kept answer for this call, or None. Each answer is used once."""
    if _ready.is_set() and not _answers:
        return None                            # warming over: nothing to do, ever again
    key = _key(name, tuple(args))
    if threading.current_thread().name != _WARM_THREAD:
        _asked.add(key)
    with _lock:
        hit = _answers.pop(key, None)
        expired = _expires_at is not None and time.monotonic() > _expires_at
        if expired:
            _answers.clear()
    if hit is None or expired:
        return None
    stamp, result = hit
    if stamp != fingerprint():
        return None
    return result


def start(api, today: date | None = None, on_answer=None) -> threading.Thread:
    t = threading.Thread(target=warm, args=(api, today, on_answer), name=_WARM_THREAD, daemon=True)
    t.start()
    return t


def reset() -> None:
    """Forget everything (tests, and a company switch)."""
    global _expires_at
    with _lock:
        _answers.clear()
        _expires_at = None
    _asked.clear()
    _ready.clear()
