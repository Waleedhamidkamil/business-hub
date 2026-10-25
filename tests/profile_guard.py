# -*- coding: utf-8 -*-
"""Say "wrong profile" out loud instead of failing cryptically (2026-09-05).

The problem this solves
-----------------------
Several tests only make sense under a specific company profile - they read
`config` constants, brand geometry or numbering that differ per profile. When
the active profile is something else, they fail on an assertion deep inside,
with a message about millimetres or numbering that has nothing to do with the
real cause.

That is how `AGENT-BOARD` ended up carrying this as a standing WARNING rather
than a fix: *"running the whole .py suite can leave data/profile-registry.json
pointing at eshraqat; six unrelated tests then fail for that reason alone -
check the active profile before believing them."* A reader who has to know that
in advance will eventually stop believing ANY red, which is exactly what
happened here: five genuinely broken things sat behind "known failures" for
weeks, including ten dead Data Manager sections the user could still click.

A test that cannot be meaningfully run should SAY SO. A skip with a plain
reason is honest; a cryptic assertion is a red herring that costs someone an
afternoon.

Deliberately a SKIP, not a failure: the profile being wrong is a property of
the run, not of the code under test, and failing would re-create the same
false-red problem from the other direction. The skip message names the culprit
so it still gets fixed.

Usage - one line, before the module reads any profile-dependent constant:

    from tests.profile_guard import require_profile   # or a sys.path import
    require_profile("abraj")
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = APP_ROOT / "data" / "profile-registry.json"


def active_profile() -> str:
    """The profile the app would start in, read from disk.

    Read from the REGISTRY FILE rather than `config.ACTIVE_PROFILE`, because
    the point is to check the state a fresh subprocess will pick up - and
    `config` may already have been reloaded by the test itself.
    """
    try:
        return str(json.loads(REGISTRY.read_text(encoding="utf-8"))
                   .get("active_profile") or "")
    except Exception:
        return ""


def require_profile(expected: str = "abraj") -> None:
    """Skip the whole module unless the active profile is `expected`.

    Exits 0 with a loud reason rather than raising, because this repo runs each
    file as `python tests/x.test.py` (see [[11-RUN-AND-MAINTAIN]]): a
    module-level `SkipTest` there is not caught by anything, so it propagates
    and the process exits NON-ZERO - which is the very red herring this guard
    exists to remove. Exit 0 plus an unmissable message is the honest answer:
    nothing was checked, and the reason is on screen.

    If this suite ever moves to `unittest discover`, swap the exit for
    `raise unittest.SkipTest(reason)` - discovery does understand that, and
    `unittest` is imported here so the swap is one line.
    """
    got = active_profile()
    if got == expected:
        return
    reason = (
        f"needs the '{expected}' profile but data/profile-registry.json says "
        f"'{got or 'unreadable'}'. NOTHING WAS CHECKED - this is neither a pass "
        f"nor a failure of the code under test. A previous test left the "
        f"registry switched. Restore it (set active_profile to '{expected}') "
        f"and re-run. If a test really does leave it switched, fix that test: "
        f"restore in a `finally`, not as a trailing statement, so a crash "
        f"cannot skip the cleanup."
    )
    print(f"SKIPPED (wrong profile): {reason}", file=sys.stderr)
    print(f"SKIPPED (wrong profile): {reason}")
    sys.exit(0)
