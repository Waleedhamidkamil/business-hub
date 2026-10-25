# -*- coding: utf-8 -*-
"""Speed without staleness (2026-10-01).

Waleed: going back to the hub took about two seconds every time, and he asked
for it to be fast "with no issues to up to date data". Measured on the live
data, `dashboard_needs_attention` spent 1.3 of its 1.4 seconds in
`client_master._connection()` re-running its whole setup on each of 359 client
name lookups. The fix gates that setup, and the alias map, on SQLite's own
"did anyone commit since?" counter (`backend/db_version.py`).

A cache is only a fix if it can never serve an old answer, so most of this
file is about STALENESS, not speed:

  1. a client added through another connection is seen on the very next call,
  2. a name typed on a saved document (database.remember_client writes the
     legacy `clients` table) still becomes a client record - that import used
     to ride on every connection and now rides on the change counter,
  3. a write from ANOTHER PROCESS is seen too (a test run, the other agent),
  4. a rename moves the map,
  5. an unreadable counter means "rebuild", never "serve".

And one structural guard on the speed itself, which is deterministic where a
stopwatch would be flaky: the hub's Needs Attention must open a handful of
shared-database connections, not one per document row.

Runs against a SCRATCH shared database - `shared_library.connect` is pointed at
a temporary file, the same seam tests/master_items_two_languages.test.py uses.
The last test reads the live data and writes nothing.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import client_master, db_version, shared_library


def _insert_client(path, name, alias=None):
    """Write a client the way another connection (or process) would."""
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(path)
    try:
        cid = uuid.uuid4().hex
        conn.execute("INSERT INTO client_records(id,name,details_json,archived,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?)", (cid, name, "{}", 0, now, now))
        conn.execute("INSERT INTO client_aliases VALUES(?,?,?)",
                     (client_master.normalize(alias or name), alias or name, cid))
        conn.commit()
    finally:
        conn.close()


class ScratchShared(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix="-zz-dbversion.db")
        os.close(fd)
        os.unlink(self.path)                      # let sqlite create it fresh
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE clients(name TEXT PRIMARY KEY, use_count INTEGER,"
                     " last_used_at TEXT)")
        conn.commit()
        conn.close()
        self._real = shared_library.connect
        shared_library.connect = lambda path=None, _r=self._real: _r(self.path)
        # The first call creates the schema.
        client_master.canonical_map()

    def tearDown(self):
        shared_library.connect = self._real
        # db_version holds no handle, so the scratch file can always be
        # deleted - the first design kept one open and broke exactly this.
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    # 1 -----------------------------------------------------------------
    def test_a_client_added_by_another_connection_is_seen_next_call(self):
        before = client_master.canonical_map()
        self.assertNotIn("zz acme trading", before)
        _insert_client(self.path, "ZZ Acme Trading")
        after = client_master.canonical_map()
        self.assertEqual(after.get("zz acme trading"), "ZZ Acme Trading",
                         "a client committed by another connection must be in the "
                         "very next map - the cache may only serve while NOTHING "
                         "has been committed")

    # 2 -----------------------------------------------------------------
    def test_a_name_typed_on_a_saved_document_still_becomes_a_client(self):
        # database.remember_client writes ONLY the legacy table; turning that
        # into a client record is the sync's job. Before 2026-10-01 it ran on
        # every connection; now it must still run after this write.
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO clients(name,use_count,last_used_at) VALUES(?,1,'x')",
                     ("ZZ Typed On An Invoice",))
        conn.commit()
        conn.close()
        self.assertEqual(client_master.canonical("zz typed on an invoice"),
                         "ZZ Typed On An Invoice",
                         "the legacy-name import must re-run after a write - "
                         "otherwise a client typed on a document never becomes a record")
        conn = sqlite3.connect(self.path)
        n = conn.execute("SELECT count(*) FROM client_records WHERE name=?",
                         ("ZZ Typed On An Invoice",)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1, "the import must create exactly one record")

    # 3 -----------------------------------------------------------------
    def test_a_write_from_another_process_is_seen(self):
        code = (
            "import sqlite3,sys,uuid\n"
            "c=sqlite3.connect(sys.argv[1])\n"
            "i=uuid.uuid4().hex\n"
            "c.execute(\"INSERT INTO client_records VALUES(?,?,?,?,?,?)\","
            "(i,'ZZ Other Process','{}',0,'t','t'))\n"
            "c.execute(\"INSERT INTO client_aliases VALUES(?,?,?)\","
            "('zz other process','ZZ Other Process',i))\n"
            "c.commit();c.close()\n")
        client_master.canonical_map()             # cache is warm
        subprocess.run([sys.executable, "-c", code, self.path], check=True)
        self.assertEqual(client_master.canonical("ZZ OTHER PROCESS"), "ZZ Other Process",
                         "the database's change counter moves for commits from ANY process; "
                         "a cache that missed this would show the other window's "
                         "work only after a restart")

    # 4 -----------------------------------------------------------------
    def test_a_rename_moves_the_map(self):
        _insert_client(self.path, "ZZ Old Name")
        self.assertEqual(client_master.canonical("zz old name"), "ZZ Old Name")
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE client_records SET name='ZZ New Name' WHERE name='ZZ Old Name'")
        conn.commit()
        conn.close()
        self.assertEqual(client_master.canonical("zz old name"), "ZZ New Name",
                         "the old spelling must resolve to the NEW display name at once")

    # 5 -----------------------------------------------------------------
    def test_an_unreadable_counter_means_rebuild_not_serve(self):
        client_master.canonical_map()             # warm
        real = db_version.version
        db_version.version = lambda path: None
        try:
            # Write AFTER blinding the counter. A cache that trusted a None
            # would keep serving the map without this client.
            _insert_client(self.path, "ZZ Blind Spot")
            self.assertEqual(client_master.canonical("zz blind spot"), "ZZ Blind Spot",
                             "None from db_version must force a rebuild")
        finally:
            db_version.version = real

    def test_nothing_changed_means_no_rework(self):
        # The point of the change: with no commits in between, the second call
        # must not redo the setup. Counted by spying on _sync.
        client_master.canonical_map()
        calls = []
        real = client_master._sync
        client_master._sync = lambda c: (calls.append(1), real(c))
        try:
            for _ in range(50):
                client_master.canonical_map()
        finally:
            client_master._sync = real
        self.assertEqual(calls, [], "50 lookups with no write in between re-ran the "
                                    "client setup %d times" % len(calls))


class DbVersionWal(unittest.TestCase):
    """The databases run in WAL mode since 2026-10-22 (multi-user, sqlite_policy).

    SQLite creates an EMPTY -wal file when a connection opens and deletes it
    when the last one closes - a new timestamp every time, with nothing
    written. Fingerprinting the -wal's mtime turned every open into "changed",
    so every cache recomputed on every call (the 1.4 s hub came back)."""

    def setUp(self):
        from backend import sqlite_policy
        self.open = sqlite_policy.open_db
        fd, self.path = tempfile.mkstemp(suffix="-zz-dvwal.db")
        os.close(fd)
        os.unlink(self.path)
        c = self.open(self.path)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def test_opening_and_closing_without_writing_changes_nothing(self):
        v = db_version.version(self.path)
        for _ in range(3):
            c = self.open(self.path)
            self.assertEqual(db_version.version(self.path), v, "an OPEN must not move it")
            list(c.execute("SELECT * FROM t"))
            self.assertEqual(db_version.version(self.path), v, "a READ must not move it")
            c.close()
            self.assertEqual(db_version.version(self.path), v, "a CLOSE must not move it")

    def test_a_commit_in_wal_moves_it(self):
        keep = self.open(self.path)            # held open, so the commit stays in the -wal
        try:
            v = db_version.version(self.path)
            keep.execute("INSERT INTO t VALUES (1)")
            keep.commit()
            v2 = db_version.version(self.path)
            self.assertNotEqual(v2, v, "a committed write must move it")
            keep.execute("INSERT INTO t VALUES (2)")
            keep.commit()
            self.assertNotEqual(db_version.version(self.path), v2, "every commit moves it")
        finally:
            keep.close()


class DbVersion(unittest.TestCase):
    def test_fingerprint_moves_on_commit_and_only_on_commit(self):
        fd, path = tempfile.mkstemp(suffix="-zz-dv.db")
        os.close(fd)
        try:
            c = sqlite3.connect(path)
            c.execute("CREATE TABLE t(x)")
            c.commit()
            v1 = db_version.version(path)
            self.assertIsNotNone(v1)
            self.assertEqual(db_version.version(path), v1, "no commit, no change")
            list(c.execute("SELECT * FROM t"))
            self.assertEqual(db_version.version(path), v1, "a READ must not move it")
            c.execute("INSERT INTO t VALUES(1)")
            self.assertEqual(db_version.version(path), v1,
                             "an UNCOMMITTED write must not move it")
            c.commit()
            v2 = db_version.version(path)
            self.assertNotEqual(v2, v1, "a commit must move it")
            # Same connection again: the first design (PRAGMA data_version)
            # could not see a connection's own commits; this one must.
            c.execute("INSERT INTO t VALUES(2)")
            c.commit()
            self.assertNotEqual(db_version.version(path), v2,
                                "every commit moves it, whichever connection made it")
            c.close()
        finally:
            os.unlink(path)

    def test_it_holds_no_file_open(self):
        # Windows refuses to delete a file another handle holds. If version()
        # kept one open, this unlink would raise PermissionError.
        fd, path = tempfile.mkstemp(suffix="-zz-dv2.db")
        os.close(fd)
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE t(x)")
        c.commit()
        c.close()
        self.assertIsNotNone(db_version.version(path))
        os.unlink(path)
        self.assertFalse(os.path.exists(path))

    def test_a_file_that_is_not_a_database_is_unknown(self):
        fd, path = tempfile.mkstemp(suffix="-zz-dv3.db")
        os.write(fd, b"this is not sqlite at all, just thirty bytes+")
        os.close(fd)
        try:
            self.assertIsNone(db_version.version(path),
                              "an unreadable header must be 'unknown', never a fingerprint")
        finally:
            os.unlink(path)

    def test_missing_file_is_unknown(self):
        self.assertIsNone(db_version.version(os.path.join(tempfile.gettempdir(),
                                                          "zz-no-such-db.db")))


class LiveHubIsCheap(unittest.TestCase):
    """Reads the live data, writes nothing."""

    def test_needs_attention_opens_a_handful_of_shared_connections(self):
        from backend.profile_manager import manager as pm
        from pathlib import Path
        app = Path(ROOT)
        pm().bootstrap_profiles(legacy_db=app / "data" / "po_tool.db",
                                legacy_settings=app / "data" / "settings.json",
                                legacy_assets=app / "static")
        pm().activate("abraj")
        from backend import database
        database.dashboard_needs_attention()      # warm: schema + first sync
        opened = []
        real = shared_library.connect

        def counting(path=None):
            opened.append(1)
            return real(path)
        shared_library.connect = counting
        try:
            database.dashboard_needs_attention()
        finally:
            shared_library.connect = real
        # 359 before the fix (one per document row that names a client). The
        # bound is loose on purpose - it is here to catch a per-row lookup
        # coming back, not to pin an exact count.
        self.assertLess(len(opened), 25,
                        "Needs Attention opened %d shared-database connections; a "
                        "per-row client lookup has crept back in. Build the alias "
                        "map once (database._identities) and pass it down."
                        % len(opened))


class OtherHotPathsStayCheap(unittest.TestCase):
    """The same per-row pattern, found by timing every screen (2026-10-01).
    Reads the live data, writes nothing."""

    @classmethod
    def setUpClass(cls):
        from backend.profile_manager import manager as pm
        from pathlib import Path
        app = Path(ROOT)
        pm().bootstrap_profiles(legacy_db=app / "data" / "po_tool.db",
                                legacy_settings=app / "data" / "settings.json",
                                legacy_assets=app / "static")
        pm().activate("abraj")

    def test_invoice_dn_picker_uses_one_connection_not_four_per_dn(self):
        # The invoice item field lists every priced delivery note. Each DN's
        # summary asked four helpers that each opened a connection: 314 for 78
        # DNs, 220 ms on the first keystroke.
        from backend import database
        opened = []
        real = database._connect

        def counting():
            opened.append(1)
            return real()
        database._connect = counting
        try:
            dns = database.list_invoiceable_dns(300)
        finally:
            database._connect = real
        self.assertGreater(len(dns), 0, "the live data has priced DNs; an empty "
                                        "list means the picker broke, not that it got fast")
        self.assertLess(len(opened), 10,
                        "list_invoiceable_dns opened %d connections - the per-DN "
                        "helpers are opening their own again. Pass the list's one "
                        "connection down (database._use)." % len(opened))

    def test_clients_list_counts_usage_once_for_all_clients(self):
        # Master Data > Clients rescanned every document in both companies once
        # per client, parsing every PO's rows each time.
        calls = []
        real = client_master.usage_index

        def counting():
            calls.append(1)
            return real()
        client_master.usage_index = counting
        try:
            out = client_master.list_records("", "all")
        finally:
            client_master.usage_index = real
        self.assertTrue(out["ok"])
        if out["records"]:
            self.assertEqual(len(calls), 1,
                             "the clients list built the usage index %d times for %d "
                             "clients - it must be built once and shared"
                             % (len(calls), len(out["records"])))

    def test_usage_matches_the_original_per_client_scan(self):
        # Compared against the ORIGINAL algorithm, kept verbatim below - not
        # against usage_for itself, which would compare the new code with the
        # new code and pass whatever it did (that version of this test let a
        # dropped PO-allocation count straight through).
        import json as _json
        import sqlite3 as _sqlite3
        from backend import profile_manager
        norm = client_master.normalize

        def reference(names):
            keys = {norm(n) for n in names}
            by_profile, refs, errors = {}, {}, []
            for profile in profile_manager.list_profiles():
                pid = profile["id"]
                path = profile_manager.profile_db_path(pid)
                if not path.is_file():
                    errors.append(f"{pid}: profile database unavailable")
                    continue
                c = None
                try:
                    c = _sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    total = 0
                    for table in ("quotes", "dns", "invoices", "client_lpos"):
                        if table not in tables:
                            continue
                        n = sum(1 for r in c.execute(f"SELECT client_name FROM {table}") if norm(r[0]) in keys)
                        if n:
                            refs[f"{pid}.{table}"] = n
                        total += n
                    if "pos" in tables:
                        n = 0
                        for r in c.execute("SELECT rows_json FROM pos"):
                            for line in _json.loads(r[0] or "[]"):
                                if isinstance(line, dict) and norm(line.get("company")) in keys:
                                    n += 1
                        if n:
                            refs[f"{pid}.po_allocations"] = n
                    by_profile[pid] = total
                except (_sqlite3.Error, ValueError, TypeError) as exc:
                    errors.append(f"{pid}: {exc}")
                finally:
                    if c is not None:
                        c.close()
            c = None
            try:
                c = shared_library.connect()
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "master_items" in tables:
                    cols = {r[1] for r in c.execute("PRAGMA table_info(master_items)")}
                    if "last_company" in cols:
                        n = sum(1 for r in c.execute("SELECT last_company FROM master_items") if norm(r[0]) in keys)
                        if n:
                            refs["materials.company_memory"] = n
                if "column_memory" in tables:
                    n = sum(1 for r in c.execute("SELECT value FROM column_memory") if norm(r[0]) in keys)
                    if n:
                        refs["column_memory"] = n
            except _sqlite3.Error as exc:
                errors.append(str(exc))
            finally:
                if c is not None:
                    c.close()
            return {"total": sum(by_profile.values()), "by_profile": by_profile,
                    "references": refs, "errors": errors}

        # Real names: every client's aliases, plus the companies POs are
        # allocated to (so the PO-allocation count is actually exercised).
        from backend import database
        companies = set()
        with database._connect() as conn:
            for r in conn.execute("SELECT rows_json FROM pos"):
                for line in _json.loads(r[0] or "[]"):
                    if isinstance(line, dict) and str(line.get("company") or "").strip():
                        companies.add(str(line["company"]).strip())
        out = client_master.list_records("", "all")
        groups = [client_master.aliases_for(r["name"]) for r in out["records"]]
        groups += [[c] for c in sorted(companies)] + [["zz-nobody-at-all"]]

        # These tables are LIVE, and the rest of the suite writes to them. A
        # write landing between building the index and the reference scan
        # makes them disagree for a reason that has nothing to do with this
        # code (it happened once, mid-suite). So: compare, and if any of the
        # three database files changed during the comparison, do it again.
        # Only a mismatch over UNCHANGED data is a failure.
        files = [str(profile_manager.profile_db_path(p["id"]))
                 for p in profile_manager.list_profiles()]
        files.append(str(profile_manager.shared_db_path()))

        def snapshot():
            return [db_version.version(f) for f in files]

        for attempt in range(5):
            before = snapshot()
            index = client_master.usage_index()
            problems, saw_po = [], False
            for names in groups:
                want = reference(names)
                got_shared = client_master.usage_for(names, index)
                got_fresh = client_master.usage_for(names)
                if got_shared != want or got_fresh != want:
                    problems.append((names, want, got_shared, got_fresh))
                saw_po = saw_po or any(k.endswith(".po_allocations") for k in want["references"])
            if snapshot() == before and None not in before:
                break                    # the data held still: the verdict counts
        else:
            self.skipTest("the live databases kept changing under the comparison "
                          "(another test run?) - NOTHING WAS CHECKED")
        self.assertEqual(problems, [],
                         "usage differs from the original scan over unchanged data")
        self.assertTrue(saw_po, "no tested name has PO allocations, so that count "
                                "went unchecked - pick names from the pos table")

if __name__ == "__main__":
    unittest.main(verbosity=2)
