"""Auto complexity routing — pick quick vs deep per question (Vercel knowledge-agent
idea). Cheap heuristic first (zero cost), optional small-LLM tie-breaker. Deep =
reads page images + navigates outline (slower, pricier); quick = text-only.

User's explicit Quick/Deep always wins; routing only runs when mode == 'auto'.
"""
import re

from .config import (
    OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL,
    AUTO_ROUTE_ENABLED, AUTO_ROUTE_LLM,
)

# signals a question needs deep (multi-step / visual / synthesis) reasoning
_DEEP = re.compile(
    r"\b(step[\s-]?by[\s-]?step|walk me through|how do i|how to|procedure|"
    r"configure|set ?up|troubleshoot|diagnose|compare|difference between|"
    r"vs\.?|all the ways|every (method|option|step)|end[\s-]?to[\s-]?end|"
    r"workflow|reconcile|why does|explain|screenshot|which (screen|menu|tab|field))\b"
    r"|�‌ဘယ်လို|အဆင့်ဆင့်",   # Burmese: "how" / "step by step"
    re.IGNORECASE,
)
# strong quick signals: short lookups / single facts
_QUICK = re.compile(
    r"\b(what is|who is|when is|where is|extension|hotline|phone|email|"
    r"contact|deadline|date|code for|value of|define)\b",
    re.IGNORECASE,
)

_client = None
if OPENROUTER_API_KEY and AUTO_ROUTE_LLM:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def _heuristic(q: str) -> str | None:
    """Fast, free pass. Returns 'quick'|'deep' or None if unsure."""
    t = (q or "").strip()
    words = len(t.split())
    if _DEEP.search(t):
        return "deep"
    if _QUICK.search(t) and words <= 14:
        return "quick"
    if words <= 8:                      # short → almost always a lookup
        return "quick"
    if words >= 28:                     # long, elaborate → likely multi-part
        return "deep"
    return None


def _llm(q: str) -> str:
    if not _client:
        return "quick"
    try:
        r = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content":
                 "Classify the user's question difficulty for a runbook assistant. "
                 "Reply ONE word: 'deep' if it needs multi-step procedures, comparisons, "
                 "synthesis across pages, or reading screenshots; else 'quick' for a "
                 "single-fact lookup."},
                {"role": "user", "content": q[:400]},
            ],
            max_tokens=2, temperature=0.0,
        )
        return "deep" if "deep" in (r.choices[0].message.content or "").lower() else "quick"
    except Exception:
        return "quick"


def resolve_mode(q: str, requested: str) -> tuple[str, bool]:
    """Resolve the effective mode. Returns (mode, routed):
       - requested 'quick'/'deep' → honored as-is (routed=False)
       - requested 'auto' (and routing on) → classify → ('quick'|'deep', True)
       - routing off → defaults to 'quick'."""
    if requested in ("quick", "deep"):
        return requested, False
    if not AUTO_ROUTE_ENABLED:
        return "quick", False
    m = _heuristic(q)
    if m is None:
        m = _llm(q)
    return m, True
