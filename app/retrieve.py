"""Hybrid, indexed retrieval over pages + precomputed tree nodes.

Signals (all SQL, indexed — no full scan, no per-query tree parse):
  • Postgres full-text search (tsvector / GIN) over page text
  • trigram similarity (pg_trgm / GIN) — fuzzy, Burmese-substring friendly
  • ILIKE substring per expanded term — exact Burmese / code matches
  • section match via precomputed `nodes` table (PageIndex tree), weighted 2x
LLM query expansion adds EN+Burmese synonyms so 'disable' also finds
'deactivate / end validity / ပိတ်'.
"""
import os
import re
import json

from .db import get_conn
from .config import OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL

RETRIEVE_EXPAND = os.getenv("RETRIEVE_EXPAND", "1") == "1"
DEFAULT_K = int(os.getenv("RETRIEVE_K", "6"))
# depth-fill: the per-page ranker spreads top hits across many docs (one
# keyword-dense TITLE page each), so procedural steps — which live PAST the
# title page — never surface. After ranking, pull the single best-matching
# doc's top body pages so the answer has the actual content, not just titles.
RETRIEVE_DEPTH = int(os.getenv("RETRIEVE_DEPTH", "5"))
# rerank: FTS recall is broad but its ordering is keyword-frequency, not
# relevance. Over-fetch candidates, let a small LLM reorder by how well each
# page actually answers the question, keep the best K. Fail-soft to FTS order.
RETRIEVE_RERANK = os.getenv("RETRIEVE_RERANK", "1") == "1"
RERANK_POOL = int(os.getenv("RERANK_POOL", "16"))

_TERM = re.compile(r"[^\wက-႟]+", re.UNICODE)

_client = None
if OPENROUTER_API_KEY and RETRIEVE_EXPAND:
    from openai import OpenAI

    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


_EXPAND_SYS = (
    "You turn a user's question into search terms for finding the right page in "
    "company IT SOP / runbook documents (full-text search, no embeddings). "
    "The user often uses casual or abbreviated wording while the document uses the "
    "formal name. Output up to 12 short terms/phrases, comma-separated, NO "
    "explanation, that maximise the chance of matching the document text. Include:\n"
    "1. The user's own key nouns, kept as phrases.\n"
    "2. EXPANDED acronyms AND their short forms (e.g. 'adj code' -> 'adjustment "
    "reason code', 'GLD' -> 'Gold Central', 'CAR' -> stays 'CAR').\n"
    "3. Likely on-screen / menu / table / field / transaction names a screenshot "
    "would show for this task (e.g. 'General Parameter Management', table codes, "
    "button labels).\n"
    "4. Synonyms for the ACTION (add/create/new/insert; disable/deactivate/remove).\n"
    "5. The Burmese equivalent of the main term if the question is Burmese.\n"
    "Only terms likely to appear verbatim in the document. No sentences."
)


def _expand(query: str) -> list[str]:
    """Return [query] + targeted search terms/phrases (EN + Burmese). Fail-soft."""
    terms = {query.strip()}
    if not _client:
        return list(terms)
    try:
        r = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _EXPAND_SYS},
                {"role": "user", "content": query.strip()},
            ],
            max_tokens=160,
            temperature=0,
        )
        raw = r.choices[0].message.content or ""
        for t in raw.replace("\n", ",").split(","):
            t = t.strip().strip("-•*0123456789. ").strip()
            if 2 <= len(t) <= 50:
                terms.add(t)
    except Exception as e:
        print(f"[retrieve] expand failed: {e!r}")
    return list(terms)


def _tsquery(terms: list[str]) -> str:
    """Build a safe OR tsquery string from terms."""
    toks = []
    for t in terms:
        for w in _TERM.split(t):
            w = w.strip()
            if len(w) >= 2:
                toks.append(w)
    seen = []
    for w in toks:
        if w.lower() not in [s.lower() for s in seen]:
            seen.append(w)
    return " | ".join(seen[:40])


def _rerank(query: str, rows: list, keep: int) -> list:
    """Reorder candidate pages by relevance to the question, keep top `keep`.
    One small LLM call over snippets. Fail-soft -> original FTS order."""
    if not (_client and RETRIEVE_RERANK) or len(rows) <= keep:
        return rows[:keep]
    try:
        lines = []
        for i, r in enumerate(rows):
            snip = (r.get("snippet") or r.get("text_full") or "")[:220].replace("\n", " ")
            lines.append(f"[{i}] {r.get('doc_name','')} p.{r.get('page_no','')}: {snip}")
        r = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": (
                    "You rank candidate document pages by how well each one ANSWERS "
                    "the user's question (actual steps/values/screens, not just a "
                    f"keyword match). Return ONLY the {keep} best indices, most "
                    "relevant first, comma-separated (e.g. 3,0,5). No other text.")},
                {"role": "user", "content": f"Question: {query}\n\nPages:\n" + "\n".join(lines)},
            ],
            max_tokens=60,
            temperature=0,
        )
        order = [int(x) for x in re.findall(r"\d+", r.choices[0].message.content or "")]
        picked, used = [], set()
        for i in order:
            if 0 <= i < len(rows) and i not in used:
                picked.append(rows[i]); used.add(i)
            if len(picked) >= keep:
                break
        # backfill anything the model dropped, preserving FTS order
        for i, row in enumerate(rows):
            if len(picked) >= keep:
                break
            if i not in used:
                picked.append(row); used.add(i)
        return picked or rows[:keep]
    except Exception as e:
        print(f"[retrieve] rerank failed: {e!r}")
        return rows[:keep]


def search_pages(query: str, k: int | None = None) -> list[dict]:
    """Top-k candidate pages with combined hybrid score."""
    k = k or DEFAULT_K
    terms = _expand(query)
    tsq = _tsquery(terms)
    raw = query.strip()
    ilike_terms = [t for t in terms if 3 <= len(t) <= 50][:10]

    # one indexed query; score = fts + 2*section + 0.5*trigram + ilike hits
    sql = """
    WITH q AS (
        SELECT to_tsquery('simple', %(tsq)s) AS tsq
    )
    SELECT p.id AS page_id, p.doc_id, d.name AS doc_name, p.page_no,
           p.image_path, left(p.text, 400) AS snippet,
           left(p.text, 16000) AS text_full, m.md AS page_md,
           ts_rank(p.tsv, q.tsq) AS fts,
           similarity(left(p.text, 2000), %(raw)s) AS trg,
           COALESCE((SELECT max(ts_rank(n.tsv, q.tsq)) FROM nodes n
                     WHERE n.doc_id = p.doc_id AND n.page_no = p.page_no
                       AND n.tsv @@ q.tsq), 0) AS node,
           (SELECT count(*) FROM unnest(%(ilike)s::text[]) term
            WHERE p.text ILIKE '%%' || term || '%%') AS hits
    FROM pages p JOIN docs d ON d.id = p.doc_id
         LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no, q
    WHERE d.status = 'ready' AND (
          p.tsv @@ q.tsq
       OR p.text ILIKE ANY (SELECT '%%' || t || '%%' FROM unnest(%(ilike)s::text[]) t)
       OR EXISTS (SELECT 1 FROM nodes n WHERE n.doc_id = p.doc_id
                  AND n.page_no = p.page_no AND n.tsv @@ q.tsq))
    ORDER BY (ts_rank(p.tsv, q.tsq) + 2.0 * COALESCE(
                 (SELECT max(ts_rank(n.tsv, q.tsq)) FROM nodes n
                  WHERE n.doc_id = p.doc_id AND n.page_no = p.page_no
                    AND n.tsv @@ q.tsq), 0)
              + 0.5 * similarity(left(p.text, 2000), %(raw)s)
              + 0.3 * (SELECT count(*) FROM unnest(%(ilike)s::text[]) term
                       WHERE p.text ILIKE '%%' || term || '%%')) DESC
    LIMIT %(k)s
    """
    # over-fetch a candidate pool when reranking, then trim to k by relevance
    fetch_k = max(k, min(RERANK_POOL, k * 2)) if (RETRIEVE_RERANK and _client) else k
    params = {"tsq": tsq or "x", "raw": raw, "ilike": ilike_terms or [raw], "k": fetch_k}
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    # relevance rerank the pool, keep best k (before depth-fill, so depth-fill
    # extends the page the user actually needs)
    if rows:
        rows = _rerank(query, list(rows), k)

    if not rows:
        # nothing matched — fall back to most recent doc's first pages so the
        # vision model still has something to read
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT p.id AS page_id, p.doc_id, d.name AS doc_name, p.page_no, "
                "p.image_path, left(p.text,400) AS snippet, left(p.text,16000) AS text_full, "
                "m.md AS page_md "
                "FROM pages p JOIN docs d ON d.id = p.doc_id "
                "LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no "
                "WHERE p.doc_id = (SELECT max(id) FROM docs WHERE status='ready') "
                "ORDER BY p.page_no LIMIT %s",
                (k,),
            ).fetchall()

    # depth-fill the top doc with its best-matching body pages (relevance-ranked,
    # so steps deep in a long SOP still surface). Skips pages already retrieved.
    if rows and RETRIEVE_DEPTH and tsq:
        top_doc = rows[0]["doc_id"]
        have = {(r["doc_id"], r["page_no"]) for r in rows}
        with get_conn() as conn:
            extra = conn.execute(
                "WITH q AS (SELECT to_tsquery('simple', %(tsq)s) AS tsq) "
                "SELECT p.id AS page_id, p.doc_id, d.name AS doc_name, p.page_no, "
                "p.image_path, left(p.text,400) AS snippet, left(p.text,16000) AS text_full, "
                "m.md AS page_md "
                "FROM pages p JOIN docs d ON d.id = p.doc_id "
                "LEFT JOIN doc_pages_md m ON m.doc_id = p.doc_id AND m.page_no = p.page_no, q "
                "WHERE p.doc_id = %(doc)s "
                "ORDER BY (ts_rank(p.tsv, q.tsq) + 0.3 * similarity(left(p.text,2000), %(raw)s)) DESC, "
                "p.page_no LIMIT %(d)s",
                {"tsq": tsq, "raw": raw, "doc": top_doc, "d": RETRIEVE_DEPTH},
            ).fetchall()
        for e in extra:
            if (e["doc_id"], e["page_no"]) not in have:
                rows.append(e)
                have.add((e["doc_id"], e["page_no"]))

    use_wiki = os.getenv("CHAT_USE_WIKI", "0") == "1"
    out = []
    for r in rows:
        raw = r.get("text_full") or r.get("snippet") or ""
        md = r.get("page_md")
        body = md if (use_wiki and md) else raw   # compiled markdown preferred, raw fallback
        out.append({
            "page_id": r["page_id"],
            "doc_id": r["doc_id"],
            "doc_name": r["doc_name"],
            "page_no": r["page_no"],
            "image_path": r["image_path"],
            "snippet": r["snippet"] or "",
            "text_full": body,
        })
    return out


# ---------------- agentic / iterative retrieval (Vercel knowledge-agent idea) ---
# When the first search returns thin results, let a small LLM propose alternative
# angles (sub-questions / synonyms / predicted screen names) and search each, then
# merge. Targets blind spots without embeddings. Flag-gated; falls back to plain.
AGENTIC_RETRIEVE = os.getenv("AGENTIC_RETRIEVE_ENABLED", "1") == "1"
AGENTIC_MAX_SUBS = int(os.getenv("AGENTIC_MAX_SUBS", "3"))

_REFORMULATE_SYS = (
    "A runbook search returned too few results for the user's question. Propose up "
    "to 3 ALTERNATIVE search queries that might surface the answer: rephrase with "
    "synonyms, break a multi-part question into sub-questions, or guess the on-screen "
    "menu/table/feature name. Keep each query short (2-8 words). "
    'Reply ONLY JSON: {"queries": ["...", "..."]}'
)


def _reformulate(query: str) -> list[str]:
    if not _client:
        return []
    try:
        r = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "system", "content": _REFORMULATE_SYS},
                      {"role": "user", "content": query[:300]}],
            max_tokens=120, temperature=0.2,
        )
        raw = (r.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
        qs = (json.loads(raw).get("queries") or [])
        out, seen = [], {query.strip().lower()}
        for q in qs:
            q = (q or "").strip()
            if q and q.lower() not in seen:
                seen.add(q.lower()); out.append(q)
        return out[:AGENTIC_MAX_SUBS]
    except Exception as e:
        print(f"[retrieve] reformulate skipped: {e!r}")
        return []


def agentic_search(query: str, k: int | None = None) -> tuple[list[dict], list[str]]:
    """Iterative retrieval. Returns (pages, subqueries_used). If the base search is
    already solid, subqueries is [] (no extra LLM/DB work)."""
    k = k or DEFAULT_K
    base = search_pages(query, k)
    if not AGENTIC_RETRIEVE or len(base) >= max(3, (k + 1) // 2):
        return base, []
    subs = _reformulate(query)
    if not subs:
        return base, []
    seen = {p["page_id"] for p in base}
    out = list(base)
    for s in subs:
        for p in search_pages(s, k):
            if p["page_id"] not in seen:
                seen.add(p["page_id"]); out.append(p)
        if len(out) >= k:
            break
    return out[: max(k, len(base))], subs


# ---------------- unified brain: pages + facts + doc summaries -----------------
# Facts are corrections the team taught; per the agent's PRECEDENCE rule they
# override the docs, so they're scored ABOVE pages and a relevant fact is
# guaranteed into the result (pages must never crowd a relevant fact out).
FACT_BOOST = float(os.getenv("BRAIN_FACT_BOOST", "2.0"))
FACT_MIN_KEEP = int(os.getenv("BRAIN_FACT_MIN_KEEP", "8"))  # always include >= this many active facts


def _search_facts(query: str, terms: list[str], limit: int) -> list[dict]:
    """Active facts ranked by relevance (trigram similarity + ILIKE on expanded
    terms). `memory` is tiny, so a full scan is cheap. When the query matches
    nothing, top recent active facts are still returned so facts never vanish
    from context. Score is boosted so a clearly-relevant fact sits near the top."""
    raw = (query or "").strip()
    ilike_terms = [t for t in terms if 2 <= len(t) <= 50][:12] or [raw or "x"]
    sql = """
    SELECT id, key, value, source, status, confidence, auto,
           similarity(value, %(raw)s) AS trg,
           (SELECT count(*) FROM unnest(%(ilike)s::text[]) term
            WHERE value ILIKE '%%' || term || '%%') AS hits
    FROM memory WHERE status = 'active'
    ORDER BY (similarity(value, %(raw)s) + 0.4 * (
                 SELECT count(*) FROM unnest(%(ilike)s::text[]) term
                 WHERE value ILIKE '%%' || term || '%%')) DESC, id DESC
    LIMIT %(lim)s
    """
    with get_conn() as conn:
        rows = conn.execute(
            sql, {"raw": raw or "x", "ilike": ilike_terms, "lim": max(limit, FACT_MIN_KEEP)}
        ).fetchall()
    out = []
    for r in rows:
        text = f"{r['key']}: {r['value']}" if r["key"] else r["value"]
        rel = float(r["trg"] or 0) + 0.4 * int(r["hits"] or 0)
        out.append({
            "type": "fact",
            "id": r["id"],
            "doc_id": None,
            "doc_name": None,
            "title": (text or "")[:60],
            "page_no": None,
            "image_path": None,
            "snippet": text,
            "text_full": text,
            "source": r["source"],
            "status": r["status"],
            "confidence": r["confidence"],
            "auto": r["auto"],
            "score": FACT_BOOST + rel,   # boosted above pages
            "_rel": rel,
        })
    return out


def _search_doc_summaries(query: str, limit: int) -> list[dict]:
    """Doc-level wiki summaries ranked by trigram similarity on title+summary."""
    raw = (query or "").strip()
    sql = """
    SELECT doc_id, title, summary,
           greatest(similarity(coalesce(title,''), %(raw)s),
                    similarity(left(coalesce(summary,''), 2000), %(raw)s)) AS trg
    FROM doc_wiki
    ORDER BY trg DESC, doc_id DESC
    LIMIT %(lim)s
    """
    with get_conn() as conn:
        rows = conn.execute(sql, {"raw": raw or "x", "lim": limit}).fetchall()
    out = []
    for r in rows:
        out.append({
            "type": "doc",
            "id": r["doc_id"],
            "doc_id": r["doc_id"],
            "doc_name": r["title"],
            "title": r["title"] or f"Document {r['doc_id']}",
            "page_no": None,
            "image_path": None,
            "snippet": (r["summary"] or "")[:180],
            "text_full": r["summary"] or "",
            "source": None,
            "status": None,
            "score": float(r["trg"] or 0),
        })
    return out


def search_brain(query: str, k: int | None = None) -> list[dict]:
    """Unified retriever over the whole brain: doc pages + taught facts + doc
    summaries, merged into ONE relevance-ranked typed list.

    Each item is a dict with at least: type ('page'|'fact'|'doc'), id, title,
    snippet, score (+ type-specific keys). Facts are scored above pages (they
    override docs) and the top relevant active facts are guaranteed in the result
    so they never get crowded out. Fail-soft: a failing sub-source is skipped."""
    k = k or DEFAULT_K
    terms = _expand(query)

    # pages — reuse the existing hybrid page search, then shape as 'page' items
    pages: list[dict] = []
    try:
        for p in search_pages(query, k=k):
            pages.append({
                "type": "page",
                "id": p["page_id"],
                "page_id": p["page_id"],
                "doc_id": p["doc_id"],
                "doc_name": p["doc_name"],
                "title": p["doc_name"],
                "page_no": p["page_no"],
                "image_path": p.get("image_path"),
                "snippet": p.get("snippet") or "",
                "text_full": p.get("text_full") or "",
                "source": None,
                "status": None,
                # page scores live below the fact boost so facts win on conflict;
                # spread by rank so page ordering from the hybrid search is kept.
                "score": 1.0,
            })
    except Exception as e:
        print(f"[brain] page search failed: {e!r}")
    # preserve the hybrid page ranking as a descending score band (< FACT_BOOST)
    for i, p in enumerate(pages):
        p["score"] = max(0.05, 1.0 - i * 0.02)

    facts: list[dict] = []
    try:
        facts = _search_facts(query, terms, limit=k)
    except Exception as e:
        print(f"[brain] fact search failed: {e!r}")

    docs: list[dict] = []
    try:
        docs = _search_doc_summaries(query, limit=max(3, k // 2))
    except Exception as e:
        print(f"[brain] doc-summary search failed: {e!r}")

    # merge + rank. Guarantee the relevant active facts survive: keep every fact
    # whose relevance cleared the bar (or the top FACT_MIN_KEEP when few exist),
    # then fill the remaining slots from pages + doc summaries by score.
    facts_sorted = sorted(facts, key=lambda f: f["score"], reverse=True)
    guaranteed = facts_sorted[:max(FACT_MIN_KEEP, 0)]
    rest = sorted(pages + docs + facts_sorted[len(guaranteed):],
                  key=lambda x: x["score"], reverse=True)
    merged = guaranteed + [x for x in rest if x not in guaranteed]
    merged.sort(key=lambda x: x["score"], reverse=True)
    # never truncate below the guaranteed facts
    cap = max(k, len(guaranteed))
    return merged[:cap]
