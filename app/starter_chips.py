"""LLM-curated bilingual home starter chips (Phase 2 of the chip system).

A refresh job builds a GROUNDED candidate pool from the Q&A bank (one best, real
question per document) + a filename-derived topic for any doc without Q&A, then
makes ONE LLM call to: pick the most useful, distinct starters, clean the English,
and translate each to Burmese. Results land in the `starter_chips` table, which
`/api/suggestions` reads with zero LLM at request time.

The LLM only CHOOSES + TRANSLATES from real questions — it never invents content,
so chips stay grounded. Fail-soft everywhere: any failure leaves the previous
chips intact (or falls through to the live DB logic in routes.suggestions).
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
)

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

# how many chips to curate + keep (home shows the first 4)
TARGET = 8

# deflection-style answers make poor chips (non-answers) — mirror qa.serve_match
_DEFLECT = re.compile(
    r"(do not attempt to resolve|inform your superior|request assistance|"
    r"escalate to|contact your (supervisor|superior|manager|it team))",
    re.IGNORECASE,
)


def _doc_topic(name: str) -> str:
    """Clean an SOP filename into a human topic (mirrors routes._doc_topic)."""
    t = name or ""
    t = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", t)
    t = re.sub(r"^[0-9a-f]{6,}__", "", t)
    t = re.sub(r"^SOP[_\s]+(?:[A-Z]+[_\s]+){0,6}\d+[_\s]+", "", t)
    t = re.sub(r"[_]+", " ", t).strip()
    return re.sub(r"\s+", " ", t)


def _candidate_pool() -> list[dict]:
    """At most one real question per document — best proven Q&A, else a doc topic."""
    pool: list[dict] = []
    seen: set = set()
    try:
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT q.question AS q, q.answer AS a, q.doc_id AS doc_id, "
                "COALESCE(NULLIF(d.category,''), 'DOCS') AS cat "
                "FROM qa_pairs q LEFT JOIN docs d ON d.id = q.doc_id "
                "WHERE q.status='active' AND length(q.question) BETWEEN 8 AND 90 "
                "AND q.question !~* '(this sop|this document|this runbook|this procedure|purpose of)' "
                "ORDER BY q.cited_count DESC, q.last_cited_at DESC NULLS LAST, q.created_at DESC "
                "LIMIT 200"
            ).fetchall():
                if r["doc_id"] in seen:
                    continue
                # skip a pure-deflection answer's question (reads as a non-answer chip)
                if _DEFLECT.search(r["a"] or "") and len((r["a"] or "").strip()) < 200:
                    continue
                seen.add(r["doc_id"])
                pool.append({"q": r["q"], "cat": r["cat"], "doc_id": r["doc_id"]})
            for r in conn.execute(
                "SELECT id AS doc_id, name, COALESCE(NULLIF(category,''),'DOCS') AS cat "
                "FROM docs WHERE status='ready' ORDER BY id"
            ).fetchall():
                if r["doc_id"] in seen:
                    continue
                seen.add(r["doc_id"])
                topic = _doc_topic(r["name"])
                if 3 <= len(topic) <= 60:
                    pool.append({"q": f"What is the procedure for {topic}?",
                                 "cat": r["cat"], "doc_id": r["doc_id"]})
    except Exception as e:
        print(f"[starter_chips] pool build failed: {e!r}")
    return pool


_SYS = (
    "You curate the home-screen starter questions for a bilingual (English + Burmese) "
    "IT runbook assistant. You are given candidate questions, each tied to a document "
    "and category.\n"
    "TASK: choose the {n} MOST USEFUL and DISTINCT questions a new user would ask, "
    "spread across different documents and categories. For each chosen question:\n"
    "- Keep it grounded — only pick from the candidates, do NOT invent new topics.\n"
    "- Clean the English phrasing into a natural, concise question.\n"
    "- Translate it accurately to Burmese (my).\n"
    "- Keep its doc_id and a SHORT category label (<=12 chars, uppercase).\n"
    'Reply ONLY as JSON: {{"chips":[{{"q_en":"...","q_my":"...","cat":"...","doc_id":N}}]}}'
)


def _curate_llm(pool: list[dict]) -> list[dict]:
    """One LLM call → curated bilingual chips. Returns [] on any failure."""
    if not _client or not pool:
        return []
    cand = [{"q": p["q"], "cat": p["cat"], "doc_id": p["doc_id"]} for p in pool[:40]]
    try:
        resp = _client.chat.completions.create(
            model=COMPILE_MODEL or CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYS.format(n=TARGET)},
                {"role": "user", "content": "CANDIDATES:\n" + json.dumps(cand, ensure_ascii=False)},
            ],
            max_tokens=1600, temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
        chips = json.loads(raw).get("chips", []) or []
    except Exception as e:
        print(f"[starter_chips] LLM curate failed: {e!r}")
        return []
    valid_docs = {p["doc_id"] for p in pool}
    out = []
    for c in chips[:TARGET]:
        if not isinstance(c, dict):
            continue
        q_en = (c.get("q_en") or "").strip()
        if len(q_en) < 6:
            continue
        did = c.get("doc_id")
        out.append({
            "q_en": q_en,
            "q_my": (c.get("q_my") or "").strip() or None,
            "cat": ((c.get("cat") or "DOCS").upper()[:12]).strip(),
            "doc_id": did if did in valid_docs else None,
        })
    return out


def _store(chips: list[dict]) -> int:
    """Replace the starter_chips table with the curated set. Returns count."""
    if not chips:
        return 0
    with get_conn() as conn:
        conn.execute("DELETE FROM starter_chips")
        for i, c in enumerate(chips):
            conn.execute(
                "INSERT INTO starter_chips (rank, q_en, q_my, cat, doc_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (i, c["q_en"], c.get("q_my"), c.get("cat"), c.get("doc_id")),
            )
    return len(chips)


def refresh() -> int:
    """Rebuild the curated bilingual chips. Fail-soft: leaves old chips on failure."""
    pool = _candidate_pool()
    if not pool:
        return 0
    chips = _curate_llm(pool)
    if not chips:
        # LLM unavailable/failed → store the grounded pool as EN-only (no Burmese)
        chips = [{"q_en": p["q"], "q_my": None, "cat": (p["cat"] or "DOCS").upper()[:12],
                  "doc_id": p["doc_id"]} for p in pool[:TARGET]]
    return _store(chips)


def read(lang: str = "en", limit: int = 4) -> list[dict]:
    """Read curated chips for the home hero. lang 'my' → Burmese (falls back to EN).
    Returns [] when the table is empty (caller falls through to live DB logic)."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sc.q_en, sc.q_my, sc.cat, sc.doc_id, d.name AS doc_name "
                "FROM starter_chips sc LEFT JOIN docs d ON d.id = sc.doc_id "
                "ORDER BY sc.rank LIMIT %s",
                (limit,),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        q = (r["q_my"] if lang == "my" and r["q_my"] else r["q_en"]) or r["q_en"]
        out.append({
            "cat": (r["cat"] or "DOCS"),
            "q": q,
            "doc": _doc_topic(r["doc_name"]) if r["doc_name"] else None,
        })
    return out
