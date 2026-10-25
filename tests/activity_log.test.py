"""The activity and problems log (2026-10-23): backend/activity_log.py.

What must hold:
  * watching the exceptions a call catches never keeps them alive: a held
    exception keeps its frames, and subprocess closes a pipe end only when its
    frame goes - Chrome's PDF render then "hung" for 60 s (2026-10-23, found
    when every preview through an engine timed out);
  * an exception a method catches and turns into {"ok": False, "error": ...}
    is logged as app_error WITH its traceback; a deliberate "no" is refused;
    a machine failure (a file, the database) is server_error;
  * a call that crashes is logged and still raises; nested calls belong to
    the outer one; the log's own calls are not logged;
  * arguments are trimmed: no file contents, no secrets;
  * what screens and PCs send is stored in their own order on the server's
    clock; the trail around an entry shows what that person did before it;
  * routine rows are kept 30 days, everything else 400.
Scratch log file only.
"""
import json
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server_scratch as S  # noqa: E402

sys.path.insert(0, str(S.ROOT))
from backend import activity_log as A  # noqa: E402

CHILD = r'''
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from backend import activity_log as A
A.configure(sys.argv[2])
assert A.watch_exceptions()
call = A.start_call("preview_inv", ())
t0 = time.time()
r = subprocess.run(["cmd", "/c", "echo hi"], capture_output=True, text=True, timeout=20)
A.end_call(call, {"ok": True})
print("done", r.stdout.strip(), round(time.time() - t0, 1))
'''


class WatchingNeverHoldsExceptions(unittest.TestCase):
    def test_a_captured_program_finishes_while_a_call_is_watched(self):
        work = S.ROOT / ".test-work" / "activity-child"
        S.remove_tree(work)
        work.mkdir(parents=True)
        try:
            r = subprocess.run([sys.executable, "-c", CHILD, str(S.ROOT), str(work / "a.db")],
                               capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            self.fail("a program run inside a watched call never finished - the watch kept its pipe open")
        self.assertIn("done hi", r.stdout, r.stderr[-2000:])
        self.assertLess(float(r.stdout.split()[-1]), 10)

    def test_a_function_that_caught_an_exception_still_lets_go_of_its_things(self):
        A.configure(S.ROOT / ".test-work" / "activity-child" / "b.db")
        self.assertTrue(A.watch_exceptions())

        class Sentinel:              # stands in for a pipe, a file, a lock
            freed = False

            def __del__(self):
                Sentinel.freed = True

        def work():
            thing = Sentinel()       # noqa: F841 - released when work() returns
            try:
                raise ValueError("caught inside work")
            except ValueError:
                pass

        call = A.start_call("preview_inv", ())
        try:
            work()
            self.assertTrue(Sentinel.freed, "the log kept the caught exception, and with it work()'s things")
        finally:
            A.end_call(call, {"ok": True})


class Recording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = S.ROOT / ".test-work" / "activity-log"
        S.remove_tree(cls.work)
        A.configure(cls.work / "logs" / "activity.db")
        A.watch_exceptions()

    def rows(self, **where):
        A.flush()
        conn = sqlite3.connect(self.work / "logs" / "activity.db")
        conn.row_factory = sqlite3.Row
        try:
            sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(f"{k} = ?" for k in where) if where else "")
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", list(where.values()))]
        finally:
            conn.close()

    def run_call(self, name, fn, *args):
        call = A.start_call(name, args)
        result, error = None, None
        try:
            result = fn(*args)
            return result
        except BaseException as e:
            error = e
            raise
        finally:
            A.end_call(call, result, error)

    # ---------------------------------------------------------------- whose fault
    def test_a_swallowed_bug_is_an_app_error_with_its_traceback(self):
        def save_thing(doc):
            try:
                return {"ok": True, "total": doc["qty"] * doc["rate"]}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        self.run_call("save_thing_bug", save_thing, {"qty": 2})
        row = self.rows(action="save_thing_bug")[-1]
        self.assertEqual(row["outcome"], "app_error")
        detail = json.loads(row["detail"])
        self.assertEqual(detail["exception"], "KeyError")
        self.assertTrue(detail.get("swallowed"))
        self.assertIn("save_thing", "\n".join(detail["traceback"]))

    def test_a_deliberate_no_is_refused(self):
        def save_thing(doc):
            if not doc.get("client"):
                return {"ok": False, "error": "Pick a client first."}
            return {"ok": True}

        self.run_call("save_thing_rule", save_thing, {})
        row = self.rows(action="save_thing_rule")[-1]
        self.assertEqual((row["outcome"], row["message"]), ("refused", "Pick a client first."))
        self.assertNotIn("traceback", json.loads(row["detail"]))

    def test_a_handled_exception_that_is_not_the_reason_is_not_blamed(self):
        def save_thing(doc):
            try:
                int("not a number")
            except ValueError:
                pass
            return {"ok": False, "error": "Pick a client first."}

        self.run_call("save_thing_unrelated", save_thing, {})
        self.assertEqual(self.rows(action="save_thing_unrelated")[-1]["outcome"], "refused")

    def test_the_machine_failing_is_a_server_error(self):
        def open_file(path):
            try:
                return {"ok": True, "size": Path(path).stat().st_size}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        self.run_call("open_file_missing", open_file, str(self.work / "no-such.pdf"))
        self.assertEqual(self.rows(action="open_file_missing")[-1]["outcome"], "server_error")
        locked = sqlite3.OperationalError("database is locked")
        self.assertEqual(A.classify_exception(locked), "server_error")
        self.assertEqual(A.classify_exception(TypeError("x")), "app_error")

    def test_a_crash_is_logged_and_still_raised(self):
        def boom():
            return 1 / 0

        with self.assertRaises(ZeroDivisionError):
            self.run_call("boom_call", boom)
        row = self.rows(action="boom_call")[-1]
        self.assertEqual(row["outcome"], "app_error")
        self.assertTrue(json.loads(row["detail"])["crashed"])

    def test_nested_calls_belong_to_the_outer_one_and_the_log_skips_itself(self):
        def inner():
            return {"ok": True}

        def outer():
            return self.run_call("nested_inner", inner)

        self.run_call("nested_outer", outer)
        self.run_call("log_client_events", lambda: {"ok": True})
        self.assertEqual(len(self.rows(action="nested_outer")), 1)
        self.assertEqual(self.rows(action="nested_inner"), [])
        self.assertEqual(self.rows(action="log_client_events"), [])

    def test_arguments_are_trimmed(self):
        payload = {"inv_no": "IV-0009", "api_key": "sk-secret", "scan": "data:application/pdf;base64," + "A" * 40000,
                   "note": "x" * 500}
        self.run_call("save_trim", lambda d: {"ok": True}, payload)
        row = self.rows(action="save_trim")[-1]
        self.assertEqual(row["ref"], "IV-0009")
        text = row["detail"]
        self.assertNotIn("sk-secret", text)
        self.assertNotIn("AAAAAAAAAA", text)
        self.assertLess(len(text), 1200)

    # ---------------------------------------------------------------- screens, PCs, the trail
    def test_screen_events_keep_their_order_and_the_trail_shows_what_came_before(self):
        A.set_context(person="trailer", pc="DESK-1 (10.0.0.9)")
        try:
            now_ms = time.time() * 1000
            n = A.client_events({"sent": now_ms, "events": [
                {"kind": "view", "t": now_ms - 3000, "action": "view:invView", "summary": "Opened Invoice editor"},
                {"kind": "click", "t": now_ms - 2000, "action": "click", "summary": "Save"},
                {"kind": "toast", "t": now_ms - 1000, "outcome": "warning", "message": "Pick a client first."},
                {"kind": "js_error", "t": now_ms, "message": "TypeError: x is undefined",
                 "detail": {"stack": "at save (inv.js:10)"}},
                {"kind": "bogus", "t": now_ms},
            ]})
        finally:
            A.clear_context()
        self.assertEqual(n, 4)
        rows = self.rows(person="trailer")
        self.assertEqual([r["action"] for r in rows], ["view:invView", "click", "toast", "js_error"])
        self.assertTrue(all(a["ts"] <= b["ts"] for a, b in zip(rows, rows[1:])))
        # placed at the moments they happened (on the server's clock), not all at "now"
        self.assertAlmostEqual(rows[-1]["ts"] - rows[0]["ts"], 3.0, delta=0.5)
        self.assertEqual(rows[-1]["outcome"], "app_error")
        detail = A.event_detail(rows[-1]["id"])
        self.assertEqual([t["action"] for t in detail["trail"]], ["view:invView", "click", "toast", "js_error"])
        listed = A.list_events({"person": "trailer", "problems_only": True})
        self.assertEqual({r["outcome"] for r in listed["rows"]}, {"warning", "app_error"})

    def test_old_routine_rows_go_first(self):
        conn = sqlite3.connect(self.work / "logs" / "activity.db")
        try:
            day = 86400
            conn.executemany("INSERT INTO events(ts, day, source, action, outcome, routine) VALUES (?,?,?,?,?,?)",
                             [(time.time() - 40 * day, "old", "call", "prune_read", "ok", 1),
                              (time.time() - 40 * day, "old", "call", "prune_problem", "app_error", 0),
                              (time.time() - 500 * day, "old", "call", "prune_ancient", "app_error", 0)])
            conn.commit()
            A.prune(conn)
            left = {r[0] for r in conn.execute("SELECT action FROM events WHERE action LIKE 'prune_%'")}
        finally:
            conn.close()
        self.assertEqual(left, {"prune_problem"})

    def test_plain_words(self):
        self.assertEqual(A.describe("save_inv", ({},), {"ok": True, "inv_no": "IV-0003"}), "Save invoice IV-0003")
        self.assertEqual(A.describe("switch_profile", ("eshraqat",)), "Switch company to eshraqat")
        self.assertEqual(A.describe("get_next_po_number", ()), "Get next PO number")


if __name__ == "__main__":
    unittest.main(verbosity=2)
