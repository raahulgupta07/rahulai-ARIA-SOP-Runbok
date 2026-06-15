"""Async ingest worker.

The `docs` table doubles as the job queue. A queued doc is claimed atomically
with `FOR UPDATE SKIP LOCKED` (so N workers never grab the same row), processed
through the ingest pipeline with live progress, then marked ready/failed. The
source file lives in the storage inbox and is moved to processed/failed when
done.

Runs as one or more daemon threads inside the app process (no extra container).
Set INGEST_WORKER=0 to disable (e.g. run a dedicated worker process instead),
INGEST_WORKERS=N for parallel docs.
"""
import os
import threading
import time
import traceback

from . import storage
from .config import UPLOADS_DIR
from .db import get_conn, log_ingest
from .ingest import process_doc, _now

ENABLED = os.getenv("INGEST_WORKER", "1") == "1"
_N = int(os.getenv("INGEST_WORKERS", "1"))
_IDLE_SLEEP = float(os.getenv("INGEST_POLL_SECONDS", "2"))

_started = False


def _claim() -> dict | None:
    """Atomically grab one queued doc and flip it to processing. Returns the
    row (id, name, storage_key) or None if the queue is empty."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE docs SET status = 'processing', progress = 1, error = NULL "
            "WHERE id = ("
            "  SELECT id FROM docs WHERE status = 'queued' "
            "  ORDER BY created_at, id "
            "  FOR UPDATE SKIP LOCKED LIMIT 1"
            ") RETURNING id, name, storage_key"
        ).fetchone()
    return row


def _resolve_src(row: dict):
    """Locate the source file for a claimed doc. Prefer the storage inbox key;
    fall back to the legacy uploads dir by filename."""
    key = row.get("storage_key")
    if key:
        p = storage.inbox_path(key)
        if p.exists():
            return p
    # legacy / synchronous uploads
    from pathlib import Path

    legacy = Path(UPLOADS_DIR) / (row.get("name") or "")
    return legacy


def _run_one(row: dict) -> None:
    from . import ingest_control as kill
    doc_id = row["id"]
    name = row.get("name") or f"doc {doc_id}"
    src = _resolve_src(row)
    gen0 = kill.cancel_gen()
    should_cancel = lambda: kill.is_paused() or kill.should_cancel_doc(doc_id, gen0)
    try:
        if not src or not src.exists():
            raise FileNotFoundError(f"source file missing for doc {doc_id}: {src}")
        process_doc(doc_id, src, name, should_cancel=should_cancel)
        with get_conn() as conn:
            conn.execute(
                "UPDATE docs SET status = 'ready', progress = 100, "
                "error = NULL, ready_at = %s, updated_at = now() WHERE id = %s",
                (_now(), doc_id),
            )
        _move(row, "processed")
        try:
            from . import notify

            notify.emit("ingest", f"Document ingested · {name}", "ready", "success")
        except Exception:
            pass
        print(f"[worker] ingested doc {doc_id} · {name}")
    except kill.Cancelled:
        print(f"[worker] doc {doc_id} cancelled")
        log_ingest(doc_id, "cancelled", "⊘ cancelled", level="warn")
        with get_conn() as conn:
            conn.execute(
                "UPDATE docs SET status = 'cancelled', progress = 0, "
                "error = 'cancelled by admin' WHERE id = %s",
                (doc_id,),
            )
        # leave the source in the inbox so a retry can re-ingest it
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"[worker] doc {doc_id} FAILED: {msg}\n{traceback.format_exc()}")
        log_ingest(doc_id, "failed", f"✗ failed: {msg[:160]}", level="error")
        with get_conn() as conn:
            conn.execute(
                "UPDATE docs SET status = 'failed', error = %s, updated_at = now() WHERE id = %s",
                (msg[:500], doc_id),
            )
        _move(row, "failed")
    finally:
        kill.clear_doc(doc_id)


def _move(row: dict, outcome: str) -> None:
    key = row.get("storage_key")
    if not key:
        return
    try:
        storage.move_out(key, outcome)
    except Exception as e:
        print(f"[worker] move_out({key}, {outcome}) failed: {e!r}")


def _loop(idx: int) -> None:
    from . import ingest_control as kill
    print(f"[worker] thread {idx} started")
    while True:
        if kill.is_paused():            # master stop — claim nothing
            time.sleep(_IDLE_SLEEP)
            continue
        try:
            row = _claim()
        except Exception as e:
            print(f"[worker] claim error: {e!r}")
            time.sleep(_IDLE_SLEEP)
            continue
        if not row:
            time.sleep(_IDLE_SLEEP)
            continue
        _run_one(row)


def boot_sweep() -> None:
    """Requeue docs left 'processing' by a worker that died mid-job."""
    try:
        with get_conn() as conn:
            n = conn.execute(
                "UPDATE docs SET status = 'queued', progress = 0, pages_done = 0 "
                "WHERE status = 'processing'"
            ).rowcount
        if n:
            print(f"[worker] boot sweep requeued {n} stuck doc(s)")
    except Exception as e:
        print(f"[worker] boot sweep failed: {e!r}")


def start() -> None:
    """Start the ingest worker thread(s). Call once on app boot."""
    global _started
    if not ENABLED or _started:
        return
    _started = True
    boot_sweep()
    for i in range(max(1, _N)):
        threading.Thread(target=_loop, args=(i,), daemon=True).start()
    print(f"[worker] {_N} ingest worker(s) running")
