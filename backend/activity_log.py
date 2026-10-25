"""Activity and problems log (2026-10-23, Waleed: "know what broke, what
happened before it, and whose fault it was").

One log for everything that happens, in `<data>/logs/activity.db`:

  source  'call'    every function a screen asks the app to run (Api methods)
          'screen'  what the person did and saw: screens opened, buttons,
                    messages shown, screen errors, "report a problem"
          'pc'      what the launcher did on the person's own PC (open / print
                    a PDF, file pickers) and network trouble reaching the server
          'server'  the server's own life: engines starting, stopping, lost
          'login'   logging in and out

Every row says WHOSE fault in one word (`outcome`):
  ok            it worked
  refused       the app said no on purpose (a rule or the input) - the person
                can fix it
  app_error     a mistake in the app (an exception, swallowed or not, or a
                screen error) - not the person's fault
  server_error  the server's machine: a file locked or missing, the disk, the
                database busy, a program (Chrome) that hung
  network       the PC could not reach the server, or the line broke
  warning       the person was shown an error message
  report        the person used "report a problem"
  info          worth knowing, not a problem

A swallowed exception - `except Exception as e: return {"ok": False, "error":
str(e)}`, the app's usual shape - is still caught: Python's sys.monitoring
reports every exception a call handles, and when the error the call returned
matches one, the row becomes app_error / server_error WITH its traceback
instead of a plain refusal.

Writing never slows or breaks the app: rows go to a background writer, and a
log that cannot be written is skipped, never raised.
"""
from __future__ import annotations

import json
import os
import queue
import re
import socket
import sqlite3
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

OUTCOMES = ("ok", "refused", "app_error", "server_error", "network", "warning", "report", "info")
PROBLEMS = ("app_error", "server_error", "network", "warning", "report")
SOURCES = ("call", "screen", "pc", "server", "login")
KEEP_ROUTINE_DAYS = 30          # plain successful reads and screen clicks
KEEP_ALL_DAYS = 400             # everything else
# Calls that are part of the log itself - logging them would log forever.
NOT_LOGGED = frozenset({"log_client_events", "report_problem", "activity_list", "activity_detail",
                        "activity_people", "activity_can_view"})
_READ_PREFIXES = ("get_", "list_", "load_", "check_", "search_", "find_", "peek_", "preview_",
                  "count_", "dm_list", "dm_find", "lookup", "suggest", "verify_", "is_", "has_",
                  "export_preview", "gate_brief", "lpo_page", "statement_preview")
_SECRETISH = re.compile(r"pass|secret|token|api_?key|pfx|private", re.I)
_REF_KEYS = ("inv_no", "quote_no", "dn_no", "po_no", "lpo_no", "doc_no", "no")

_lock = threading.Lock()
_path: Path | None = None
_writer: "_Writer | None" = None
_local = threading.local()


# ---------------------------------------------------------------- where
def configure(path: Path | str | None) -> None:
    """Point the log at a file (the gateway and the tests do); None = the
    app's data folder, found the same way as data/secrets.json."""
    global _path, _writer
    with _lock:
        if _writer is not None:
            _writer.stop()
            _writer = None
        _path = Path(path) if path else None


def log_path() -> Path:
    if _path is not None:
        return _path
    try:
        from . import profile_manager
        return profile_manager.shared_db_path().parent.parent / "logs" / "activity.db"
    except Exception:
        return Path(__file__).resolve().parents[1] / "data" / "logs" / "activity.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    ts       REAL    NOT NULL,
    day      TEXT    NOT NULL,
    person   TEXT    NOT NULL DEFAULT '',
    pc       TEXT    NOT NULL DEFAULT '',
    company  TEXT    NOT NULL DEFAULT '',
    source   TEXT    NOT NULL,
    action   TEXT    NOT NULL,
    summary  TEXT    NOT NULL DEFAULT '',
    outcome  TEXT    NOT NULL,
    message  TEXT    NOT NULL DEFAULT '',
    detail   TEXT    NOT NULL DEFAULT '',
    ms       INTEGER,
    ref      TEXT    NOT NULL DEFAULT '',
    call_id  TEXT    NOT NULL DEFAULT '',
    routine  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_person_ts ON events(person, ts);
CREATE INDEX IF NOT EXISTS ix_events_outcome_ts ON events(outcome, ts);
"""


def _connect(path: Path) -> sqlite3.Connection:
    from .sqlite_policy import open_db        # WAL + busy wait, like every other open
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(path, row_factory=False)
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------- writing
class _Writer:
    """Rows queue up and one background thread writes them in batches."""

    def __init__(self, path: Path):
        self.path = path
        self.q: queue.Queue = queue.Queue(maxsize=20000)
        self.wake = threading.Event()
        self.idle = threading.Event()
        self.idle.set()
        self.alive = True
        self.dropped = 0
        self.last_prune = 0.0
        self.thread = threading.Thread(target=self._run, name="activity-log", daemon=True)
        self.thread.start()

    def put(self, row: dict, urgent: bool = False) -> None:
        try:
            self.q.put_nowait(row)
            self.idle.clear()
        except queue.Full:
            self.dropped += 1
        if urgent:
            self.wake.set()

    def flush(self, timeout: float = 10.0) -> bool:
        self.wake.set()
        end = time.time() + timeout
        while time.time() < end:
            if self.q.empty() and self.idle.is_set():
                return True
            time.sleep(0.02)
        return False

    def stop(self) -> None:
        self.flush(5)
        self.alive = False
        self.wake.set()

    def _run(self) -> None:
        conn = None
        while self.alive:
            self.wake.wait(0.5)
            self.wake.clear()
            rows = []
            while len(rows) < 500:
                try:
                    rows.append(self.q.get_nowait())
                except queue.Empty:
                    break
            if rows:
                try:
                    if conn is None:
                        conn = _connect(self.path)
                    with conn:
                        conn.executemany(
                            "INSERT INTO events(ts, day, person, pc, company, source, action, summary,"
                            " outcome, message, detail, ms, ref, call_id, routine) VALUES"
                            " (:ts, :day, :person, :pc, :company, :source, :action, :summary,"
                            " :outcome, :message, :detail, :ms, :ref, :call_id, :routine)", rows)
                except Exception:
                    self.dropped += len(rows)          # never let the log break the app
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
            if self.q.empty():
                self.idle.set()
                if time.time() - self.last_prune > 6 * 3600:
                    self.last_prune = time.time()
                    try:
                        if conn is None:
                            conn = _connect(self.path)
                        prune(conn)
                    except Exception:
                        pass
        if conn is not None:
            conn.close()


def _writer_for() -> _Writer:
    global _writer
    with _lock:
        if _writer is None:
            _writer = _Writer(log_path())
        return _writer


def flush(timeout: float = 10.0) -> bool:
    """Wait until everything recorded so far is on disk (tests, shutdown)."""
    w = _writer
    return True if w is None else w.flush(timeout)


def prune(conn: sqlite3.Connection, now: float | None = None) -> int:
    now = time.time() if now is None else now
    with conn:
        a = conn.execute("DELETE FROM events WHERE routine = 1 AND outcome = 'ok' AND ts < ?",
                         (now - KEEP_ROUTINE_DAYS * 86400,)).rowcount
        b = conn.execute("DELETE FROM events WHERE ts < ?", (now - KEEP_ALL_DAYS * 86400,)).rowcount
    return a + b


# ---------------------------------------------------------------- who and where
def set_context(*, person: str | None = None, pc: str | None = None, call_id: str | None = None) -> None:
    """Who is calling and from which PC (the engine sets it per request)."""
    _local.person, _local.pc, _local.call_id = person, pc, call_id


def clear_context() -> None:
    _local.person = _local.pc = _local.call_id = None


def _person() -> str:
    return getattr(_local, "person", None) or os.environ.get("BUSINESSHUB_USER", "")


def _pc() -> str:
    pc = getattr(_local, "pc", None)
    if pc:
        return pc
    return "" if os.environ.get("BUSINESSHUB_USER") else socket.gethostname()


def _company() -> str:
    try:
        from . import config
        return str(getattr(config, "ACTIVE_PROFILE", "") or "")
    except Exception:
        return ""


def record(*, source: str, action: str, outcome: str, summary: str = "", message: str = "",
           detail: Any = None, ms: int | None = None, ref: str = "", person: str | None = None,
           pc: str | None = None, company: str | None = None, ts: float | None = None,
           call_id: str = "", routine: bool = False) -> None:
    """One row. Never raises."""
    try:
        ts = time.time() if ts is None else ts
        row = {
            "ts": ts, "day": datetime.fromtimestamp(ts).strftime("%Y-%m-%d"),
            "person": _clip(person if person is not None else _person(), 64),
            "pc": _clip(pc if pc is not None else _pc(), 120),
            "company": _clip(company if company is not None else _company(), 32),
            "source": source if source in SOURCES else "screen",
            "action": _clip(action, 120), "summary": _clip(summary, 300),
            "outcome": outcome if outcome in OUTCOMES else "info",
            "message": _clip(message, 2000),
            "detail": detail if isinstance(detail, str) else (_json(detail) if detail else ""),
            "ms": ms, "ref": _clip(ref, 80), "call_id": _clip(call_id or getattr(_local, "call_id", "") or "", 64),
            "routine": 1 if routine else 0,
        }
        _writer_for().put(row, urgent=outcome in PROBLEMS)
    except Exception:
        pass


# ---------------------------------------------------------------- calls
class _Call:
    __slots__ = ("name", "args", "t0", "t1", "handled", "outer")

    def __init__(self, name: str, args: tuple):
        self.name, self.args = name, args
        self.t0 = self.t1 = time.perf_counter()
        self.handled: deque = deque(maxlen=6)
        self.outer = True


def start_call(name: str, args: tuple) -> "_Call | None":
    """Before an Api method runs. Nested calls (one method calling another)
    belong to the outer one, and the log's own calls are not logged."""
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    if depth or name in NOT_LOGGED:
        return None
    call = _Call(name, args)
    _local.call = call
    return call


def started(call: "_Call | None") -> None:
    """The call got its turn (after waiting for any other call to finish)."""
    if call is not None:
        call.t1 = time.perf_counter()


def end_call(call: "_Call | None", result: Any = None, error: BaseException | None = None) -> None:
    _local.depth = max(0, getattr(_local, "depth", 1) - 1)
    if call is None:
        return
    _local.call = None
    try:
        _record_call(call, result, error)
    except Exception:
        pass


def _record_call(call: _Call, result: Any, error: BaseException | None) -> None:
    done = time.perf_counter()
    ms, wait_ms = int((done - call.t0) * 1000), int((call.t1 - call.t0) * 1000)
    name, args = call.name, call.args
    detail: dict = {}
    message = ""
    if error is not None:
        outcome = classify_exception(error)
        message = f"{type(error).__name__}: {error}"
        detail.update(exception=type(error).__name__, traceback=_trace(error), crashed=True)
    elif isinstance(result, dict) and result.get("ok") is False:
        message = str(result.get("error") or result.get("message") or "")
        seen = _matching(call.handled, message)
        if seen is not None:
            outcome = classify_type(seen.cls)
            detail.update(exception=seen.cls.__name__, traceback=seen.lines()[-24:], swallowed=True)
        else:
            outcome = "refused"
        detail["result"] = summarize(result)
    else:
        outcome = "ok"
        if isinstance(result, dict) and result.get("warning"):
            message = str(result.get("warning"))
    read = name.startswith(_READ_PREFIXES)
    if outcome != "ok" or not read:
        detail["args"] = summarize(list(args))
    if wait_ms >= 200:
        detail["waited_ms"] = wait_ms
    record(source="call", action=name, outcome=outcome, summary=describe(name, args, result),
           message=message, detail=detail, ms=ms, ref=ref_of(args, result),
           routine=read and outcome == "ok")


def classify_exception(exc: BaseException) -> str:
    """server_error when the machine let us down, app_error when the code did."""
    return classify_type(type(exc))


def classify_type(cls: type) -> str:
    import subprocess
    if issubclass(cls, (OSError, MemoryError, TimeoutError, subprocess.TimeoutExpired)):
        return "server_error"
    if issubclass(cls, sqlite3.OperationalError):
        return "server_error"            # database locked / disk I/O / no space
    return "app_error"


def _matching(handled, message: str):
    if not message:
        return None
    for seen in reversed(handled):
        text = seen.text
        if (text and text in message) or (seen.cls.__name__ in message) or (message in text and len(message) > 8):
            return seen
    return None


def _trace(exc: BaseException) -> list[str]:
    lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return [ln.rstrip("\n") for ln in lines][-24:]


# ---------------------------------------------------------------- swallowed exceptions
_TOOL_ID = 4
_watching = False
_NOT_ERRORS = (StopIteration, StopAsyncIteration, GeneratorExit)   # how Python ends loops, not problems


class _Seen:
    """A caught exception, copied out on the spot: its type, its text and its
    stack WITHOUT the frames. Never hold the exception itself: its traceback
    keeps every frame alive, and Python frees some resources only when a frame
    goes - subprocess closes a pipe end that way, so holding one exception made
    Chrome's PDF render wait 60 s for an end of output that never came
    (2026-10-23, tests/activity_log.test.py)."""
    __slots__ = ("cls", "text", "stack")

    def __init__(self, exc: BaseException):
        self.cls = type(exc)
        try:
            self.text = str(exc)
        except Exception:
            self.text = ""
        self.stack = traceback.StackSummary.extract(traceback.walk_tb(exc.__traceback__), limit=20,
                                                    lookup_lines=False)

    def lines(self) -> list[str]:
        return (["Traceback (most recent call last):"]
                + [ln.rstrip("\n") for ln in self.stack.format()]
                + [f"{self.cls.__name__}: {self.text}"])


def _on_handled(code, offset, exc):
    call = getattr(_local, "call", None)
    if call is None or isinstance(exc, _NOT_ERRORS):
        return
    try:
        call.handled.append(_Seen(exc))
    except Exception:
        pass


def watch_exceptions() -> bool:
    """Ask Python to tell us about every exception a call catches (3.12+)."""
    global _watching
    if _watching:
        return True
    mon = getattr(sys, "monitoring", None)
    if mon is None:
        return False
    try:
        mon.use_tool_id(_TOOL_ID, "business-hub-activity-log")
    except ValueError:
        return False
    mon.register_callback(_TOOL_ID, mon.events.EXCEPTION_HANDLED, _on_handled)
    mon.set_events(_TOOL_ID, mon.events.EXCEPTION_HANDLED)
    _watching = True
    return True


# ---------------------------------------------------------------- what the screens and PCs send
_CLIENT_KINDS = {"view", "click", "toast", "js_error", "report", "pc", "network"}


def client_events(events: Any, source: str = "screen") -> int:
    """Rows sent by a screen or a launcher, checked and trimmed. Each event:
    {"kind", "t" (ms, the sender's clock), "action", "summary", "message",
    "outcome", "detail", "ref"}; "sent" on the batch keeps the order right
    on the server's clock."""
    if not isinstance(events, dict) or not isinstance(events.get("events"), list):
        return 0
    now = time.time()
    sent = _num(events.get("sent"))
    n = 0
    for ev in events["events"][:300]:
        if not isinstance(ev, dict) or ev.get("kind") not in _CLIENT_KINDS:
            continue
        kind = ev["kind"]
        t = _num(ev.get("t"))
        ts = now - max(0.0, (sent - t) / 1000.0) if sent and t else now
        outcome = ev.get("outcome")
        if outcome not in OUTCOMES:
            outcome = {"js_error": "app_error", "report": "report", "network": "network"}.get(kind, "info")
        src = "pc" if kind in ("pc", "network") else source
        record(source=src, action=_clip(str(ev.get("action") or kind), 120), outcome=outcome,
               summary=_clip(str(ev.get("summary") or ""), 300), message=_clip(str(ev.get("message") or ""), 2000),
               detail=summarize(ev.get("detail")) if ev.get("detail") is not None else None,
               ref=_clip(str(ev.get("ref") or ""), 80), ts=ts,
               routine=kind in ("view", "click") or (kind == "toast" and outcome == "info"))
        n += 1
    return n


# ---------------------------------------------------------------- reading it back
def _read() -> sqlite3.Connection:
    flush(5)
    conn = _connect(log_path())
    conn.row_factory = sqlite3.Row
    return conn


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["time"] = datetime.fromtimestamp(d["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    return d


def list_events(filters: dict | None = None) -> dict:
    """Newest first. filters: person, outcomes [..], sources [..], q, since,
    until (unix), problems_only, include_routine, limit, before_id."""
    f = filters or {}
    where, params = ["1=1"], []
    if f.get("person"):
        where.append("person = ?")
        params.append(str(f["person"]))
    outs = [o for o in (f.get("outcomes") or []) if o in OUTCOMES]
    if f.get("problems_only"):
        outs = list(PROBLEMS)
    if outs:
        where.append(f"outcome IN ({','.join('?' * len(outs))})")
        params += outs
    srcs = [s for s in (f.get("sources") or []) if s in SOURCES]
    if srcs:
        where.append(f"source IN ({','.join('?' * len(srcs))})")
        params += srcs
    if not f.get("include_routine"):
        where.append("routine = 0")
    if f.get("since"):
        where.append("ts >= ?")
        params.append(float(f["since"]))
    if f.get("until"):
        where.append("ts < ?")
        params.append(float(f["until"]))
    if f.get("before_id"):
        where.append("id < ?")
        params.append(int(f["before_id"]))
    if f.get("q"):
        like = "%" + str(f["q"]).strip().replace("%", "").replace("_", "") + "%"
        where.append("(summary LIKE ? OR message LIKE ? OR ref LIKE ? OR action LIKE ? OR person LIKE ?)")
        params += [like] * 5
    limit = max(1, min(int(f.get("limit") or 200), 1000))
    conn = _read()
    try:
        rows = conn.execute(f"SELECT id, ts, person, pc, company, source, action, summary, outcome,"
                            f" message, ms, ref FROM events WHERE {' AND '.join(where)}"
                            f" ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
        counts = dict(conn.execute(f"SELECT outcome, COUNT(*) FROM events WHERE {' AND '.join(where)}"
                                   f" GROUP BY outcome", params).fetchall())
    finally:
        conn.close()
    return {"ok": True, "rows": [_row(r) for r in rows], "counts": counts,
            "more": len(rows) == limit}


def event_detail(event_id: int, before_s: float = 300, after_s: float = 30) -> dict:
    """One row in full, and what the same person did around it - every source,
    routine steps included - oldest first."""
    conn = _read()
    try:
        r = conn.execute("SELECT * FROM events WHERE id = ?", (int(event_id),)).fetchone()
        if r is None:
            return {"ok": False, "error": "That entry is no longer in the log."}
        ev = _row(r)
        trail = conn.execute(
            "SELECT id, ts, source, action, summary, outcome, message, ms, ref FROM events"
            " WHERE person = ? AND pc = ? AND ts BETWEEN ? AND ? ORDER BY ts, id LIMIT 120",
            (ev["person"], ev["pc"], ev["ts"] - before_s, ev["ts"] + after_s)).fetchall()
    finally:
        conn.close()
    try:
        ev["detail"] = json.loads(ev["detail"]) if ev["detail"] else {}
    except ValueError:
        pass
    return {"ok": True, "event": ev, "trail": [_row(t) for t in trail]}


def people() -> list[str]:
    conn = _read()
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT person FROM events ORDER BY person")]
    finally:
        conn.close()


# ---------------------------------------------------------------- words
_DOC_WORDS = {"inv": "invoice", "quote": "quotation", "dn": "delivery note", "po": "purchase order",
              "lpo": "client LPO", "qut": "quotation"}
_TEMPLATES = {
    "save_inv": "Save invoice {ref}", "save_quote": "Save quotation {ref}", "save_dn": "Save delivery note {ref}",
    "save_po": "Save purchase order {ref}", "save_lpo": "Save client LPO {ref}",
    "load_inv": "Open invoice {a0}", "load_quote": "Open quotation {a0}", "load_dn": "Open delivery note {a0}",
    "load_po": "Open purchase order {a0}", "load_lpo": "Open client LPO {a0}",
    "preview_inv": "Preview invoice {ref}", "preview_quote": "Preview quotation {ref}",
    "preview_dn": "Preview delivery note {ref}", "preview_pdf": "Preview purchase order {a0}",
    "switch_profile": "Switch company to {a0}", "make_client_statement": "Make the monthly statement for {a0}",
    "attach_doc_signed_copy_data": "Attach the client's signed copy to {a1}",
    "remove_doc_signed_copy": "Remove the signed copy from {a1}",
    "save_settings": "Change settings", "save_doc_settings": "Change {a0} document settings",
    "dm_delete_record": "Delete {a1} ({a0})", "dm_delete_records": "Delete records ({a0})",
    "soft_delete_file": "Move a file to the recycle folder", "verify_document": "Check document {a0}",
    "bulk_parse_spreadsheet": "Read a spreadsheet", "save_doc_costs": "Save costs of {ref}",
}
_ACRONYMS = {"po": "PO", "dn": "DN", "inv": "invoice", "lpo": "LPO", "pdf": "PDF", "dm": "data manager",
             "qut": "quotation", "id": "ID", "ar": "Arabic", "en": "English"}


def describe(name: str, args: tuple | list = (), result: Any = None) -> str:
    """The action in plain words: 'Save invoice IV-0003'."""
    args = list(args or ())
    ref = ref_of(args, result)
    a0 = _short(args[0]) if len(args) > 0 else ""
    a1 = _short(args[1]) if len(args) > 1 else ""
    tpl = _TEMPLATES.get(name)
    if tpl:
        text = tpl.format(ref=ref, a0=a0, a1=a1)
    else:
        words = [_ACRONYMS.get(w, w) for w in name.split("_") if w]
        text = " ".join(words)
        text = text[:1].upper() + text[1:]
    return re.sub(r"\s+", " ", text).replace(" ()", "").strip()


def ref_of(args: Any, result: Any = None) -> str:
    """A document number found in the result or the arguments."""
    for src in (result, *(args if isinstance(args, (list, tuple)) else [])):
        if isinstance(src, dict):
            for k in _REF_KEYS:
                v = src.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:80]
            saved = src.get("saved")
            if isinstance(saved, list) and saved and isinstance(saved[0], dict):
                v = saved[0].get("po_no")
                if isinstance(v, str) and v:
                    return v[:80]
    return ""


def summarize(value: Any, depth: int = 0) -> Any:
    """Arguments and results, trimmed: no file contents, no secrets, no walls of text."""
    if isinstance(value, str):
        if value.startswith("data:"):
            return f"<file {len(value) * 3 // 4 // 1024} KB>"
        return value if len(value) <= 160 else value[:157] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        if depth >= 3:
            return f"<{len(value)} fields>"
        out = {}
        for k, v in list(value.items())[:30]:
            out[str(k)] = "<hidden>" if _SECRETISH.search(str(k)) else summarize(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if depth >= 3:
            return f"<{len(value)} items>"
        items = [summarize(v, depth + 1) for v in list(value)[:8]]
        if len(value) > 8:
            items.append(f"<and {len(value) - 8} more>")
        return items
    return f"<{type(value).__name__}>"


def _short(v: Any) -> str:
    if isinstance(v, str):
        return v[:60]
    if isinstance(v, (int, float)):
        return str(v)
    return ""


def _json(v: Any) -> str:
    try:
        text = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps(str(v))
    return text if len(text) <= 12000 else text[:12000]


def _clip(s: Any, n: int) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
