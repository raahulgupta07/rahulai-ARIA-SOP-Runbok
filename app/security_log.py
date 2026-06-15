"""Tiny append-only logger for auth/security events. Fail-soft — a logging
failure must never block a login or a user-management action."""
import json

from .db import get_conn


def log_event(event: str, email: str | None = None,
              actor_email: str | None = None, meta: dict | None = None) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO auth_event (event, email, actor_email, meta) "
                "VALUES (%s,%s,%s,%s::jsonb)",
                (event, email, actor_email, json.dumps(meta or {})),
            )
    except Exception as e:
        print(f"[security] log skipped: {e!r}")


def recent_fail_count(email: str, minutes: int) -> int:
    """How many failed logins for this email in the last `minutes` (since the last
    successful login, so a good login resets the counter). Fail-soft → 0."""
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT count(*) AS n FROM auth_event "
                "WHERE event = 'login_fail' AND lower(email) = lower(%s) "
                "  AND created_at > now() - (%s||' minutes')::interval "
                "  AND created_at > coalesce(("
                "      SELECT max(created_at) FROM auth_event "
                "      WHERE event = 'login_ok' AND lower(email) = lower(%s)), '-infinity')",
                (email, minutes, email),
            ).fetchone()
        return int((r or {}).get("n") or 0)
    except Exception:
        return 0
