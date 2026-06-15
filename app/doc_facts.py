"""Document → facts auto-extraction at ingest.

After a doc is compiled, run ONE LLM pass over its clean markdown/summary to pull
out the few durable, standalone facts/rules/values worth surfacing in EVERY answer
(policies, thresholds, owners, key codes). Each lands as a memory fact with
source='doc', status='pending' (Review Gate — an admin approves which ones become
active). Dedup is handled by memory.add_memory (find_similar).

Gated by AUTO_FACTS_ENABLED (cost = 1 LLM call per doc). Fail-soft.
"""
import json
import re

from .db import get_conn, log_ingest
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
    AUTO_FACTS_MAX,
)
from .memory import add_memory

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You extract DURABLE FACTS from an IT SOP/runbook — the handful of standalone "
    "rules, policies, thresholds, owners, key codes or values that someone should "
    "remember WITHOUT re-reading the document.\n"
    "RULES:\n"
    "- Each fact = ONE concise, standalone sentence (no 'the document says', no 'this SOP').\n"
    "- Only stable, reusable facts (a policy value, a threshold, a default, an owner, a "
    "key transaction/screen code). NOT step-by-step procedure lines, NOT headings, NOT vague statements.\n"
    "- Keep exact values/codes/names verbatim.\n"
    "- Preserve the original language (English or Burmese).\n"
    "- Give each fact a confidence 0-1 = how clearly it's a durable, standalone fact "
    "(not a procedure step or vague line).\n"
    f"- Return AT MOST {{n}} facts. Fewer is fine. If nothing durable, return an empty list.\n"
    'Reply ONLY as JSON: {{"facts": [{{"fact": "...", "confidence": 0.0-1.0}}]}}'
)


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _doc_text(doc_id: int) -> tuple[str, str]:
    """Return (title, compiled markdown|summary) for the doc, capped."""
    with get_conn() as conn:
        w = conn.execute(
            "SELECT title, summary, md FROM doc_wiki WHERE doc_id = %s", (doc_id,)
        ).fetchone()
        if w and (w.get("md") or w.get("summary")):
            return (w.get("title") or "", (w.get("md") or w.get("summary") or "")[:14000])
        # fallback: concat compiled page md, else raw page text
        rows = conn.execute(
            "SELECT COALESCE(m.md, p.text, p.text_layer, '') AS t "
            "FROM pages p LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no "
            "WHERE p.doc_id = %s ORDER BY p.page_no", (doc_id,)
        ).fetchall()
        d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
    body = "\n\n".join(r["t"] for r in rows if r["t"])[:14000]
    return ((d or {}).get("name", ""), body)


def extract_doc_facts(doc_id: int, display_name: str = "") -> int:
    """Extract durable facts from a compiled doc → pending memory facts.
    Returns count saved (post-dedup). Fail-soft (returns 0 on any error)."""
    if not _client:
        return 0
    title, body = _doc_text(doc_id)
    if not body.strip():
        return 0
    try:
        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS.format(n=AUTO_FACTS_MAX)},
                {"role": "user", "content": f"DOCUMENT: {title or display_name}\n\n{body}"},
            ],
            max_tokens=700, temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
        facts = json.loads(raw).get("facts", []) or []
    except Exception as e:
        print(f"[doc_facts] extract {doc_id} failed: {e!r}")
        return 0
    from .governance import decide_status
    saved = 0
    auto = 0
    origin = f"From document: {title or display_name}"
    for item in facts[:AUTO_FACTS_MAX]:
        if isinstance(item, dict):
            f = (item.get("fact") or "").strip()
            try:
                conf = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
        else:                       # tolerate a bare string
            f, conf = (item or "").strip(), 0.0
        if len(f) < 8:
            continue
        status = decide_status("doc", conf, is_admin=False)
        try:
            before = _max_id()
            mid = add_memory(f, source="doc", created_by=f"ingest:{doc_id}",
                             status=status, origin_question=origin, confidence=conf)
            if mid > before:        # newly inserted (not a dedup hit)
                saved += 1
                if status == "active":
                    auto += 1
        except Exception as e:
            print(f"[doc_facts] save failed: {e!r}")
    if saved:
        tail = f" ({auto} auto-approved)" if auto else " for review"
        log_ingest(doc_id, "facts", f"💡 {saved} fact(s){tail}")
    return saved


def _max_id() -> int:
    try:
        with get_conn() as conn:
            r = conn.execute("SELECT COALESCE(max(id), 0) AS m FROM memory").fetchone()
            return r["m"]
    except Exception:
        return 0
