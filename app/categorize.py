"""Auto-category — one-time LLM topic tag per document (stored in docs.category).

Picks ONE short topic category for a doc from its title + compiled summary, and
reuses an existing category whenever the doc fits one (so the set stays small and
consistent). Runs once at ingest (gated AUTO_CATEGORIZE) + a backfill script.
Fail-soft: any error leaves category NULL → the UI falls back to filename grouping.
"""
import re

from .db import get_conn
from .config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You assign ONE short topic category to an IT SOP / runbook.\n"
    "RULES:\n"
    "- Reply with ONLY the category — 1-3 words, Title Case "
    "(e.g. 'Batch Jobs', 'User Admin', 'Inventory', 'Ecommerce', 'POS', 'Reporting').\n"
    "- If the document clearly fits one of the EXISTING categories, reuse it verbatim.\n"
    "- Otherwise create a concise new one.\n"
    "- No punctuation, no quotes, no sentence — just the category."
)


def _existing() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM docs "
            "WHERE coalesce(category,'') <> '' ORDER BY category"
        ).fetchall()
    return [r["category"] for r in rows]


def _clean(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.splitlines()[0].strip().strip('"\'')
    s = re.sub(r"[^\w &/+-]", "", s).strip()
    return s[:40]


def categorize_doc(doc_id: int) -> str | None:
    """LLM-tag one doc with a topic category and store it. Returns the category or None."""
    if not _client:
        return None
    with get_conn() as conn:
        d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
        if not d:
            return None
        w = conn.execute(
            "SELECT title, summary FROM doc_wiki WHERE doc_id = %s", (doc_id,)
        ).fetchone()
    title = (w and w.get("title")) or d["name"]
    summary = (w and w.get("summary")) or ""
    ctx = f"Title: {title}\nSummary: {summary[:1200]}"
    existing = _existing()
    if existing:
        ctx += "\n\nEXISTING categories: " + ", ".join(existing)
    try:
        r = _client.chat.completions.create(
            model=COMPILE_MODEL,
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": ctx}],
            max_tokens=16,
            temperature=0,
        )
        cat = _clean(r.choices[0].message.content or "")
    except Exception as e:
        print(f"[categorize] doc {doc_id} failed: {e!r}")
        return None
    if not cat:
        return None
    with get_conn() as conn:
        conn.execute("UPDATE docs SET category = %s WHERE id = %s", (cat, doc_id))
    return cat


def categorize_all(force: bool = False) -> dict:
    """Backfill: categorize every ready doc (force=re-tag ones already categorized)."""
    with get_conn() as conn:
        if force:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM docs WHERE status = 'ready' ORDER BY id").fetchall()]
        else:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM docs WHERE status = 'ready' "
                "AND coalesce(category,'') = '' ORDER BY id").fetchall()]
    done = {}
    for i in ids:
        c = categorize_doc(i)
        if c:
            done[i] = c
    return {"categorized": len(done), "total": len(ids), "map": done}
