"""Self-learning memory: facts the user teaches via chat, recalled on every ask.

Two ways to 'train' DocSensei:
  1. Upload more documents  (/upload)  -> instantly retrievable
  2. Teach a fact in chat   (/memory)  -> injected into every answer

Review gate (Intern Rule): hand-taught facts are 'active' at once; facts the
agent learns mid-chat land 'pending' and do NOT influence answers until an
admin approves them. Freshness: cited_count / last_cited_at record when a fact
actually shaped an answer, so the Coverage Audit can flag stale, never-used ones.
"""
import re

from .db import get_conn

_STOP = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and",
         "or", "be", "at", "by", "it", "this", "that", "should", "will", "with"}


def norm_fact(value: str) -> str:
    """Normalized dedupe key: lowercase, strip punctuation + common stopwords,
    collapse whitespace. Two facts with the same key are treated as duplicates."""
    s = re.sub(r"[^a-z0-9က-႟\s]", " ", (value or "").lower())
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks).strip()


# trigram similarity threshold — catches LLM rephrases ("every month" vs "each
# month") without embeddings (app is vectorless; pg_trgm is already installed).
_SIM = 0.55


def find_similar(value: str) -> dict | None:
    """Return an existing fact that's a duplicate of `value`:
      1. exact (case-insensitive) match, OR
      2. same normalized dedupe_key, OR
      3. trigram similarity above _SIM (fuzzy — catches reworded duplicates).
    None if nothing close exists."""
    key = norm_fact(value)
    v = value or ""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, value, status, similarity(value, %(v)s) AS sim FROM memory "
            "WHERE lower(value) = lower(%(v)s) "
            "   OR (dedupe_key IS NOT NULL AND dedupe_key = %(k)s) "
            "   OR similarity(value, %(v)s) > %(t)s "
            "ORDER BY sim DESC NULLS LAST LIMIT 1",
            {"v": v, "k": key, "t": _SIM},
        ).fetchone()
    return row


def add_memory(value: str, key: str | None = None,
               source: str = "human", created_by: str | None = None,
               status: str = "active",
               origin_question: str | None = None,
               confidence: float | None = None, auto: bool = False) -> int:
    """Store a fact. source='human' (taught via UI) or 'chat' (agent learned
    it mid-conversation). created_by = who/which user it came from. status:
    'active' (counts in answers) or 'pending' (awaiting review).
    origin_question: the chat/feedback message that produced this fact.
    confidence/auto: set by Layer-B auto-extraction (confidence 0-1, auto=True)."""
    value = (value or "").strip()
    dkey = norm_fact(value)
    # dedup: skip exact / same-key / trigram-similar (reworded) duplicates
    dup = find_similar(value)
    if dup:
        return dup["id"]
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO memory (key, value, source, created_by, status, origin_question, "
            "confidence, auto, dedupe_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (key, value, source, created_by, status, origin_question, confidence, auto, dkey),
        ).fetchone()
    return row["id"]


def list_memory(limit: int = 100, status: str | None = None) -> list[dict]:
    where = "WHERE status = %s" if status else ""
    params: tuple = (status, limit) if status else (limit,)
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, key, value, source, created_by, status, "
            "cited_count, last_cited_at, created_at, origin_question, "
            "confidence, auto FROM memory "
            f"{where} ORDER BY id DESC LIMIT %s",
            params,
        ).fetchall()


def pending_count() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM memory WHERE status = 'pending'"
        ).fetchone()["n"]


def set_status(mem_id: int, status: str) -> bool:
    """Approve ('active') or reject ('rejected') a fact."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE memory SET status = %s WHERE id = %s RETURNING id",
            (status, mem_id),
        ).fetchone()
    return bool(row)


def update_memory(mem_id: int, key: str | None, value: str) -> bool:
    """Edit a fact's key/value in place. Returns False if id missing or value empty."""
    value = (value or "").strip()
    if not value:
        return False
    key = (key or "").strip() or None
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE memory SET key = %s, value = %s WHERE id = %s RETURNING id",
            (key, value, mem_id),
        ).fetchone()
    return bool(row)


def memory_facts(limit: int = 50) -> list[dict]:
    """Active facts for prompt injection: [{id, text}]. Only 'active' status —
    pending/rejected facts never reach the model."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, key, value FROM memory WHERE status = 'active' "
            "ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    return [
        {"id": r["id"],
         "text": f"{r['key']}: {r['value']}" if r["key"] else r["value"]}
        for r in rows
    ]


def touch_used_facts(answer: str, question: str | None = None,
                     conversation_id: str | None = None) -> None:
    """Bump cited_count/last_cited_at for active facts whose wording shows up in
    the answer — a cheap 'this fact shaped the reply' signal for staleness. Also
    inserts one fact_citation row per hit for the provenance log. Never raises
    (post-turn, must not break the request)."""
    text = (answer or "").lower()
    if not text:
        return
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, key, value FROM memory WHERE status = 'active'"
            ).fetchall()
            hits = []
            for r in rows:
                # distinctive needle: the key, else the first ~6 words of value
                needle = (r["key"] or " ".join((r["value"] or "").split()[:6])).strip().lower()
                if len(needle) >= 4 and needle in text:
                    hits.append(r["id"])
            if hits:
                conn.execute(
                    "UPDATE memory SET cited_count = cited_count + 1, "
                    "last_cited_at = now() WHERE id = ANY(%s)",
                    (hits,),
                )
                # record one citation-log row per hit
                for fid in hits:
                    try:
                        conn.execute(
                            "INSERT INTO fact_citation (fact_id, conversation_id, question) "
                            "VALUES (%s, %s, %s)",
                            (fid, str(conversation_id) if conversation_id is not None else None,
                             (question or None)),
                        )
                    except Exception as ie:
                        print(f"[memory] fact_citation insert skipped for fact {fid}: {ie!r}")
    except Exception as e:
        print(f"[memory] touch_used_facts skipped: {e!r}")
