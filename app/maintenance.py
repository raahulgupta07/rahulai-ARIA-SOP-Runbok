"""DB retention / vacuum — stop churny tables from bloating.

Agno rewrites the whole `agent_sessions.memory` jsonb every conversation turn, so
a handful of rows accumulate massive dead-tuple bloat that autovacuum doesn't
keep up with (observed: 18 rows = 82 MB). We delete sessions idle past a TTL and
VACUUM to reclaim the space. Daemon default OFF (flag DB_MAINTENANCE_ENABLED).
"""
import os
import threading
import time

from .db import get_conn

DB_MAINTENANCE_ENABLED = os.getenv("DB_MAINTENANCE_ENABLED", "0") == "1"
SESSION_TTL_DAYS = int(os.getenv("DB_SESSION_TTL_DAYS", "30"))
INTERVAL_H = int(os.getenv("DB_MAINTENANCE_INTERVAL_H", "24"))


def prune_sessions(ttl_days: int | None = None, vacuum_full: bool = False) -> dict:
    """Delete agent_sessions idle past the TTL, then VACUUM to reclaim space.
    `vacuum_full` (one-off use) returns space to the OS but briefly locks the
    table; the daemon uses a plain VACUUM (no lock, space reused not released)."""
    ttl_days = ttl_days if ttl_days is not None else SESSION_TTL_DAYS
    with get_conn() as conn:  # autocommit → VACUUM is allowed
        deleted = conn.execute(
            "DELETE FROM agent_sessions "
            "WHERE coalesce(updated_at, created_at) < "
            "      (extract(epoch FROM now())::bigint - %s) RETURNING session_id",
            (ttl_days * 86400,),
        ).fetchall()
        conn.execute(f"VACUUM {'FULL ' if vacuum_full else ''}ANALYZE agent_sessions")
    return {"deleted_sessions": len(deleted), "vacuum_full": vacuum_full,
            "ttl_days": ttl_days}


def db_sizes(top: int = 8) -> list[dict]:
    """Largest public tables (name + pretty size) — for ops / before-after."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT relname AS table, "
            "  pg_size_pretty(pg_total_relation_size(c.oid)) AS size, "
            "  pg_total_relation_size(c.oid) AS bytes "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "ORDER BY bytes DESC LIMIT %s", (top,),
        ).fetchall()
    return [dict(r) for r in rows]


def start_daemon() -> None:
    if not DB_MAINTENANCE_ENABLED:
        return

    def _loop():
        time.sleep(120)   # let boot settle
        while True:
            try:
                print(f"[db.maint] {prune_sessions()}")
            except Exception as e:
                print(f"[db.maint] failed: {e!r}")
            time.sleep(max(1, INTERVAL_H) * 3600)

    threading.Thread(target=_loop, daemon=True, name="db-maint").start()
    print("[db.maint] daemon started")
