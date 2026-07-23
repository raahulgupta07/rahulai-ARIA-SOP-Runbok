"""Cheap, conservative off-topic gate.

Vectorless FTS always returns SOME page, so nonsense/off-topic questions
("recipe for chocolate chips", generic "new windows installation") get
confident grounded SOP answers. This gate refuses CLEARLY off-topic questions
BEFORE the answer pipeline — conservatively: a false refusal is worse than a
rare miss, so we only refuse when BOTH retrieval is weak AND the question has
no domain signal (or hits a tiny obvious-junk denylist).

Flags are read here (NOT in config.py) to keep the gate self-contained:
  SCOPE_GATE_ENABLED  (default ON)   — master switch
  SCOPE_MIN_SCORE     (default 0.15) — retrieval floor; any hit above => in scope
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

SCOPE_GATE_ENABLED: bool = os.getenv("SCOPE_GATE_ENABLED", "1") not in ("0", "false", "False", "")
# Floor sits in the GAP between out-of-corpus (capex 0.048 / SAP 0.059 / recipe
# 0.040) and in-corpus (lowest seen 0.134). 0.10 leaves room for the LLM-rerank
# non-determinism that swings a valid query's top-page score (0.35 <-> 0.13)
# without letting the clear off-corpus junk (<0.07) through.
SCOPE_MIN_SCORE: float = float(os.getenv("SCOPE_MIN_SCORE", "0.10"))

_REFUSAL = (
    "That's outside the runbooks I cover (company SOPs & IT procedures). "
    "Try asking about a specific process — e.g. CAR mass input, user "
    "creation, or a batch job."
)

# Tiny OBVIOUS off-topic denylist — secondary fast-refuse only. Kept small &
# unambiguous on purpose; the retrieval score is the primary gate.
_DENY = (
    "recipe", "chocolate", "cook", "cooking", "bake", "weather", "horoscope",
    "sports", "football", "soccer", "basketball", "joke", "celebrity",
    "lyrics", "poem", "movie", "song",
)


def _page_score(p: dict) -> float:
    """Best-effort in-scope signal for one retrieved row.

    `search_pages` rows expose the raw hybrid components (fts/node/trg/hits) but
    NOT a combined `score` field — the combined value only lives in the SQL
    ORDER BY. `search_brain` rows DO carry a synthetic `score`. Prefer an
    explicit `score` when present; otherwise reconstruct the hybrid score from
    the component columns (mirrors retrieve.search_pages' ORDER BY weights)."""
    if p.get("score") is not None:
        try:
            return float(p["score"])
        except (TypeError, ValueError):
            return 0.0
    fts = float(p.get("fts") or 0.0)
    node = float(p.get("node") or 0.0)
    trg = float(p.get("trg") or 0.0)
    hits = float(p.get("hits") or 0.0)
    return fts + 2.0 * node + 0.5 * trg + 0.3 * hits


def in_scope(q: str, search_fn: Callable[[str], list[dict]]) -> tuple[bool, str]:
    """Returns (ok, refusal_text). ok=True => proceed normally.

    Conservative: only refuse when BOTH (a) retrieval is weak (top score <
    SCOPE_MIN_SCORE or zero pages) AND (b) the question has no SOP/IT/retail
    signal. Any retrieval hit above floor => in scope. Fail-open on any error.
    """
    if not SCOPE_GATE_ENABLED:
        return True, ""

    text = (q or "").strip().lower()
    if not text:
        return True, ""  # let the pipeline handle empties

    try:
        pages = search_fn(q) or []
    except Exception as e:  # never block on an infra/search error
        print(f"[scope] search failed, failing open: {e!r}")
        return True, ""

    # (a) obvious junk (cooking/weather/sports/…) is ALWAYS refused, regardless
    # of retrieval score — a stray term can push a nonsense query above the floor.
    if any(w in text for w in _DENY):
        return False, _REFUSAL

    top = max((_page_score(p) for p in pages), default=0.0)
    # (b) retrieval has real signal => in scope (primary gate)
    if pages and top >= SCOPE_MIN_SCORE:
        return True, ""

    # Weak retrieval. `search_pages` now exposes a real relevance score, so a
    # best-page that barely matches is a strong off-corpus signal — refuse (a
    # confident hallucination over unrelated pages is the worst failure). The
    # junk denylist + zero-pages are still caught here; the change is that
    # weak-but-nonzero no longer gets the benefit of the doubt.
    return False, _REFUSAL


# ---------------- recovery: rephrase before refusing ----------------
# Broken-English / casual questions score low on literal word overlap even when
# the topic is squarely in-corpus ("patch instal step" -> 0.078). Before the
# gate refuses, ONE LLM call rewrites the question up to SCOPE_RECOVER_SUBS
# ways in runbook vocabulary; each variant is re-searched (SQL only, ~ms).
# Junk stays refused: denylist short-circuits, and off-corpus rephrasings
# still score under the floor. Cost lands ONLY on would-be refusals.
SCOPE_RECOVER_ENABLED: bool = os.getenv("SCOPE_RECOVER_ENABLED", "1") not in ("0", "false", "False", "")
SCOPE_RECOVER_SUBS: int = int(os.getenv("SCOPE_RECOVER_SUBS", "3"))
# Recovery accept-floor sits BELOW the main gate floor on purpose: the rewrites
# are short (2-3 word) doc-title queries whose raw ts_rank runs tiny (exact
# title "Patch Installation" ranks its own doc #1 at 0.097 — under the 0.10
# main floor). Junk can't exploit the lower bar: _recover_queries returns NO
# queries for off-corpus questions, so nothing reaches this comparison.
SCOPE_RECOVER_MIN_SCORE: float = float(os.getenv("SCOPE_RECOVER_MIN_SCORE", "0.05"))


_CATALOG_TTL_S = 300
_catalog_cache: tuple[float, str] = (0.0, "")


def _doc_catalog() -> str:
    """Cached one-line-per-doc title list (humanised names, 5-min TTL)."""
    global _catalog_cache
    import time as _t
    ts, val = _catalog_cache
    if _t.monotonic() - ts < _CATALOG_TTL_S and val:
        return val
    try:
        from .db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name FROM docs WHERE status IN ('ready','ready_lite') "
                "ORDER BY id").fetchall()
        names = []
        for r in rows:
            n = r["name"]
            n = re.sub(r"^SOP_[A-Z0-9_]*?_\d+_", "", n)
            n = re.sub(r"\.(pdf|png|jpe?g)$", "", n, flags=re.IGNORECASE)
            names.append(n)
        val = "\n".join(f"- {n}" for n in names)
        _catalog_cache = (_t.monotonic(), val)
    except Exception as e:
        print(f"[scope] catalog failed: {e!r}")
    return _catalog_cache[1]


def _recover_queries(q: str) -> tuple[list[str], list[str]]:
    """Catalog-grounded ROUTER: the model sees the runbook titles and returns
    (runbook_titles, search_queries). Titles are the primary signal — proven
    ~8/8 accurate on broken-English questions — and let the caller fetch the
    routed doc's pages directly, skipping the expand/rerank noise that buries
    a clean title query. If the question is unrelated to every title it must
    return NOTHING — that keeps real junk refused (a generic rephraser once
    turned "install windows 11" into wording that matched the Patch SOP)."""
    from .retrieve import _client
    from .config import CHAT_MODEL
    if not _client:
        return [], []
    cat = _doc_catalog()
    r = _client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": (
                "A user question (possibly broken English, typos, or Burmese-mixed) "
                "failed keyword search over these internal IT runbooks:\n" + cat +
                "\n\nIf the question plausibly concerns one or two of these runbooks, "
                "return their EXACT titles from the list, plus up to "
                f"{SCOPE_RECOVER_SUBS} short search queries (2-8 words) in the "
                "runbook's own vocabulary. If it is unrelated to ALL of them, return "
                'empty lists. Reply ONLY JSON: '
                '{"runbooks": ["<exact title>"], "queries": ["..."]}')},
            {"role": "user", "content": (q or "")[:300]},
        ],
        max_tokens=160, temperature=0,
    )
    raw = (r.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
    data = json.loads(raw)
    titles = [str(t).strip() for t in (data.get("runbooks") or []) if str(t).strip()][:2]
    qs = data.get("queries") or []
    out, seen = [], {(q or "").strip().lower()}
    for s in qs:
        s = (s or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return titles, out[:SCOPE_RECOVER_SUBS]


def _docs_by_titles(titles: list[str]) -> list[int]:
    """Resolve routed runbook titles back to doc ids (title is a cleaned
    substring of docs.name)."""
    ids: list[int] = []
    if not titles:
        return ids
    try:
        from .db import get_conn
        with get_conn() as conn:
            for t in titles:
                row = conn.execute(
                    "SELECT id FROM docs WHERE status IN ('ready','ready_lite') "
                    "AND name ILIKE %s ORDER BY id LIMIT 1",
                    (f"%{t[:60]}%",)).fetchone()
                if row and row["id"] not in ids:
                    ids.append(row["id"])
    except Exception as e:
        print(f"[scope] title resolve failed: {e!r}")
    return ids


def try_recover(q: str, search_fn: Callable[[str], list[dict]],
                sectors: list[int] | None = None,
                folders: list[int] | None = None) -> tuple[list[dict] | None, str | None, list[str]]:
    """Returns (pages, used_label, rephrasings). pages is None when recovery
    failed (caller refuses as before). Primary path: the router LLM names the
    runbook -> fetch that doc's best pages directly (deterministic, RBAC-scoped
    via sectors/folders). Fallback: re-search the rewritten queries through
    search_fn (a REAL search, not the cached-seed lambda of the primary gate)."""
    if not SCOPE_RECOVER_ENABLED:
        return None, None, []
    text = (q or "").strip().lower()
    if not text or any(w in text for w in _DENY):
        return None, None, []
    try:
        titles, subs = _recover_queries(q)
    except Exception as e:
        print(f"[scope] recover reformulate failed: {e!r}")
        return None, None, []
    # 1) routed runbook -> its pages, no expand/rerank noise
    doc_ids = _docs_by_titles(titles)
    if doc_ids:
        try:
            from .retrieve import pages_for_docs
            pages = pages_for_docs(doc_ids, q, k=8, sectors=sectors, folders=folders)
            if pages:
                return pages, titles[0], subs
        except Exception as e:
            print(f"[scope] routed fetch failed: {e!r}")
    # 2) fallback: rewritten queries through the normal search
    best_top, best_pages, best_q = 0.0, None, None
    for sq in subs:
        try:
            pages = search_fn(sq) or []
        except Exception:
            continue
        top = max((_page_score(p) for p in pages), default=0.0)
        if top > best_top:
            best_top, best_pages, best_q = top, pages, sq
    if best_pages and best_top >= SCOPE_RECOVER_MIN_SCORE:
        return best_pages, best_q, subs
    return None, None, subs
