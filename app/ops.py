"""System / operations health (admin-tracking hole #8). Pure reads, no LLM.

Surfaces the reliability signals an operator needs:
  - ingest    : doc pipeline status mix, failure rate, stuck docs, throughput
  - daemons   : background loops — enabled?, last activity, stale?
  - storage   : DB size + biggest tables (growth watch)
Everything is best-effort + fail-soft; a missing table never breaks the report."""
import os

from .db import get_conn

# stuck = a doc that's been processing/queued longer than this (minutes)
STUCK_MIN = int(os.getenv("INGEST_STUCK_MIN", "30"))
# a daemon is "stale" if its last activity is older than this (hours)
STALE_HRS = int(os.getenv("DAEMON_STALE_HRS", "48"))


def _one(sql, params=()):
    try:
        with get_conn() as conn:
            return conn.execute(sql, params).fetchone()
    except Exception:
        return None


def _scalar(sql, params=()):
    r = _one(sql, params)
    if not r:
        return None
    return list(dict(r).values())[0]


def _ingest() -> dict:
    with get_conn() as conn:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT COALESCE(status,'ready') AS status, count(*) AS n FROM docs GROUP BY 1"
        ).fetchall()}
        total = sum(by_status.values())
        failed = [dict(r) for r in conn.execute(
            "SELECT id, name, error, "
            " to_char(created_at,'MM-DD HH24:MI') AS at, "
            " EXTRACT(EPOCH FROM now()-created_at)/3600 AS age_h "
            "FROM docs WHERE status = 'failed' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()]
        stuck = [dict(r) for r in conn.execute(
            "SELECT id, name, status, progress, "
            " EXTRACT(EPOCH FROM now()-created_at)/60 AS age_min "
            "FROM docs WHERE status IN ('queued','processing') "
            "  AND created_at < now() - make_interval(mins => %s) "
            "ORDER BY created_at ASC LIMIT 20", (STUCK_MIN,),
        ).fetchall()]
        thru = conn.execute(
            "SELECT COALESCE(round(avg(EXTRACT(EPOCH FROM ready_at-created_at))),0) AS avg_sec, "
            " count(*) AS n FROM docs WHERE ready_at IS NOT NULL AND status='ready'"
        ).fetchone()
    fail_n = by_status.get("failed", 0)
    queue = by_status.get("queued", 0) + by_status.get("processing", 0)
    return {
        "by_status": by_status,
        "total": total,
        "fail_rate": round(100 * fail_n / total) if total else 0,
        "failed": [{**f, "age_h": round(f["age_h"] or 0, 1)} for f in failed],
        "stuck": [{**s, "age_min": round(s["age_min"] or 0)} for s in stuck],
        "queue_depth": queue,
        "avg_ingest_sec": int((thru or {}).get("avg_sec") or 0),
        "ingested": (thru or {}).get("n", 0) or 0,
    }


def _daemons() -> list[dict]:
    """Each background loop: is it enabled, when did it last do something, stale?"""
    def env_on(k, default="0"):
        return os.getenv(k, default) == "1"

    rows = [
        # name, enabled, last-activity SQL (single ts column)
        ("ingest worker", True,
         "SELECT max(ts) AS t FROM ingest_log"),
        ("file watcher", True,
         "SELECT max(created_at) AS t FROM docs"),
        ("weekly digest", env_on("DIGEST_ENABLED", "1"),
         "SELECT max(created_at) AS t FROM notifications WHERE kind='digest'"),
        ("usage rollup", env_on("USAGE_ROLLUP_ENABLED"),
         "SELECT max(day)::timestamptz AS t FROM usage_daily"),
        ("citation verify", env_on("VERIFY_ENABLED"),
         "SELECT max(created_at) AS t FROM answer_verify"),
    ]
    out = []
    for name, enabled, sql in rows:
        last = _scalar(sql)
        stale = None
        last_iso = None
        if last is not None:
            try:
                last_iso = last.isoformat()
                age_h = _scalar(
                    "SELECT EXTRACT(EPOCH FROM now()-%s)/3600", (last,))
                stale = bool(enabled and age_h is not None and age_h > STALE_HRS)
            except Exception:
                pass
        out.append({
            "name": name, "enabled": enabled,
            "last_activity": last_iso, "stale": stale,
        })
    return out


def _storage() -> dict:
    db_bytes = _scalar("SELECT pg_database_size(current_database())") or 0
    tables = []
    try:
        with get_conn() as conn:
            tables = [dict(r) for r in conn.execute(
                "SELECT relname AS table, "
                " pg_total_relation_size(c.oid) AS bytes, "
                " COALESCE(c.reltuples,0)::bigint AS rows "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind='r' AND n.nspname='public' "
                "ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 12"
            ).fetchall()]
    except Exception:
        pass
    return {"db_bytes": int(db_bytes), "tables": tables}


def report() -> dict:
    return {
        "ingest": _ingest(),
        "daemons": _daemons(),
        "storage": _storage(),
        "thresholds": {"stuck_min": STUCK_MIN, "stale_hrs": STALE_HRS},
    }
