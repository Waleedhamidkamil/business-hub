"""Cleanup transaction guards, never using production files."""
import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class CleanupSafety(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location("cleanup",ROOT/"scripts/cleanup_client_master.py")
        self.mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.live=self.root/"live.db"; self.clean=self.root/"clean.db"
        for path in (self.live,self.clean):
            with closing(sqlite3.connect(path)) as c, c:
                c.executescript("CREATE TABLE clients(name TEXT); INSERT INTO clients VALUES('Test'); "
                               "CREATE TABLE master_items(id TEXT, name TEXT); INSERT INTO master_items VALUES('M1','Preserved');")
        with closing(sqlite3.connect(self.live)) as c:
            self.before=self.mod.fingerprint(c)
        with closing(sqlite3.connect(self.clean)) as c, c:
            c.execute("DELETE FROM clients")
    def test_publish_changes_only_client_tables(self):
        self.mod.publish(self.live,self.clean,self.before,{})
        with closing(sqlite3.connect(self.live)) as c:
            after=self.mod.fingerprint(c)
            self.assertEqual(after['master_items'],self.before['master_items'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0],0)
    def test_changed_live_data_aborts_without_deletion(self):
        with closing(sqlite3.connect(self.live)) as c, c:c.execute("INSERT INTO clients VALUES('New')")
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.mod.publish(self.live,self.clean,self.before,{})
        with closing(sqlite3.connect(self.live)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0],2)
    def test_changed_profile_aborts(self):
        profile=self.root/'profile.db'
        with closing(sqlite3.connect(profile)) as c, c:
            c.execute('CREATE TABLE documents(name TEXT)')
            before=self.mod.fingerprint(c)
            c.execute("INSERT INTO documents VALUES('Recent')")
        with self.assertRaisesRegex(RuntimeError,'changed'):
            self.mod.publish(self.live,self.clean,self.before,{profile:before})
        with closing(sqlite3.connect(self.live)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM clients').fetchone()[0],1)

if __name__=='__main__':unittest.main()
