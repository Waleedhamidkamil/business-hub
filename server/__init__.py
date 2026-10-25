"""Shared office-server runtime helpers."""
from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from pathlib import Path


def data_dir() -> Path:
    configured = str(os.environ.get("BUSINESSHUB_DATA") or "").strip()
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parent.parent / "data"


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """A reusable threaded server that refuses accidental duplicate binds."""

    allow_reuse_address = False
    daemon_threads = True
