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
                        sectors: list[int] | None = None,
                        folders: list[int] | None = None) -> list[int]:
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
      AND (%(folders)s::bigint[] IS NULL
           OR d.folder_id = ANY(%(folders)s)
           OR (d.folder_id IS NULL AND d.sector_id = ANY(%(sectors)s)))
    GROUP BY m.doc_id
    ORDER BY dist ASC, m.doc_id
    LIMIT %(limit)s
    """
    try:
        with get_conn() as c:
            rows = c.execute(sql, {"docs": seed_doc_ids, "hops": hops, "limit": limit,
                                   "sectors": sectors, "folders": folders}).fetchall()
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


# ───────────────────────── #5 graph viz data ─────────────────────────
def graph_data(limit: int = 300) -> dict:
    """Nodes (entities that have edges) + typed edges, for the graph view."""
    with get_conn() as c:
        edges = c.execute(
            "SELECT src_entity AS s, dst_entity AS d, rel FROM entity_edge LIMIT %s",
            (limit,)).fetchall()
        ids = {e["s"] for e in edges} | {e["d"] for e in edges}
        nodes = []
        if ids:
            nodes = c.execute(
                "SELECT e.id, e.name, e.kind, "
                "(SELECT count(*) FROM entity_mention m WHERE m.entity_id=e.id) AS docs "
                "FROM entities e WHERE e.id = ANY(%s)", (list(ids),)).fetchall()
    return {"nodes": [dict(n) for n in nodes],
            "edges": [{"src": e["s"], "dst": e["d"], "rel": e["rel"]} for e in edges]}


# ───────────────────────── #3 entity profile ─────────────────────────
def resolve_entity(name: str) -> int | None:
    if not name:
        return None
    with get_conn() as c:
        r = c.execute("SELECT id FROM entities WHERE norm_key=%s", (_norm(name),)).fetchone()
        if r:
            return r["id"]
        r = c.execute("SELECT id FROM entities WHERE similarity(name,%s) > 0.4 "
                      "ORDER BY similarity(name,%s) DESC LIMIT 1", (name, name)).fetchone()
        return r["id"] if r else None


def entity_profile(entity_id: int) -> dict:
    """One entity + its typed relationships (both directions) + the docs it's in."""
    with get_conn() as c:
        ent = c.execute("SELECT id, name, kind FROM entities WHERE id=%s",
                        (entity_id,)).fetchone()
        if not ent:
            return {}
        rels = c.execute(
            "SELECT e.rel, e.dst_entity AS oid, x.name AS other, 'out' AS dir FROM entity_edge e "
            "JOIN entities x ON x.id=e.dst_entity WHERE e.src_entity=%(id)s "
            "UNION ALL "
            "SELECT e.rel, e.src_entity AS oid, x.name AS other, 'in' AS dir FROM entity_edge e "
            "JOIN entities x ON x.id=e.src_entity WHERE e.dst_entity=%(id)s",
            {"id": entity_id}).fetchall()
        docs = c.execute(
            "SELECT d.id, d.name FROM entity_mention m JOIN docs d ON d.id=m.doc_id "
            "WHERE m.entity_id=%s", (entity_id,)).fetchall()
    return {"entity": dict(ent), "relationships": [dict(r) for r in rels],
            "docs": [dict(d) for d in docs]}


# ───────────────────────── #2 path reasoning ─────────────────────────
def shortest_path(a_id: int, b_id: int, max_hops: int = 5) -> list[dict]:
    """Shortest path a→b over entity_edge (recursive CTE). Returns ordered steps
    [{entity, rel_to_next}]. Empty if unconnected within max_hops."""
    sql = """
    WITH RECURSIVE p(node, path, rels, hop) AS (
        SELECT %(a)s::bigint, ARRAY[%(a)s::bigint], ARRAY[]::text[], 0
      UNION ALL
        SELECT (CASE WHEN e.src_entity=p.node THEN e.dst_entity ELSE e.src_entity END),
               p.path || (CASE WHEN e.src_entity=p.node THEN e.dst_entity ELSE e.src_entity END),
               p.rels || e.rel, p.hop+1
        FROM p JOIN entity_edge e ON (e.src_entity=p.node OR e.dst_entity=p.node)
        WHERE p.hop < %(m)s
          AND NOT (CASE WHEN e.src_entity=p.node THEN e.dst_entity ELSE e.src_entity END) = ANY(p.path)
    )
    SELECT path, rels FROM p WHERE node=%(b)s ORDER BY hop ASC LIMIT 1
    """
    with get_conn() as c:
        r = c.execute(sql, {"a": a_id, "b": b_id, "m": max_hops}).fetchone()
        if not r:
            return []
        path, rels = r["path"], r["rels"]
        names = {x["id"]: x["name"] for x in c.execute(
            "SELECT id, name FROM entities WHERE id = ANY(%s)", (path,)).fetchall()}
    steps = []
    for i, nid in enumerate(path):
        steps.append({"entity": names.get(nid, str(nid)),
                      "rel": rels[i] if i < len(rels) else None})
    return steps


def explain_path(a: str, b: str, steps: list[dict]) -> str:
    cli = _client()
    chain = " ".join(f"{s['entity']}" + (f" —{s['rel']}→ " if s['rel'] else "") for s in steps)
    if not cli or not steps:
        return chain
    try:
        resp = cli.chat.completions.create(
            model=_model().replace("openrouter/", ""),
            messages=[{"role": "system", "content":
                       "Explain in 1-2 sentences how two things connect, given the relationship "
                       "chain. Plain, practical (this is an IT/SOP context). No preamble."},
                      {"role": "user", "content": f"{a} → {b}\nchain: {chain}"}],
            max_tokens=120, temperature=0)
        return (resp.choices[0].message.content or chain).strip()
    except Exception:
        return chain


def path_between(a_name: str, b_name: str) -> dict:
    a, b = resolve_entity(a_name), resolve_entity(b_name)
    if not a or not b:
        return {"found": False, "reason": "entity not found"}
    steps = shortest_path(a, b)
    if not steps:
        return {"found": False, "reason": "no path within 5 hops"}
    return {"found": True, "steps": steps, "explanation": explain_path(a_name, b_name, steps)}


# ───────────────────────── #1 communities (option B) ─────────────────────────
def build_communities() -> dict:
    """Connected-components clustering over entity_edge, then 1 LLM summary per
    cluster (whole-corpus 'global' answers). Returns counts. Fail-soft."""
    with get_conn() as c:
        edges = c.execute("SELECT src_entity AS s, dst_entity AS d FROM entity_edge").fetchall()
    # union-find
    parent: dict[int, int] = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for e in edges:
        union(e["s"], e["d"])
    clusters: dict[int, list[int]] = {}
    for n in list(parent):
        clusters.setdefault(find(n), []).append(n)
    # renumber + persist membership; only clusters with >=2 entities
    cli = _client()
    summarized = 0
    with get_conn() as c:
        c.execute("DELETE FROM entity_community"); c.execute("DELETE FROM community_summary")
        cid = 0
        for members in clusters.values():
            if len(members) < 2:
                continue
            cid += 1
            for eid in members:
                c.execute("INSERT INTO entity_community (entity_id, cluster_id) VALUES (%s,%s) "
                          "ON CONFLICT (entity_id) DO UPDATE SET cluster_id=EXCLUDED.cluster_id",
                          (eid, cid))
            names = [r["name"] for r in c.execute(
                "SELECT name FROM entities WHERE id = ANY(%s)", (members,)).fetchall()]
            doc_ids = [r["doc_id"] for r in c.execute(
                "SELECT DISTINCT doc_id FROM entity_mention WHERE entity_id = ANY(%s)",
                (members,)).fetchall()]
            label, summary = (", ".join(names[:3]), "")
            if cli:
                try:
                    resp = cli.chat.completions.create(
                        model=_model().replace("openrouter/", ""),
                        messages=[{"role": "system", "content":
                                   "Given a cluster of related entities from company SOPs, write a "
                                   "SHORT title (3-5 words) and a 2-3 sentence summary of what this "
                                   "group of things is about. JSON {\"label\":..,\"summary\":..}."},
                                  {"role": "user", "content": "entities: " + ", ".join(names[:40])}],
                        max_tokens=220, temperature=0)
                    raw = re.sub(r"^```(json)?|```$", "", (resp.choices[0].message.content or "").strip(), flags=re.I)
                    d = json.loads(raw)
                    label, summary = d.get("label", label), d.get("summary", "")
                except Exception:
                    pass
            c.execute("INSERT INTO community_summary (cluster_id, label, summary, entity_ids, doc_ids) "
                      "VALUES (%s,%s,%s,%s,%s)",
                      (cid, label, summary, json.dumps(members), json.dumps(doc_ids)))
            summarized += 1
    return {"clusters": summarized}


def global_answer(query: str) -> dict:
    """Answer a whole-corpus question from the community summaries (global mode)."""
    cli = _client()
    with get_conn() as c:
        comms = c.execute(
            "SELECT cluster_id, label, summary FROM community_summary "
            "WHERE summary <> '' ORDER BY cluster_id").fetchall()
    if not comms:
        return {"answer": "No community summaries yet — run /graphrag/communities/build.",
                "clusters": []}
    ctx = "\n\n".join(f"[{r['label']}] {r['summary']}" for r in comms)
    if not cli:
        return {"answer": ctx, "clusters": [dict(r) for r in comms]}
    try:
        resp = cli.chat.completions.create(
            model=_model().replace("openrouter/", ""),
            messages=[{"role": "system", "content":
                       "Answer the question by SYNTHESISING across these cluster summaries of a "
                       "company's SOP knowledge base. Be concrete; name the relevant areas."},
                      {"role": "user", "content": f"Question: {query}\n\nClusters:\n{ctx}"}],
            max_tokens=500, temperature=0.2)
        ans = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        ans = ctx
    return {"answer": ans, "clusters": [dict(r) for r in comms]}
