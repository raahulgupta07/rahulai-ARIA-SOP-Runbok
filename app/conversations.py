"""Per-user chat conversations + messages. Conversation id doubles as the
Agno session_id so the agent's own history stays aligned with what we store
for display. Titles are LLM-generated (fail-soft to the first question)."""
import json

from .db import get_conn
from .config import OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


# ---------- conversations ----------
def list_for_user(user_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations "
            "WHERE user_id = %s ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()


def create(user_id: int, title: str = "New chat") -> dict:
    with get_conn() as conn:
        return conn.execute(
            "INSERT INTO conversations (user_id, title) VALUES (%s, %s) "
            "RETURNING id, title, created_at, updated_at",
            (user_id, title),
        ).fetchone()


def owned(conv_id: int, user_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM conversations WHERE id = %s AND user_id = %s",
            (conv_id, user_id),
        ).fetchone()


def messages(conv_id: int) -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, role, text, pages, meta, created_at FROM messages "
            "WHERE conversation_id = %s ORDER BY id",
            (conv_id,),
        ).fetchall()


def rename(conv_id: int, user_id: int, title: str) -> dict | None:
    with get_conn() as conn:
        return conn.execute(
            "UPDATE conversations SET title = %s, updated_at = now() "
            "WHERE id = %s AND user_id = %s "
            "RETURNING id, title, created_at, updated_at",
            (title, conv_id, user_id),
        ).fetchone()


def delete(conv_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
            (conv_id, user_id),
        ).fetchone()
    return bool(row)


# ---------- messages ----------
def add_message(conv_id: int, role: str, text: str, pages: list | None = None,
                meta: dict | None = None) -> int | None:
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO messages (conversation_id, role, text, pages, meta) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (conv_id, role, text, json.dumps(pages or []), json.dumps(meta or {})),
        ).fetchone()
        conn.execute(
            "UPDATE conversations SET updated_at = now() WHERE id = %s", (conv_id,)
        )
        return row["id"] if row else None


def message_count(conv_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM messages WHERE conversation_id = %s", (conv_id,)
        ).fetchone()["n"]


# ---------- LLM title ----------
def _truncate_title(q: str) -> str:
    q = (q or "").strip().replace("\n", " ")
    return (q[:40] + "…") if len(q) > 40 else (q or "New chat")


def autotitle(conv_id: int, first_q: str) -> str:
    """Generate a short title from the first question; fail-soft to truncation."""
    title = _truncate_title(first_q)
    if _client:
        try:
            r = _client.chat.completions.create(
                model=CHAT_MODEL,
                messages=[{
                    "role": "user",
                    "content": (
                        "Give a 3-5 word title (no quotes, no period) for a chat that "
                        f"starts with this question. Reply in the question's language.\n\n{first_q}"
                    ),
                }],
                max_tokens=20,
                temperature=0.2,
            )
            cand = (r.choices[0].message.content or "").strip().strip('"').strip()
            if cand:
                title = cand[:60]
        except Exception:
            pass
    with get_conn() as conn:
        conn.execute(
            "UPDATE conversations SET title = %s WHERE id = %s", (title, conv_id)
        )
    return title
