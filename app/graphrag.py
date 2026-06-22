"""GraphRAG-A — typed entity relationships + multi-hop graph-walk retrieval.

Vectorless, in your existing Postgres (recursive CTE — no Neo4j, no AGE, no
embeddings). Builds on the entity index (entities/entity_mention) by adding
TYPED entity->entity edges (entity_edge), then at query time traverses the graph
N hops to surface related documents that share no keywords with the question
(e.g. a prerequisite SOP, a system two hops away).

Two halves:
  ingest  : extract_relationships(doc_id)  — 1 LLM pass/doc -> entity_edge rows
  retrieve: graph_neighbor_docs(seed_docs, hops) — recursive walk -> related docs

Flags (env): GRAPHRAG_ENABLED (default 0), GRAPHWALK_HOPS (default 2, clamp 1..4),
GRAPHRAG_MAX_DOCS (default 4). All fail-soft — any error returns empty/0.
"""
from __future__ import annotations

import json
import os
import re

from .db import get_conn

GRAPHRAG_ENABLED: bool = os.getenv("GRAPHRAG_ENABLED", "0") == "1"
GRAPHWALK_HOPS: int = max(1, min(int(os.getenv("GRAPHWALK_HOPS", "2")), 4))
GRAPHRAG_MAX_DOCS: int = int(os.getenv("GRAPHRAG_MAX_DOCS", "4"))

_RELS = "depends_on, part_of, accessed_via, runs_on, produces, relates_to"

_SYS = (
    "You map relationships between named things in a company SOP/runbook. "
    "You are given the document text and a list of KNOWN entities (codes, "
    "systems, screens, menus, terms) already extracted from it. Output JSON "
    "{\"edges\":[{\"src\":\"<entity>\",\"dst\":\"<entity>\",\"rel\":\"<type>\"}]} "
    f"using ONLY these rel types: {_RELS}. BOTH src and dst MUST be from the "
    "KNOWN entities list verbatim — never invent a node. Only emit a relationship "
    "the text actually states or strongly implies. Return {\"edges\":[]} if none."
)


def _model() -> str:
    return os.getenv("ENTITY_MODEL") or os.getenv("INGEST_MODEL", "google/gemini-2.5-flash")


def _client():
    from .entities import _client as ec  # reuse the configured OpenAI/OpenRouter client
    return ec


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


def extract_relationships(doc_id: int) -> int:
    """One LLM pass: read the doc's known entities + text, write typed entity_edge
    rows (validated against the entities table — no hallucinated nodes). Returns
    the number of edges written. Fail-soft."""
    cli = _client()
    if not cli:
        return 0
    try:
        from .entities import _doc_text_paged
        body = _doc_text_paged(doc_id)
        if not body.strip():
            return 0
        with get_conn() as c:
            ents = c.execute(
                "SELECT e.id, e.name, e.norm_key FROM entities e "
                "JOIN entity_mention m ON m.entity_id = e.id WHERE m.doc_id = %s",
                (doc_id,)).fetchall()
        if len(ents) < 2:
            return 0
        by_key = {e["norm_key"]: e["id"] for e in ents}
        names = ", ".join(sorted({e["name"] for e in ents}))
        resp = cli.chat.completions.create(
            model=_model().replace("openrouter/", ""),
            messages=[{"role": "system", "content": _SYS},
                      {"role": "user", "content":
                       f"KNOWN entities: {names}\n\nDOCUMENT:\n{body[:18000]}"}],
            max_tokens=900, temperature=0)
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.I).strip()
        edges = json.loads(raw).get("edges", []) or []
    except Exception as e:
        print(f"[graphrag] extract {doc_id} failed: {e!r}")
        return 0

    written = 0
    with get_conn() as c:
        for ed in edges:
            if not isinstance(ed, dict):
                continue
            s = by_key.get(_norm(ed.get("src")))
            d = by_key.get(_norm(ed.get("dst")))
            rel = (ed.get("rel") or "relates_to").strip().lower()[:32]
            if not s or not d or s == d:        # both must be real, distinct entities
                continue
            try:
                c.execute(
                    "INSERT INTO entity_edge (src_entity, dst_entity, rel, doc_id) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (s, d, rel, doc_id))
                written += 1
            except Exception:
                pass
    return written


def graph_neighbor_docs(seed_doc_ids: list[int], hops: int | None = None,
                        limit: int | None = None,
                        sectors: list[int] | None = None) -> list[int]:
    """Walk the entity graph up to `hops` from the seed docs' entities and return
    related doc_ids (excluding the seeds), nearest-first. Recursive CTE over
    entity_edge (typed) UNION the entity_mention bridge. RBAC-scoped. Fail-soft."""
    if not seed_doc_ids:
        return []
    hops = max(1, min(hops or GRAPHWALK_HOPS, 4))
    limit = limit or GRAPHRAG_MAX_DOCS
    sql = """
    WITH RECURSIVE walk(entity_id, hop) AS (
        SELECT DISTINCT m.entity_id, 0
        FROM entity_mention m WHERE m.doc_id = ANY(%(docs)s)
      UNION
        SELECT CASE WHEN e.src_entity = w.entity_id THEN e.dst_entity
                    ELSE e.src_entity END, w.hop + 1
        FROM walk w
        JOIN entity_edge e ON (e.src_entity = w.entity_id OR e.dst_entity = w.entity_id)
        WHERE w.hop < %(hops)s
    )
    SELECT m.doc_id, min(w.hop) AS dist
    FROM walk w
    JOIN entity_mention m ON m.entity_id = w.entity_id
    JOIN docs d ON d.id = m.doc_id
    WHERE m.doc_id <> ALL(%(docs)s) AND d.status = 'ready'
      AND (%(sectors)s::bigint[] IS NULL OR d.sector_id = ANY(%(sectors)s))
    GROUP BY m.doc_id
    ORDER BY dist ASC, m.doc_id
    LIMIT %(limit)s
    """
    try:
        with get_conn() as c:
            rows = c.execute(sql, {"docs": seed_doc_ids, "hops": hops,
                                   "limit": limit, "sectors": sectors}).fetchall()
        return [r["doc_id"] for r in rows]
    except Exception as e:
        print(f"[graphrag] neighbor walk failed (non-fatal): {e!r}")
        return []


def link_all() -> dict:
    """Backfill: extract relationships for every ready doc (run once after upgrade)."""
    with get_conn() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM docs WHERE status='ready' ORDER BY id").fetchall()]
    total = 0
    for did in ids:
        total += extract_relationships(did)
    return {"docs": len(ids), "edges": total}
