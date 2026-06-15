"""Cadence — the weekly self-audit digest (AIS-OS's 4th C).

Aria runs this without being asked: a background thread checks how long since
the last digest and, when due, composes a short summary from the Coverage Audit
and drops it in the activity feed as a notification. Proactive, not ask-only.

State lives in the notifications table (kind='digest') — no new table, crash-safe.
"""
import datetime
import threading
import time

from .config import DIGEST_ENABLED, DIGEST_INTERVAL_DAYS
from .db import get_conn, log_ingest
from . import notify
from .audit import coverage_report

_started = False


def _last_digest_at():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT created_at FROM notifications WHERE kind = 'digest' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row["created_at"] if row else None


def build_digest() -> dict:
    """Compose the digest payload from a fresh coverage audit (last 7 days)."""
    rep = coverage_report(blind_limit=5, days=DIGEST_INTERVAL_DAYS)
    s = rep["stats"]
    lines = [
        f"Score {rep['score']}/100 · {s['answers']} answers this week",
        f"{s['answers_no_source']} had no source page",
        f"{s['facts_pending']} fact(s) awaiting review · {s['facts_active']} active",
    ]
    if rep["blind_spots"]:
        top = rep["blind_spots"][0]
        lines.append(f"Most-missed: \"{top['question'][:60]}\" (×{top['count']})")
    if rep["gaps"]:
        lines.append(f"Top gap: {rep['gaps'][0]['title']} — {rep['gaps'][0]['detail']}")
    body = " · ".join(lines)
    level = "warn" if s["answers_no_source"] or s["facts_pending"] else "info"
    return {"report": rep, "title": "Weekly knowledge digest", "body": body, "level": level}


def run_now(force: bool = False) -> dict:
    """Build + emit a digest. force=True ignores the interval (manual trigger)."""
    last = _last_digest_at()
    if not force and last is not None:
        age = datetime.datetime.now(datetime.timezone.utc) - last
        if age < datetime.timedelta(days=DIGEST_INTERVAL_DAYS):
            return {"emitted": False, "reason": "not due yet",
                    "next_in_days": DIGEST_INTERVAL_DAYS - age.days}
    d = build_digest()
    notify.emit("digest", d["title"], d["body"], d["level"])
    log_ingest(None, "digest", "📊 weekly digest posted")
    return {"emitted": True, "title": d["title"], "body": d["body"],
            "report": d["report"]}


def latest() -> dict | None:
    """Most recent digest notification (for the audit UI banner)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title, body, level, created_at FROM notifications "
            "WHERE kind = 'digest' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {"title": row["title"], "body": row["body"], "level": row["level"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else ""}


def _loop():
    # check hourly; emit when the interval has elapsed since the last digest
    while True:
        try:
            run_now(force=False)
        except Exception as e:
            print(f"[digest] loop skipped: {e!r}")
        time.sleep(3600)


def start() -> None:
    """Launch the cadence thread once (called from app lifespan)."""
    global _started
    if _started or not DIGEST_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, name="digest-cadence", daemon=True).start()
    print(f"[digest] cadence on — every {DIGEST_INTERVAL_DAYS}d")
