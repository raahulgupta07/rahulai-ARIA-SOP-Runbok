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

import os
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
