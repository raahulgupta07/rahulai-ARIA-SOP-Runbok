"""Zero-LLM entity auto-linking (gbrain-style "every write extracts refs").

The LLM entity pass (entities.py) is accurate but costs one call per doc. This
is the cheap, always-on complement: a DETERMINISTIC sweep over a doc's compiled
markdown that links it to entities ALREADY in the cross-doc index whenever their
name appears verbatim in the text. No model call, no new tables — it just adds
entity_mention edges, so the cross-doc graph (and ENTITY_RETRIEVE recall) gets
denser for free.

Conservative by construction:
  - only matches entities whose name length >= 4 (skips noisy short tokens),
  - whole-token / phrase match against the page text (case-insensitive),
  - never creates a NEW entity (that's the LLM pass's job) — only links to
    existing ones, so it can't invent junk.

Fail-soft: any error -> print + return 0. Never raises.
"""
import re

from .db import get_conn

_WORD = re.compile(r"\w+", re.UNICODE)


def _doc_text(conn, doc_id: int) -> str:
    """Concatenated compiled markdown for a doc (lowercased), capped per page so a
    looped/degenerate page can't dominate. Empty string if nothing compiled."""
    rows = conn.execute(
        "SELECT md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
        (doc_id,),
    ).fetchall()
    parts = []
    for r in rows:
        md = (r["md"] or "")
        if md:
            parts.append(md[:6000])
    return "\n".join(parts).lower()


def link_doc(doc_id: int) -> int:
    """Link `doc_id` to every existing entity whose name appears in its compiled
    text. Returns the number of NEW entity_mention edges written. Never raises."""
    try:
        with get_conn() as conn:
            text = _doc_text(conn, doc_id)
            if not text:
                return 0
            ents = conn.execute(
                "SELECT id, lower(name) AS lname FROM entities WHERE length(name) >= 4"
            ).fetchall()
            if not ents:
                return 0
            written = 0
            for e in ents:
                name = e["lname"]
                if not name or name not in text:
                    continue
                # require a word-boundary-ish hit so 'car' doesn't match 'cart'
                # (names with spaces/punctuation already match as phrases above)
                if name.isalnum() and not re.search(
                    r"(?<![\w])" + re.escape(name) + r"(?![\w])", text):
                    continue
                cur = conn.execute(
                    "INSERT INTO entity_mention (entity_id, doc_id) VALUES (%s, %s) "
                    "ON CONFLICT (entity_id, doc_id) DO NOTHING",
                    (e["id"], doc_id),
                )
                if (cur.rowcount or 0) > 0:
                    written += 1
            return written
    except Exception as ex:
        print(f"[autolink] doc {doc_id} failed: {ex!r}")
        return 0


def link_all() -> dict:
    """Sweep every ready doc, linking it to all matching existing entities.
    Returns {docs, edges, doc_ids} where doc_ids = docs that gained ≥1 new edge
    (for the 'linked tonight' highlight). Fail-soft."""
    try:
        with get_conn() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM docs WHERE status = 'ready' ORDER BY id"
            ).fetchall()]
    except Exception as e:
        print(f"[autolink] doc list failed: {e!r}")
        return {"docs": 0, "edges": 0, "doc_ids": []}
    edges = 0
    touched: list[int] = []
    for did in ids:
        n = link_doc(did)
        if n:
            edges += n
            touched.append(did)
    return {"docs": len(ids), "edges": edges, "doc_ids": touched}
