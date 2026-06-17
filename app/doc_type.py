"""Doc-type classifier — one-time LLM tag of a document's TYPE (stored in docs.doc_type).

Decides which KIND of document this is so the ingest pipeline can route it to the
right compiler. ONE short LLM call over the title + compiled summary (or first
~2000 chars of compiled markdown). Returns exactly one of:
    sop | runbook | policy | reference | other
Fail-soft: any error / no LLM client -> "sop" (the safe default — most docs here
are SOPs). Never raises into the pipeline; the CALLER gates whether to classify.
"""
from .db import get_conn
from .config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_ALLOWED = {"sop", "runbook", "policy", "reference", "other"}
_DEFAULT = "sop"

_SYS = (
    "You classify the TYPE of an IT document. Reply with ONLY one lowercase word "
    "from this exact set: sop, runbook, policy, reference, other.\n"
    "DEFINITIONS:\n"
    "- sop = step-by-step 'how to do a task' instructions (a procedure).\n"
    "- runbook = incident / troubleshooting response: 'X is broken -> diagnose -> "
    "fix -> rollback'.\n"
    "- policy = rules / do's & don'ts / what is allowed — NOT step instructions.\n"
    "- reference = lookup / glossary / config tables (codes, definitions, field "
    "meanings).\n"
    "- other = none of the above.\n"
    "Reply with ONLY the one word — no punctuation, no quotes, no explanation."
)


def _clean(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = s.split()[0].strip().strip('".\'')
    return s


def _source_text(doc_id: int, name: str) -> str:
    """Title + compiled summary if present, else first ~2000 chars of compiled md."""
    with get_conn() as conn:
        if not name:
            d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
            name = (d and d.get("name")) or ""
        w = conn.execute(
            "SELECT summary FROM doc_wiki WHERE doc_id = %s", (doc_id,)
        ).fetchone()
        summary = (w and w.get("summary")) or ""
        if not summary:
            rows = conn.execute(
                "SELECT md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
                (doc_id,),
            ).fetchall()
            summary = " ".join((r.get("md") or "") for r in rows)[:2000]
    return f"Title: {name}\n\n{summary[:2000]}"


def classify_doc_type(doc_id: int, name: str = "") -> str:
    """LLM-classify one doc's type and store it in docs.doc_type. Returns the type.

    Returns one of: sop | runbook | policy | reference | other.
    Fail-soft: no LLM client / any error -> 'sop' (still stored).
    """
    if not _client:
        try:
            with get_conn() as conn:
                conn.execute("UPDATE docs SET doc_type = %s WHERE id = %s", (_DEFAULT, doc_id))
        except Exception as e:
            print(f"[doc_type] doc {doc_id} store-default failed: {e!r}")
        return _DEFAULT
    try:
        ctx = _source_text(doc_id, name)
        r = _client.chat.completions.create(
            model=COMPILE_MODEL,
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content": ctx}],
            max_tokens=4,
            temperature=0,
        )
        t = _clean(r.choices[0].message.content or "")
        if t not in _ALLOWED:
            t = _DEFAULT
    except Exception as e:
        print(f"[doc_type] doc {doc_id} failed: {e!r}")
        t = _DEFAULT
    try:
        with get_conn() as conn:
            conn.execute("UPDATE docs SET doc_type = %s WHERE id = %s", (t, doc_id))
    except Exception as e:
        print(f"[doc_type] doc {doc_id} store failed: {e!r}")
    return t
