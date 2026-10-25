"""Temporary profile AND shared data for tests which save client documents.

Call activate() before importing backend configuration. This prevents test
fixtures from creating clients/materials in Waleed's actual catalogue.
"""
import atexit
import gc
import importlib
import json
import os
import sys
import tempfile
from contextlib import closing
from pathlib import Path

_active = None

def activate():
    global _active
    if _active is not None:
        return _active
    app = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(app))
    from backend import profile_manager
    original = profile_manager._MANAGER
    fallback = os.environ.pop("DOCUMENTS_HUB_LEGACY_FALLBACK", None)
    temporary = tempfile.TemporaryDirectory(prefix="business-hub-client-test-")
    root = Path(temporary.name)
    manager = profile_manager.ProfileManager(root / "data", app / "static")
    manager.create_registry("abraj")
    profile_manager._MANAGER = manager
    assert profile_manager.shared_db_path().resolve().is_relative_to(root.resolve())
    assert profile_manager.profile_db_path().resolve().is_relative_to(root.resolve())
    for pid in ("abraj", "eshraqat"):
        path = manager.settings_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "documents_root":str(root / "documents" / pid),
            "po_root":str(root / "documents" / pid / "PURCHASE ORDERS"),
            "invoice":{"bank_info":[
                {"key":"cash","label":"Cash","currency":"USD"},
                {"key":"adib","label":"ADIB","currency":"USD"},
                {"key":"bob","label":"Bank of Baghdad","currency":"USD"},
            ]},
        }),encoding="utf-8")
    # The helper can also wrap a test via runpy after its caller imported config.
    for name in ("backend.config","backend.database"):
        if name in sys.modules:importlib.reload(sys.modules[name])
    from backend import database, master_items, shared_library
    import sqlite3
    for pid in ("abraj","eshraqat"):
        with closing(sqlite3.connect(manager.profile_db_path(pid))) as c:
            c.executescript(database._SCHEMA)
    shared_library.init_shared_db()
    # The item RECORD is not part of SHARED_SCHEMA -- master_items owns its own
    # table and the live app creates it lazily on first use. An isolated shared
    # library without it is not a copy of the real one, and every test that
    # reads an item here dies with "no such table: master_items".
    with closing(shared_library.connect()) as c:
        master_items.init(c)
        c.commit()
    _active=root
    def finish():
        global _active
        gc.collect()
        profile_manager._MANAGER=original
        if fallback is not None:os.environ["DOCUMENTS_HUB_LEGACY_FALLBACK"]=fallback
        temporary.cleanup()
        _active=None
    atexit.register(finish)
    return root
