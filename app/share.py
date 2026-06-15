"""Shareable answer permalinks — a read-only link to ONE bot answer (question +
answer + citations). Opt-in per answer; revocable. Login-required to view by
default (stays internal); set SHARE_PUBLIC_ENABLED=1 for anonymous public view.
"""
import secrets

from .db import get_conn


def create(message_id: int, created_by: str | None) -> str:
    """Mint (or reuse) a share token for a bot answer. Returns the token."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT token FROM shares WHERE message_id = %s AND revoked = false LIMIT 1",
            (message_id,),
        ).fetchone()
        if existing:
            return existing["token"]
        token = secrets.token_urlsafe(8)
        conn.execute(
            "INSERT INTO shares (token, message_id, created_by) VALUES (%s,%s,%s)",
            (token, message_id, created_by),
        )
    return token


def revoke(token: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE shares SET revoked = true WHERE token = %s", (token,))


def get(token: str) -> dict | None:
    """Return the shared answer payload, or None if missing/revoked."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.id, m.text AS answer, m.pages, m.created_at, "
            " (SELECT u.text FROM messages u WHERE u.conversation_id = m.conversation_id "
            "  AND u.role = 'user' AND u.id < m.id ORDER BY u.id DESC LIMIT 1) AS question "
            "FROM messages m JOIN shares s ON s.message_id = m.id "
            "WHERE s.token = %s AND s.revoked = false",
            (token,),
        ).fetchone()
    if not row:
        return None
    return {
        "question": row["question"] or "",
        "answer": row["answer"] or "",
        "pages": row["pages"] or [],
        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
    }
