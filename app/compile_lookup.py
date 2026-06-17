"""Reference/glossary lookup extraction — flat TERM -> VALUE rows at ingest.

For REFERENCE documents (code lists, field dictionaries, menu/status/abbreviation
tables) the useful answer to "what is code 3001 / what does field X mean" is a
single exact pair, not a procedure. This module runs ONE LLM pass over a doc's
already-compiled page-marked markdown and extracts a flat lookup table of
term -> value pairs, each tagged with the source page, into `doc_lookup` (table
already exists). The agent can then answer exact-lookup questions verbatim.

Mirrors doc_qa.py (one JSON-returning LLM call + page_no -> page_id mapping) and
compile_playbook.py (per-page-capped concat so one monster page can't dominate).
Fail-soft: any error -> print + return 0. Never raises.
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

_SYS = (
    "Extract a flat lookup table of TERM -> VALUE pairs from this reference "
    "document. A term is a code, field name, menu item, abbreviation, status, or "
    "parameter; the value is its meaning/description/number EXACTLY as written. "
    "Keep codes and values verbatim. Reply ONLY as strict JSON: a list like "
    '[{"term": "...", "value": "...", "page": N}]. If the document has no such '
    "lookup content, reply []."
)

# cap any single page's contribution so one degenerate/looped page (e.g. a 100k
# repeated-table compile) can't eat the whole window and starve the real rows.
_PER_PAGE_CAP = 6000


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _doc_text_paged(doc_id: int) -> tuple[str, dict]:
    """Return (page-marked body, {page_no: page_id}).

    Body = doc_pages_md in page order, each page truncated to _PER_PAGE_CAP,
    joined with '## Page N' markers so the model can cite the source page.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
        prows = conn.execute(
            "SELECT id, page_no FROM pages WHERE doc_id = %s", (doc_id,)
        ).fetchall()
    no_to_id = {r["page_no"]: r["id"] for r in prows}
    parts = []
    for r in rows:
        md = (r["md"] or "").strip()
        if not md:
            continue
        if len(md) > _PER_PAGE_CAP:
            md = md[:_PER_PAGE_CAP] + " …[truncated]"
        parts.append(f"## Page {r['page_no']}\n\n{md}")
    return "\n\n".join(parts).strip(), no_to_id


def _parse_json(raw: str) -> list:
    """Robustly parse the model's JSON list: strip ```json fences, tolerate
    surrounding prose, fall back to the first [...] block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def extract_lookup(doc_id: int) -> int:
    """Mine TERM -> VALUE lookup rows from a compiled reference doc into
    doc_lookup. Returns the count of rows written (0 on skip/parse-fail/error).
    Never raises."""
    try:
        if not _client:
            return 0
        body, no_to_id = _doc_text_paged(doc_id)
        if not body:
            return 0

        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": body[:40000]},
            ],
            max_tokens=4000,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, list):
            print(f"[compile_lookup] {doc_id}: parsed JSON not a list")
            return 0

        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            term = (item.get("term") or "").strip()
            if not term:
                continue
            value = (item.get("value") or "").strip()
            page_id = None
            try:
                page_id = no_to_id.get(int(item.get("page")))
            except (TypeError, ValueError):
                page_id = None
            rows.append((doc_id, term, value, page_id))

        with get_conn() as conn:
            conn.execute("DELETE FROM doc_lookup WHERE doc_id = %s", (doc_id,))
            for r in rows:
                conn.execute(
                    "INSERT INTO doc_lookup (doc_id, term, value, page_id) "
                    "VALUES (%s, %s, %s, %s)",
                    r,
                )
        return len(rows)
    except Exception as e:
        print(f"[compile_lookup] {doc_id} failed: {e!r}")
        return 0
