"""Cross-document ENTITY index extraction — recurring things linked across docs.

The same thing (a code, a GOLD menu, a system, a screen, a field) referenced in
many SOPs/runbooks should be a single connected node so "where else is this
mentioned" works across the corpus. This module runs ONE LLM pass over a doc's
already-compiled page-marked markdown and extracts the distinct entities it
refers to (system/app names, menu paths, screen/window names, field names,
codes/IDs, key technical terms) into the cross-doc index:
    entities(name, kind, norm_key UNIQUE)   — one row per distinct thing
    entity_mention(entity_id, doc_id)        — this doc mentions that thing

Mirrors compile_lookup.py (one JSON-returning LLM call + per-page-capped concat
of doc_pages_md). Fail-soft: any error -> print + return 0. Never raises.
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
    "Extract the distinct ENTITIES this document refers to that are likely to "
    "also appear in OTHER IT documents — system/application names, menu paths, "
    "screen/window names, field names, codes/IDs, and key technical terms. Do "
    "NOT extract sentences, steps, or generic words. Reply ONLY as strict JSON: "
    'a list like [{"name":"...","kind":"..."}] where kind is one of '
    "code|menu_path|system|screen|field|term. Keep names verbatim. Max 40 "
    "entities."
)

# cap any single page's contribution so one degenerate/looped page (e.g. a 100k
# repeated-table compile) can't eat the whole window and starve the real rows.
_PER_PAGE_CAP = 6000


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _doc_text_paged(doc_id: int) -> str:
    """Return the page-marked body: doc_pages_md in page order, each page
    truncated to _PER_PAGE_CAP, joined with '## Page N' markers."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
    parts = []
    for r in rows:
        md = (r["md"] or "").strip()
        if not md:
            continue
        if len(md) > _PER_PAGE_CAP:
            md = md[:_PER_PAGE_CAP] + " …[truncated]"
        parts.append(f"## Page {r['page_no']}\n\n{md}")
    return "\n\n".join(parts).strip()


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


def extract_entities(doc_id: int) -> int:
    """Mine recurring entities from a compiled doc into the cross-doc index
    (entities + entity_mention). Returns the number of mentions written
    (0 on skip/parse-fail/error). Never raises."""
    try:
        if not _client:
            return 0
        body = _doc_text_paged(doc_id)
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
            print(f"[entities] {doc_id}: parsed JSON not a list")
            return 0

        written = 0
        seen = set()
        with get_conn() as conn:
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = (item.get("name") or "").strip()
                if not name or len(name) < 2:
                    continue
                kind = (item.get("kind") or "").strip() or None
                # norm_key = lowercased name with internal whitespace collapsed
                norm_key = re.sub(r"\s+", " ", name.lower()).strip()
                if not norm_key or norm_key in seen:
                    continue
                seen.add(norm_key)

                row = conn.execute(
                    "INSERT INTO entities (name, kind, norm_key) VALUES (%s, %s, %s) "
                    "ON CONFLICT (norm_key) DO UPDATE SET name = entities.name "
                    "RETURNING id",
                    (name, kind, norm_key),
                ).fetchone()
                entity_id = row["id"]

                conn.execute(
                    "INSERT INTO entity_mention (entity_id, doc_id) VALUES (%s, %s) "
                    "ON CONFLICT (entity_id, doc_id) DO NOTHING",
                    (entity_id, doc_id),
                )
                written += 1
        return written
    except Exception as e:
        print(f"[entities] {doc_id} failed: {e!r}")
        return 0


def docs_for_query(query: str, limit: int = 6) -> list[int]:
    """Cross-doc entity-aware retrieval (Phase 3.6): return doc_ids that mention an
    entity whose name appears in the question. Conservative — only entities with a
    name length >= 4 (skips noisy short tokens like 'trt'/'Site') matched as a
    case-insensitive substring of the question. Ordered by how many docs share the
    entity (broad connectors first). Fail-soft -> []."""
    q = (query or "").lower()
    if not q.strip():
        return []
    try:
        with get_conn() as conn:
            ents = conn.execute(
                "SELECT id, lower(name) AS lname FROM entities WHERE length(name) >= 4"
            ).fetchall()
            hit = [e["id"] for e in ents if e["lname"] and e["lname"] in q]
            if not hit:
                return []
            rows = conn.execute(
                "SELECT m.doc_id, count(*) AS shared FROM entity_mention m "
                "WHERE m.entity_id = ANY(%s) GROUP BY m.doc_id "
                "ORDER BY shared DESC LIMIT %s",
                (hit, max(1, int(limit))),
            ).fetchall()
        return [r["doc_id"] for r in rows]
    except Exception as e:
        print(f"[entities] docs_for_query failed: {e!r}")
        return []
