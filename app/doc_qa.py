"""Document → Q&A auto-extraction at ingest (Phase 1 of the Q&A bank).

After a doc is compiled, run ONE LLM pass over its page-marked markdown to mine a
handful of natural question/answer pairs a reader might actually ask — each answer
grounded ONLY in the doc, with the page(s) it came from cited. Pairs land in
qa_pairs with source='qa', status via governance (active if confidence ≥ floor,
else pending). Dedup by question is handled in qa.add_qa.

Gated by AUTO_QA_ENABLED (cost = 1 LLM call per doc). Fail-soft (returns 0).
"""
import json
import re

from .db import get_conn, log_ingest
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
    AUTO_QA_MAX,
)
from .qa import add_qa

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You build a Q&A study set from ONE IT SOP/runbook. Produce the natural "
    "questions a user would ask about this document, each with a correct, concise "
    "answer grounded ONLY in the text.\n"
    "RULES:\n"
    "- Each question = standalone, how a real user would phrase it (no 'according to "
    "the document').\n"
    "- Each answer = concise and factual, drawn ONLY from the provided text. Keep exact "
    "values, codes, thresholds and names verbatim. Never invent.\n"
    "- Cite the page number(s) the answer comes from (the '=== Page N ===' markers). "
    "Use the page numbers shown.\n"
    "- Preserve the original language (English or Burmese) of the document.\n"
    "- confidence 0-1 = how directly and unambiguously the text answers the question.\n"
    f"- Return AT MOST {{n}} pairs. Fewer is fine. If the text is too thin, return [].\n"
    'Reply ONLY as JSON: {{"qa": [{{"q": "...", "a": "...", "pages": [N], '
    '"confidence": 0.0-1.0}}]}}'
)


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _doc_text_paged(doc_id: int) -> tuple[str, str, dict]:
    """Return (title, page-marked body capped, {page_no: page_id}).

    Body uses compiled per-page markdown when present, else raw page text, each
    chunk prefixed with a '=== Page N ===' marker so the model can cite pages.
    """
    with get_conn() as conn:
        d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
        rows = conn.execute(
            "SELECT p.page_no, p.id AS page_id, "
            "COALESCE(m.md, p.text, p.text_layer, '') AS t "
            "FROM pages p LEFT JOIN doc_pages_md m "
            "  ON m.doc_id = p.doc_id AND m.page_no = p.page_no "
            "WHERE p.doc_id = %s ORDER BY p.page_no",
            (doc_id,),
        ).fetchall()
    title = (d or {}).get("name", "")
    no_to_id = {r["page_no"]: r["page_id"] for r in rows}
    parts, total = [], 0
    for r in rows:
        t = (r["t"] or "").strip()
        if not t:
            continue
        chunk = f"=== Page {r['page_no']} ===\n{t}"
        if total + len(chunk) > 14000:
            break
        parts.append(chunk)
        total += len(chunk)
    return title, "\n\n".join(parts), no_to_id


def extract_doc_qa(doc_id: int, display_name: str = "") -> int:
    """Mine grounded Q&A pairs from a compiled doc → qa_pairs (review-gated).
    Returns count saved (post-dedup). Fail-soft (returns 0 on any error)."""
    if not _client:
        return 0
    title, body, no_to_id = _doc_text_paged(doc_id)
    if not body.strip():
        return 0
    try:
        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS.format(n=AUTO_QA_MAX)},
                {"role": "user", "content": f"DOCUMENT: {title or display_name}\n\n{body}"},
            ],
            max_tokens=1400, temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
        pairs = json.loads(raw).get("qa", []) or []
    except Exception as e:
        print(f"[doc_qa] extract {doc_id} failed: {e!r}")
        return 0

    from .governance import decide_status
    saved = 0
    auto = 0
    origin = f"From document: {title or display_name}"
    for item in pairs[:AUTO_QA_MAX]:
        if not isinstance(item, dict):
            continue
        q = (item.get("q") or "").strip()
        a = (item.get("a") or "").strip()
        if len(q) < 6 or len(a) < 2:
            continue
        try:
            conf = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        # map cited page numbers → page ids (best-effort; drop unknowns)
        page_ids = []
        for pn in (item.get("pages") or []):
            try:
                pid = no_to_id.get(int(pn))
            except (TypeError, ValueError):
                pid = None
            if pid:
                page_ids.append(pid)
        status = decide_status("qa", conf, is_admin=False)
        try:
            before = _max_id()
            qid = add_qa(q, a, source="qa", status=status, confidence=conf,
                         doc_id=doc_id, page_ids=page_ids,
                         created_by=f"ingest:{doc_id}", origin=origin)
            if qid > before:        # newly inserted (not a dedup hit)
                saved += 1
                if status == "active":
                    auto += 1
        except Exception as e:
            print(f"[doc_qa] save failed: {e!r}")
    if saved:
        tail = f" ({auto} auto-approved)" if auto else " for review"
        log_ingest(doc_id, "qa", f"❓ {saved} Q&A pair(s){tail}")
    return saved


def _max_id() -> int:
    try:
        with get_conn() as conn:
            r = conn.execute("SELECT COALESCE(max(id), 0) AS m FROM qa_pairs").fetchone()
            return r["m"]
    except Exception:
        return 0
