"""The gate child must exit cleanly (2026-10-07).

Since the gate was built (2026-10-02) Windows logged 25 crashes of a pythonw
that lived ~5 seconds - "Visual Studio Just-In-Time Debugger: an unhandled
win32 exception occurred in pythonw.exe" on Waleed's screen after start-up.
That process is the gate child (`launch_gate.py --child`). Its daemon `_reader`
thread sits in `sys.stdin.buffer` waiting for the app's next message, and the
app keeps the pipe open. When the doors have parted and the child returns,
interpreter shutdown tries to close stdin, cannot take the lock the reader is
holding, and aborts:

    Fatal Python error: _enter_buffered_busy: could not acquire lock for
    <_io.BufferedReader name='<stdin>'> at interpreter shutdown, possibly due
    to daemon threads

Reproduced 3/3 with the pipe left open, 0/3 with it closed first. This drives
the child exactly as the app does - pipe left OPEN - and requires exit code 0.

2026-10-20, the launch card: the child swallows its own errors on purpose (a
broken card must never take the app down), which also makes them invisible.
So each run must leave no gate-error.txt behind, and its timeline must show
the card worked out its own rows (`card_data`) with none failing.

Shows the launch card on screen for about two seconds.
"""
import os
import ctypes
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


class GateChildExit(unittest.TestCase):

    def run_child(self):
        # Inherited by the child: a crash ends the process instead of putting
        # a debugger dialog on the screen, so the test can read its exit code.
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        p = subprocess.Popen([sys.executable, str(APP / "backend" / "launch_gate.py"),
                              "--child", str(APP)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, creationflags=0x08000000)

        def send(**m):
            p.stdin.write((json.dumps(m) + "\n").encode("utf-8"))
            p.stdin.flush()

        try:
            send(cmd="hello", launched_at=time.time())
            time.sleep(1.4)                  # long enough for the card to work out its rows
            for n in (1, 2, 3):
                send(cmd="stage", n=n)
                time.sleep(0.1)
            send(cmd="open")
            # The app does NOT close the pipe here - that is the point.
            rc = p.wait(timeout=20)
            err = p.stderr.read().decode("utf-8", "replace")
        finally:
            if p.poll() is None:
                p.kill()
            if not p.stdin.closed:
                p.stdin.close()
            p.stderr.close()
        return rc, err

    @unittest.skipUnless(sys.platform == "win32", "the gate is Windows-only")
    def test_the_child_exits_cleanly_while_the_app_still_holds_the_pipe(self):
        sys.path.insert(0, str(APP))
        from backend import launch_gate
        diag = launch_gate._diagnostics_file()
        error_file = diag.with_name("gate-error.txt")
        for attempt in range(3):
            if error_file.exists():
                os.replace(error_file, error_file.with_suffix(".old.txt"))
            rc, err = self.run_child()
            self.assertEqual(rc & 0xFFFFFFFF, 0,
                             f"attempt {attempt + 1}: the gate child crashed on exit "
                             f"(exit {hex(rc & 0xFFFFFFFF)}): {err.strip()[-400:]}")
            self.assertFalse(error_file.exists(),
                             f"attempt {attempt + 1}: the card failed inside: "
                             f"{error_file.read_text(encoding='utf-8') if error_file.exists() else ''}")
            timeline = json.loads(diag.read_text(encoding="utf-8"))
            self.assertEqual(timeline.get("design"), launch_gate.DESIGN_VERSION, "this run wrote no timeline")
            marks = timeline["seconds_since_launch"]
            self.assertIn("card_data", marks, f"attempt {attempt + 1}: the card never got its rows: {marks}")
            self.assertNotIn("card_errors", marks, f"attempt {attempt + 1}: {marks.get('card_errors')}")
            self.assertIn("opened", marks, "the card never finished")


if __name__ == "__main__":
    unittest.main(verbosity=2)
