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
    if the doc already ingested once its file was moved inbox→processed, so also
    check there (makes retry / boot-sweep resume work on completed docs); finally
    fall back to the legacy uploads dir by filename."""
    from pathlib import Path
    from .config import PROCESSED_DIR

    key = row.get("storage_key")
    if key:
        p = storage.inbox_path(key)
        if p.exists():
            return p
        moved = Path(PROCESSED_DIR) / key      # ingest succeeded before → file moved here
        if moved.exists():
            return moved
    # legacy / synchronous uploads
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
        from .config import DEFER_ENRICH
        res = process_doc(doc_id, src, name, should_cancel=should_cancel, defer_enrich=DEFER_ENRICH)
        if res and res.get("deferred"):
            # phase-1 only: leave it 'ready_lite' (answerable). The Enrichment Agent
            # runs phase-2 and flips it to 'ready'. Don't move the source out yet —
            # the agent needs it; it gets moved when enrichment completes.
            print(f"[worker] doc {doc_id} answerable · handed to Enrichment Agent · {name}")
            return
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
        # Two-phase ingest: if PHASE 1 already landed text pages (doc reached
        # 'ready_lite' = answerable), a PHASE 2 failure (vision/compile/enrichers)
        # must NOT nuke a usable doc. Keep it 'ready' (text-only) + note the error.
        with get_conn() as conn:
            npg = conn.execute(
                "SELECT count(*) AS n FROM pages WHERE doc_id = %s", (doc_id,)
            ).fetchone()["n"]
        if npg > 0:
            log_ingest(doc_id, "ready", "✓ text-usable (enrichment incomplete)", level="warn")
            try:
                from .ingest import _log_event
                _log_event(doc_id, "ready", f"enrichment incomplete: {msg[:300]}")
            except Exception:
                pass
            with get_conn() as conn:
                conn.execute(
                    "UPDATE docs SET status = 'ready', progress = 100, "
                    "error = %s, ready_at = %s, updated_at = now() WHERE id = %s",
                    (f"enrichment incomplete: {msg[:400]}", _now(), doc_id),
                )
            _move(row, "processed")
        else:
            log_ingest(doc_id, "failed", f"✗ failed: {msg[:160]}", level="error")
            try:
                from .ingest import _log_event
                _log_event(doc_id, "failed", f"ingest failed: {msg[:300]}")
            except Exception:
                pass
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
    """Requeue docs orphaned by a worker/API that died mid-job. Two cases:
      • 'processing'  — phase 1 was interrupted.
      • 'ready_lite'  — phase 1 finished (doc is answerable) but phase 2
        (vision/compile/enrichers) was interrupted by a restart and nothing
        is carrying it across the process boundary, so it would hang forever.
    process_doc is rerun-safe (it DELETEs pages/nodes before each phase), so a
    requeue cleanly re-runs from the top. A genuinely-failed phase 2 is marked
    'ready' (not 'ready_lite') by _loop's except handler, so it's never swept."""
    from .config import DEFER_ENRICH
    # In deferred mode 'ready_lite' is a valid resting state (answerable, awaiting
    # the Enrichment Agent) — don't requeue those, the agent owns them. Only sweep
    # 'processing' orphans. In classic mode sweep both.
    statuses = "('processing')" if DEFER_ENRICH else "('processing', 'ready_lite')"
    try:
        with get_conn() as conn:
            n = conn.execute(
                f"UPDATE docs SET status = 'queued', progress = 0, pages_done = 0 "
                f"WHERE status IN {statuses}"
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
