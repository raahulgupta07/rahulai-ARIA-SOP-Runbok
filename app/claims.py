"""Phase 1 of the Karpathy "LLM wiki" — atomic factual CLAIM extraction.

Each document carries small checkable assertions of the form
    <entity> <attribute> = <value>
(e.g. "Access = 2", "Category = 5", "timeout threshold = 30 seconds") — the
exact settings/codes/values/thresholds/paths that a DIFFERENT SOP could
CONTRADICT. This module runs ONE LLM pass over a doc's already-compiled
page-marked markdown and writes those atomic claims into:
    doc_claims(doc_id, page_no, page_id, entity, entity_key, attribute, value, raw)

A LATER pass (Phase 2, contradiction detection) groups claims by
(entity_key, attribute) across documents and flags divergent values — so
`claims_for_entity` below is the cross-doc lookup that pass will use.

Mirrors entities.py: one JSON-returning LLM call + per-page-capped concat of
doc_pages_md. Fail-soft — any error -> print + return 0/[]. NEVER raises.
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
)
from . import appcfg

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

# cap any single page's contribution so one degenerate/looped page (e.g. a 100k
# repeated-table compile) can't eat the whole window and starve the real rows.
_PER_PAGE_CAP = 6000

# upper bound on claims persisted per doc (mirrors the entities cap).
_MAX_CLAIMS = 40


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


def _build_sys(attrs: list, etypes: list) -> str:
    """System prompt steered by the per-project wiki schema's allowed
    attributes (+ entity-type hints) so extraction stays contradiction-shaped."""
    attr_list = ", ".join(str(a) for a in attrs) or "value"
    etype_hint = ", ".join(str(e) for e in etypes) if etypes else ""
    return (
        "You extract ATOMIC, CHECKABLE claims from an IT SOP/runbook. A claim is "
        "a single assertion of the form <entity> <attribute> = <value> — a "
        "concrete SETTING, CODE, VALUE, THRESHOLD, or PATH that another SOP could "
        "directly CONTRADICT. Only extract things that have an exact value worth "
        "checking across documents.\n"
        "Examples:\n"
        '  "set Access = 2"            -> {"entity":"Access","attribute":"value","value":"2"}\n'
        '  "Category 5 for MCS"        -> {"entity":"Category","attribute":"value","value":"5"}\n'
        '  "timeout threshold 30 seconds" -> {"entity":"timeout","attribute":"threshold","value":"30 seconds"}\n'
        f"The `attribute` MUST be one of these allowed attributes: {attr_list}. "
        + (f"Entities are usually one of: {etype_hint}. " if etype_hint else "")
        + "Do NOT extract prose, narrative steps, generic statements, or anything "
        "without a concrete checkable value. Keep entity/value verbatim from the "
        "document.\n"
        "Reply ONLY with strict JSON — a list like "
        '[{"entity":"...","attribute":"...","value":"...","page":N,"raw":"the source sentence"}]. '
        "`page` is the number from the '## Page N' marker the claim came from. "
        f"Maximum {_MAX_CLAIMS} claims."
    )


def extract_claims(doc_id: int, display_name: str = "") -> int:
    """Extract atomic factual claims from a compiled doc into doc_claims
    (rerun-safe: clears the doc's claims first). Returns the number of claims
    inserted (0 on skip/parse-fail/error). Never raises."""
    try:
        if not _client:
            return 0
        body = _doc_text_paged(doc_id)
        if not body:
            return 0

        # per-project schema steers which attributes / entity types matter
        try:
            schema = appcfg.get_wiki_schema()
            attrs = schema["contradiction"]["compare_attributes"]
            etypes = schema["entity_types"]
        except Exception:
            attrs, etypes = ["value", "threshold", "setting", "code", "path"], []
        attr_set = {str(a).lower() for a in (attrs or [])}

        sys = _build_sys(attrs, etypes)
        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": body[:40000]},
            ],
            max_tokens=4000,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, list):
            print(f"[claims] {doc_id}: parsed JSON not a list")
            return 0

        n = 0
        with get_conn() as conn:
            # rerun-safe: drop this doc's old claims before re-inserting
            conn.execute("DELETE FROM doc_claims WHERE doc_id = %s", (doc_id,))

            for item in data[:_MAX_CLAIMS]:
                if not isinstance(item, dict):
                    continue
                entity = (item.get("entity") or "").strip()
                attribute = (item.get("attribute") or "").strip().lower()
                value = (item.get("value") or "").strip()
                if not entity or not attribute or not value:
                    continue
                # lenient: keep attributes not in the allowed set (still lowercased)
                if attr_set and attribute not in attr_set:
                    pass

                entity_key = re.sub(r"\s+", " ", entity.lower()).strip()
                if not entity_key:
                    continue

                page_no = None
                if item.get("page") is not None:
                    try:
                        page_no = int(item.get("page"))
                    except (TypeError, ValueError):
                        page_no = None

                # resolve the page_id for this doc + page_no (None if not found)
                page_id = None
                if page_no is not None:
                    prow = conn.execute(
                        "SELECT id FROM pages WHERE doc_id = %s AND page_no = %s",
                        (doc_id, page_no),
                    ).fetchone()
                    if prow:
                        page_id = prow["id"]

                rawtext = (item.get("raw") or "").strip() or None

                conn.execute(
                    "INSERT INTO doc_claims "
                    "(doc_id, page_no, page_id, entity, entity_key, attribute, value, raw) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (doc_id, page_no, page_id, entity, entity_key,
                     attribute, value, rawtext),
                )
                n += 1
        print(f"[claims] doc {doc_id}: {n} claims")
        return n
    except Exception as e:
        print(f"[claims] {doc_id} failed: {e!r}")
        return 0


def claims_for_entity(entity_key: str, attribute: str,
                      exclude_doc_id: int | None = None) -> list[dict]:
    """Cross-doc lookup for the contradiction pass: all claims matching this
    (entity_key, attribute), optionally excluding one doc. Fail-soft -> []."""
    key = (entity_key or "").strip()
    attr = (attribute or "").strip().lower()
    if not key or not attr:
        return []
    try:
        sql = (
            "SELECT id, doc_id, page_no, page_id, entity, value, raw "
            "FROM doc_claims WHERE entity_key = %s AND attribute = %s"
        )
        params: list = [key, attr]
        if exclude_doc_id is not None:
            sql += " AND doc_id <> %s"
            params.append(exclude_doc_id)
        sql += " ORDER BY doc_id, page_no"
        with get_conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[claims] claims_for_entity failed: {e!r}")
        return []
