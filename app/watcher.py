"""Inbox watcher — auto-queue files dropped directly into the storage inbox.

The HTTP `/upload` endpoint saves a file into the inbox AND inserts a `docs`
row (status='queued') in one shot, so the worker picks it up. But files can
also land in the inbox by other means — a bulk copy, an ops drop, a future S3
sync — with no `docs` row to drive them.

This watcher closes that gap. It polls `storage.list_inbox()` and, for any
storage_key that does NOT yet have a `docs` row, inserts a fresh queued row
(name + storage_key). The existing async worker then claims and ingests it
exactly as if it had come through `/upload`.

Dedup is on `storage_key`: HTTP-uploaded files already have a row, so the
watcher skips them; the worker moves processed files OUT of the inbox, so
`list_inbox()` shrinks and completed files are never re-queued.

Runs as one daemon thread inside the app process. Set INGEST_WATCHER=0 to
disable, WATCHER_POLL_SECONDS to tune the poll interval. A SINGLE thread only —
two watchers would race on inserts for the same key.
"""
import os
import threading
import time
import traceback

from . import storage
from .db import get_conn

ENABLED = os.getenv("INGEST_WATCHER", "1") == "1"
_POLL = float(os.getenv("WATCHER_POLL_SECONDS", "5"))

_started = False


def scan_once() -> int:
    """Scan the inbox once; queue any file that has no docs row yet.

    For each storage_key in `storage.list_inbox()` without a matching
    `docs.storage_key`, insert a new row (status='queued', progress=0). The
    storage_key existence check makes this idempotent: HTTP-uploaded files
    (which already have a row) and already-queued files are skipped, so a key
    is never double-inserted. Returns the count of new rows created.
    """
    new = 0
    with get_conn() as conn:
        for key in storage.list_inbox():
            exists = conn.execute(
                "SELECT 1 FROM docs WHERE storage_key = %s", (key,)
            ).fetchone()
            if exists:
                continue
            name = storage.original_filename(key)
            conn.execute(
                "INSERT INTO docs (name, status, progress, storage_key) "
                "VALUES (%s, 'queued', 0, %s)",
                (name, key),
            )
            new += 1
            print(f"[watcher] queued {name} ({key})")
    return new


def _loop() -> None:
    print("[watcher] thread started")
    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"[watcher] scan error: {e!r}\n{traceback.format_exc()}")
        time.sleep(_POLL)


def start() -> None:
    """Start the inbox watcher thread. Call once on app boot."""
    global _started
    if not ENABLED or _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[watcher] inbox watcher running (poll {_POLL}s)")
