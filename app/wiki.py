"""Browsable wiki surface (Karpathy LLM-wiki Phase 3).

Turns the already-extracted knowledge — entities, entity_mention, doc_claims,
doc_dependency, doc_links, claim_conflict, compiled markdown — into a navigable
wiki: entity hub pages, backlinks, an index, and auto-hyperlinked document pages.
Pure reads + one regex linker. No LLM. Fail-soft (every function degrades to an
empty shape rather than raising into a route).
"""
import re

from .db import get_conn


def _norm(name: str) -> str:
    """Same normalization entities.py uses for norm_key: lower + ws-collapsed."""
    return re.sub(r"\s+", " ", (name or "").lower()).strip()


# ---------------------------------------------------------------- index --------
def wiki_index() -> dict:
    """Landing payload: top entities by doc-count + the document catalog + totals."""
    try:
        with get_conn() as conn:
            entities = conn.execute(
                "SELECT e.id, e.name, e.kind, count(m.doc_id) AS doc_count "
                "FROM entities e LEFT JOIN entity_mention m ON m.entity_id = e.id "
                "GROUP BY e.id ORDER BY doc_count DESC, e.name ASC LIMIT 200"
            ).fetchall()
            totals = conn.execute(
                "SELECT (SELECT count(*) FROM entities) AS entities, "
                "(SELECT count(*) FROM docs WHERE status IN ('ready','ready_lite')) AS docs, "
                "(SELECT count(*) FROM entity_mention) AS mentions"
            ).fetchone()
    except Exception as e:
        print(f"[wiki] index failed: {e!r}")
        entities, totals = [], {"entities": 0, "docs": 0, "mentions": 0}
    try:
        from .catalog import build_catalog
        catalog = build_catalog()
    except Exception as e:
        print(f"[wiki] catalog failed: {e!r}")
        catalog = {"groups": [], "systems": [], "totals": {}}
    return {"entities": entities, "catalog": catalog, "totals": totals}


# ----------------------------------------------------------- entity hub --------
def entity_hub(entity_id: int) -> dict | None:
    """Everything about one entity: where it appears, its asserted values (claims,
    superseded marked), conflicts on it, and co-mentioned related entities."""
    try:
        with get_conn() as conn:
            ent = conn.execute(
                "SELECT id, name, kind, norm_key FROM entities WHERE id = %s", (entity_id,)
            ).fetchone()
            if not ent:
                return None
            mentions = conn.execute(
                "SELECT m.doc_id, d.name AS doc_name, d.category, m.page_id "
                "FROM entity_mention m JOIN docs d ON d.id = m.doc_id "
                "WHERE m.entity_id = %s ORDER BY d.name", (entity_id,)
            ).fetchall()
            # claims keyed on the entity's norm_key (doc_claims.entity_key)
            claims = conn.execute(
                "SELECT c.attribute, c.value, c.doc_id, d.name AS doc_name, c.page_no, "
                "c.superseded_at FROM doc_claims c LEFT JOIN docs d ON d.id = c.doc_id "
                "WHERE c.entity_key = %s ORDER BY c.superseded_at NULLS FIRST, c.attribute",
                (ent["norm_key"],)
            ).fetchall()
            conflicts = conn.execute(
                "SELECT id, attribute, value_a, value_b, status, "
                "(SELECT name FROM docs WHERE id = doc_a) AS doc_a_name, "
                "(SELECT name FROM docs WHERE id = doc_b) AS doc_b_name "
                "FROM claim_conflict WHERE entity_key = %s ORDER BY created_at DESC",
                (ent["norm_key"],)
            ).fetchall()
            # related = entities co-mentioned in the same docs, ranked by overlap
            related = conn.execute(
                "SELECT e2.id, e2.name, e2.kind, count(*) AS shared "
                "FROM entity_mention m1 "
                "JOIN entity_mention m2 ON m2.doc_id = m1.doc_id AND m2.entity_id <> m1.entity_id "
                "JOIN entities e2 ON e2.id = m2.entity_id "
                "WHERE m1.entity_id = %s GROUP BY e2.id "
                "ORDER BY shared DESC, e2.name LIMIT 24", (entity_id,)
            ).fetchall()
    except Exception as e:
        print(f"[wiki] entity_hub({entity_id}) failed: {e!r}")
        return None
    return {
        "entity": {"id": ent["id"], "name": ent["name"], "kind": ent["kind"]},
        "mentions": mentions, "claims": claims, "conflicts": conflicts, "related": related,
    }


def resolve_entity(name: str) -> int | None:
    """Resolve an entity NAME (as clicked in an auto-link) to its id, via norm_key."""
    key = _norm(name)
    if not key:
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM entities WHERE norm_key = %s LIMIT 1", (key,)
            ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


# --------------------------------------------------- auto-hyperlinking ---------
def _entity_link_map() -> list[tuple[re.Pattern, str]]:
    """Build (word-boundary regex → markdown link) pairs for every entity with a
    name length >= 4. Longest names first so 'Customer Maintenance' wins over
    'Customer'. Cached per call (cheap; entities table is small)."""
    try:
        with get_conn() as conn:
            ents = conn.execute(
                "SELECT id, name FROM entities WHERE length(name) >= 4 ORDER BY length(name) DESC"
            ).fetchall()
    except Exception:
        return []
    pairs = []
    for e in ents:
        name = (e["name"] or "").strip()
        if not name:
            continue
        pat = re.compile(r"(?<![\w/[])" + re.escape(name) + r"\b", re.IGNORECASE)
        pairs.append((pat, f"[{name}](/wiki/entity/{e['id']})"))
    return pairs


def link_entities(md: str, pairs: list | None = None) -> str:
    """Wrap the FIRST occurrence of each known entity name in `md` as a markdown
    link to its hub. Skips fenced code blocks and existing links/images. Only the
    first hit per entity per page → keeps the text readable, not a sea of links."""
    if not md:
        return md or ""
    if pairs is None:
        pairs = _entity_link_map()
    # protect fenced code blocks from linking
    blocks = re.split(r"(```.*?```)", md, flags=re.DOTALL)
    out = []
    used: set[str] = set()
    for seg in blocks:
        if seg.startswith("```"):
            out.append(seg)
            continue
        for pat, repl in pairs:
            if repl in used:
                continue
            seg, n = pat.subn(repl, seg, count=1)
            if n:
                used.add(repl)
        out.append(seg)
    return "".join(out)


# --------------------------------------------------------- doc view ------------
def doc_view(doc_id: int) -> dict | None:
    """A document as a wiki page: compiled markdown with entity names auto-linked +
    backlinks (who references this doc) + prerequisites (doc_dependency)."""
    try:
        with get_conn() as conn:
            doc = conn.execute(
                "SELECT id, name, category FROM docs WHERE id = %s", (doc_id,)
            ).fetchone()
            if not doc:
                return None
            raw_pages = conn.execute(
                "SELECT page_no, md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
                (doc_id,)
            ).fetchall()
            # referenced-by = deps pointing AT this doc + co-citation links
            backlinks = conn.execute(
                "SELECT dep.from_doc AS doc_id, d.name AS doc_name, dep.reason "
                "FROM doc_dependency dep JOIN docs d ON d.id = dep.from_doc "
                "WHERE dep.to_doc = %s "
                "UNION "
                "SELECT l.src_doc AS doc_id, d2.name AS doc_name, 'co-cited' AS reason "
                "FROM doc_links l JOIN docs d2 ON d2.id = l.src_doc WHERE l.dst_doc = %s",
                (doc_id, doc_id)
            ).fetchall()
            depends_on = conn.execute(
                "SELECT dep.to_doc AS doc_id, d.name AS doc_name, dep.reason "
                "FROM doc_dependency dep JOIN docs d ON d.id = dep.to_doc "
                "WHERE dep.from_doc = %s", (doc_id,)
            ).fetchall()
    except Exception as e:
        print(f"[wiki] doc_view({doc_id}) failed: {e!r}")
        return None
    pairs = _entity_link_map()
    pages = [{"page_no": p["page_no"], "md_linked": link_entities(p["md"] or "", pairs)}
             for p in raw_pages]
    return {
        "doc": {"id": doc["id"], "name": doc["name"], "category": doc["category"]},
        "pages": pages, "backlinks": backlinks, "depends_on": depends_on,
    }
