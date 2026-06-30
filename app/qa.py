"""Q&A bank — grounded question/answer pairs that grow the agent's training set.

Phase 1: pairs are mined from documents at ingest (source='qa', see doc_qa.py).
Later phases harvest them from real sourced+upvoted chat turns (source='chat')
and fill demand/coverage gaps (a daily daemon).

Same review gate as facts (app/governance.py): a new pair lands 'active' if its
confidence clears the floor for its source, else 'pending' for an admin to approve.
Dedup is by normalized question (exact / same key / trigram) so the same question
mined from two docs collapses to one pair — mirrors memory.add_memory.
"""
import json
import re

from .db import get_conn

_STOP = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and",
         "or", "be", "at", "by", "it", "this", "that", "should", "will", "with",
         "what", "how", "where", "when", "which", "who", "why", "do", "does"}


def norm_q(question: str) -> str:
    """Normalized dedupe key for a question: lowercase, strip punctuation +
    question-framing stopwords, collapse whitespace."""
    s = re.sub(r"[^a-z0-9က-႟\s]", " ", (question or "").lower())
    toks = [t for t in s.split() if t not in _STOP]
    return " ".join(toks).strip()


# trigram threshold — catches reworded questions without embeddings (vectorless app)
_SIM = 0.55


def find_similar_qa(question: str) -> dict | None:
    """Return an existing Q&A whose QUESTION duplicates `question`:
    exact (case-insensitive) OR same dedupe_key OR trigram-similar. None if novel."""
    key = norm_q(question)
    q = question or ""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, question, status, similarity(question, %(q)s) AS sim FROM qa_pairs "
            "WHERE lower(question) = lower(%(q)s) "
            "   OR (dedupe_key IS NOT NULL AND dedupe_key = %(k)s) "
            "   OR similarity(question, %(q)s) > %(t)s "
            "ORDER BY sim DESC NULLS LAST LIMIT 1",
            {"q": q, "k": key, "t": _SIM},
        ).fetchone()


def add_qa(question: str, answer: str, source: str = "qa",
           status: str = "pending", confidence: float | None = None,
           doc_id: int | None = None, page_ids: list | None = None,
           created_by: str | None = None, origin: str | None = None) -> int:
    """Insert a Q&A pair (deduped by question). Returns the id (existing on dup)."""
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return 0
    dup = find_similar_qa(question)
    if dup:
        return dup["id"]
    dkey = norm_q(question)
    pj = json.dumps(page_ids or [])
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO qa_pairs (question, answer, source, status, confidence, "
            "doc_id, page_ids, created_by, origin, dedupe_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING id",
            (question, answer, source, status, confidence, doc_id, pj,
             created_by, origin, dkey),
        ).fetchone()
    return row["id"]


def serve_match(question: str, min_sim: float = 0.72,
                min_len: int = 0, sectors: list[int] | None = None,
                folders: list[int] | None = None) -> dict | None:
    """Best ACTIVE pair whose question is near-identical to `question` (trigram).
    Used to serve a cached answer with zero agent. Conservative by threshold —
    only approved pairs, only very close phrasing, only answers with enough
    substance (`min_len`). Returns the pair + sim, or None."""
    q = (question or "").strip()
    if len(q) < 6:
        return None
    # Vague-deflection stubs ("inform your superior; do not attempt to resolve it
    # alone; contact/escalate to ...") are non-answers when they're the WHOLE reply.
    # Skip them under ~200 chars so the question falls through to the live agent,
    # which can read the page and give a real procedure. (A long answer that merely
    # mentions escalation as one step is fine — hence the length ceiling.)
    deflect = (r"(do not attempt to resolve|inform your superior|"
               r"request assistance|escalate to|contact your (supervisor|superior|manager|it team))")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT qp.id, qp.question, qp.answer, qp.page_ids, qp.confidence, qp.source, "
            "similarity(qp.question, %(q)s) AS sim FROM qa_pairs qp "
            # row-level access: a banked answer may only be served to a user whose
            # sector owns the source doc (NULL sectors = no filter / single-tenant).
            "LEFT JOIN docs d ON d.id = qp.doc_id "
            "WHERE qp.status = 'active' AND similarity(qp.question, %(q)s) >= %(t)s "
            "AND length(btrim(qp.answer)) >= %(ml)s "
            "AND NOT (qp.answer ~* %(df)s AND length(btrim(qp.answer)) < 200) "
            # never serve an ORPHANED pair: if it was mined from a doc, that doc must
            # still exist (qa_pairs has no FK cascade → deleted docs leave stale pairs
            # with dangling page_ids that render 'unsourced' and may be hallucinated).
            "AND (qp.doc_id IS NULL OR d.id IS NOT NULL) "
            "AND (%(folders)s::bigint[] IS NULL OR d.folder_id = ANY(%(folders)s) "
            "     OR (d.folder_id IS NULL AND d.sector_id = ANY(%(sectors)s))) "
            "ORDER BY sim DESC LIMIT 1",
            {"q": q, "t": min_sim, "ml": int(min_len), "df": deflect,
             "sectors": sectors, "folders": folders},
        ).fetchone()
    return row


def sibling_questions(qa_id: int, limit: int = 3) -> list[str]:
    """Other active questions from the SAME document as `qa_id` — zero-LLM follow-ups
    for a cache-served answer. Returns [] if the pair has no doc or no siblings."""
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT doc_id FROM qa_pairs WHERE id = %s", (qa_id,)).fetchone()
            if not row or row["doc_id"] is None:
                return []
            rows = conn.execute(
                "SELECT question FROM qa_pairs WHERE doc_id = %s AND id <> %s "
                "AND status = 'active' AND length(question) BETWEEN 8 AND 100 "
                "ORDER BY cited_count DESC, created_at DESC LIMIT %s",
                (row["doc_id"], qa_id, limit),
            ).fetchall()
        return [r["question"] for r in rows]
    except Exception:
        return []


def bump_served(qa_id: int) -> None:
    """Freshness: record that a pair served an answer (cited_count / last_cited_at)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE qa_pairs SET cited_count = cited_count + 1, last_cited_at = now() "
                "WHERE id = %s", (qa_id,),
            )
    except Exception as e:
        print(f"[qa] bump_served skipped: {e!r}")


def list_qa(limit: int = 100, status: str | None = None) -> list[dict]:
    where = "WHERE status = %s" if status else ""
    params: tuple = (status, limit) if status else (limit,)
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, question, answer, source, status, confidence, doc_id, "
            "page_ids, created_by, origin, cited_count, last_cited_at, created_at "
            f"FROM qa_pairs {where} ORDER BY id DESC LIMIT %s",
            params,
        ).fetchall()


def pending_qa_count() -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT count(*) AS n FROM qa_pairs WHERE status = 'pending'"
        ).fetchone()["n"]


def qa_counts() -> dict:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, count(*) AS n FROM qa_pairs GROUP BY status"
        ).fetchall()
    out = {"active": 0, "pending": 0, "rejected": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    return out


def set_qa_status(qa_id: int, status: str) -> bool:
    """Approve ('active') or reject ('rejected') a Q&A pair."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE qa_pairs SET status = %s WHERE id = %s RETURNING id",
            (status, qa_id),
        ).fetchone()
    return bool(row)


def update_qa(qa_id: int, question: str | None, answer: str | None) -> bool:
    """Edit a pair's question/answer in place. Refreshes the dedupe key."""
    sets, params = [], []
    if question is not None and question.strip():
        sets += ["question = %s", "dedupe_key = %s"]
        params += [question.strip(), norm_q(question)]
    if answer is not None and answer.strip():
        sets.append("answer = %s")
        params.append(answer.strip())
    if not sets:
        return False
    params.append(qa_id)
    with get_conn() as conn:
        row = conn.execute(
            f"UPDATE qa_pairs SET {', '.join(sets)} WHERE id = %s RETURNING id",
            tuple(params),
        ).fetchone()
    return bool(row)


def delete_qa(qa_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "DELETE FROM qa_pairs WHERE id = %s RETURNING id", (qa_id,)
        ).fetchone()
    return bool(row)
