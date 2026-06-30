"""Enrichment Agent — the deferred PHASE-2 lane.

When DEFER_ENRICH=1 the upload worker runs only PHASE 1 (render + text → the doc
is answerable in seconds) and leaves it at status 'ready_lite'. This daemon then
claims those docs on its own schedule and runs PHASE 2 (vision / tree / compile /
enrichers), throttled to ENRICH_CONCURRENCY docs at a time, pausable, and fully
resume-cache aware (a restart re-enriches from cache in ~20s, not from page 1).

Leader-gated (started from background.start_all) so exactly one process runs it.
"""

import threading
import time

from .db import get_conn
from .config import ENRICH_CONCURRENCY, ENRICH_POLL_SECONDS, DEFER_ENRICH

_started = False
_lock = threading.Lock()
_enriching: set[int] = set()        # doc_ids currently being enriched (this process)
_recent: list[dict] = []            # ring of finished docs (newest first), cap 12
_paused = False
_concurrency = ENRICH_CONCURRENCY


# ── controls (called from routes) ──────────────────────────────────────────────
def is_paused() -> bool:
    return _paused


def pause() -> None:
    global _paused
    _paused = True


def resume() -> None:
    global _paused
    _paused = False


def set_concurrency(n: int) -> int:
    global _concurrency
    _concurrency = max(1, min(int(n), 8))
    return _concurrency


def _remember(doc_id: int, name: str, ok: bool) -> None:
    with _lock:
        _recent.insert(0, {"id": doc_id, "name": name, "ok": ok})
        del _recent[12:]


# ── one doc ─────────────────────────────────────────────────────────────────────
def _enrich_one(row: dict) -> None:
    from . import ingest_control as kill
    from .ingest import process_doc, _now, _set
    from .worker import _resolve_src
    doc_id = row["id"]
    name = row.get("name") or f"doc {doc_id}"
    gen0 = kill.cancel_gen()
    should_cancel = lambda: kill.should_cancel_doc(doc_id, gen0)
    try:
        src = _resolve_src(row)
        if not src or not src.exists():
            raise FileNotFoundError(f"source missing for doc {doc_id}: {src}")
        # enrich_mode keeps the doc 'ready_lite' (answerable) while phase 2 runs
        process_doc(doc_id, src, name, should_cancel=should_cancel, enrich_mode=True)
        with get_conn() as conn:
            conn.execute(
                "UPDATE docs SET status='ready', progress=100, ready_at=now(), updated_at=now() "
                "WHERE id=%s",
                (doc_id,),
            )
        try:
            from .worker import _move
            _move(row, "processed")     # enrichment done → archive the source
        except Exception:
            pass
        _remember(doc_id, name, True)
    except kill.Cancelled:
        # leave it answerable (ready_lite) — user cancelled enrichment, not the doc
        print(f"[enrich] doc {doc_id} enrichment cancelled")
    except Exception as e:
        # phase 2 failed but phase 1 stands → keep answerable, mark ready so it
        # doesn't loop forever. Same policy as the inline worker's fallback.
        print(f"[enrich] doc {doc_id} FAILED: {type(e).__name__}: {e}")
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE docs SET status='ready', updated_at=now() WHERE id=%s AND status='ready_lite'",
                    (doc_id,),
                )
        except Exception:
            pass
        _remember(doc_id, name, False)
    finally:
        with _lock:
            _enriching.discard(doc_id)


def _claim(limit: int) -> list[dict]:
    """Pick up to `limit` answerable-but-unenriched docs (status ready_lite),
    oldest first, that aren't already being worked by this process."""
    if limit <= 0:
        return []
    with _lock:
        busy = tuple(_enriching) or (0,)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, storage_key FROM docs "
            "WHERE status = 'ready_lite' AND id <> ALL(%s) "
            "ORDER BY ready_at NULLS FIRST, id LIMIT %s",
            (list(busy), limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── status (for the UI panel) ────────────────────────────────────────────────────
def status() -> dict:
    with _lock:
        active = list(_enriching)
        recent = list(_recent)
    enriching, queued = [], []
    try:
        with get_conn() as conn:
            if active:
                for r in conn.execute(
                    "SELECT d.id, d.name, d.progress, d.pages_done, d.page_count, "
                    "(SELECT step FROM ingest_log WHERE doc_id=d.id ORDER BY id DESC LIMIT 1) AS step "
                    "FROM docs d WHERE d.id = ANY(%s)",
                    (active,),
                ).fetchall():
                    enriching.append(dict(r))
            for r in conn.execute(
                "SELECT id, name FROM docs WHERE status='ready_lite' AND id <> ALL(%s) "
                "ORDER BY ready_at NULLS FIRST, id LIMIT 50",
                (active or [0],),
            ).fetchall():
                queued.append(dict(r))
    except Exception as e:
        print(f"[enrich] status query failed: {e!r}")
    return {
        "enabled": DEFER_ENRICH,
        "running": _started and not _paused,
        "paused": _paused,
        "concurrency": _concurrency,
        "active": len(active),
        "queued_count": len(queued),
        "enriching": enriching,
        "queued": queued,
        "recent": recent,
    }


# ── loop ──────────────────────────────────────────────────────────────────────────
def _loop() -> None:
    while True:
        try:
            if _paused:
                time.sleep(ENRICH_POLL_SECONDS)
                continue
            with _lock:
                free = _concurrency - len(_enriching)
            rows = _claim(free)
            for row in rows:
                with _lock:
                    _enriching.add(row["id"])
                threading.Thread(target=_enrich_one, args=(row,), daemon=True).start()
            time.sleep(ENRICH_POLL_SECONDS)
        except Exception as e:
            print(f"[enrich] loop error: {e!r}")
            time.sleep(ENRICH_POLL_SECONDS)


def start() -> None:
    """Start the enrichment agent loop. No-op unless DEFER_ENRICH is on."""
    global _started
    if _started or not DEFER_ENRICH:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[enrich] Enrichment Agent started · concurrency={_concurrency}")
