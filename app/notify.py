"""Activity-feed notifications. Fail-soft emit + simple queries.

A notification failure must never break the calling request, so emit() swallows
all exceptions. Reads use the same psycopg3 get_conn() (dict_row, autocommit).
"""
from .db import get_conn


def emit(kind: str, title: str, body: str = "", level: str = "info",
         dedupe_hours: float = 0) -> None:
    """Insert a notification row. Never raises.

    dedupe_hours > 0 → skip if an identical (kind, title, body) notification was
    already emitted within that window (quiets repeat blind-spots / verify flags).
    """
    try:
        with get_conn() as conn:
            if dedupe_hours and dedupe_hours > 0:
                dup = conn.execute(
                    "SELECT 1 FROM notifications "
                    "WHERE kind = %s AND title = %s AND body = %s "
                    "AND created_at > now() - (%s || ' hours')::interval LIMIT 1",
                    (kind, title, body or "", str(dedupe_hours)),
                ).fetchone()
                if dup:
                    return
            conn.execute(
                "INSERT INTO notifications (kind, title, body, level) "
                "VALUES (%s, %s, %s, %s)",
                (kind, title, body or "", level or "info"),
            )
    except Exception:
        pass
    # escalate warn/error to the external alert sink (webhook, if configured)
    if (level or "info") in ("warn", "error"):
        try:
            from .alert import maybe_push
            maybe_push(kind, title, body or "", level)
        except Exception:
            pass


def list_notifications(filter: str = "all") -> dict:
    where = ""
    if filter == "unread":
        where = "WHERE read = false"
    elif filter == "alerts":
        where = "WHERE level IN ('warn', 'alert')"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, kind, title, body, level, read, created_at "
            f"FROM notifications {where} "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        unread = conn.execute(
            "SELECT count(*) AS n FROM notifications WHERE read = false"
        ).fetchone()["n"]
    items = []
    for r in rows:
        ca = r["created_at"]
        items.append(
            {
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "body": r["body"],
                "level": r["level"],
                "read": r["read"],
                "created_at": ca.isoformat() if ca is not None else "",
            }
        )
    return {"items": items, "unread": unread}


def mark_all_read() -> None:
    with get_conn() as conn:
        conn.execute("UPDATE notifications SET read = true WHERE read = false")
