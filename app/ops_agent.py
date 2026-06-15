"""AI admin agent (Vercel knowledge-agent 'admin agent' idea).

Answers operational questions in natural language — "how many blind spots this
week? which docs are cold? what's the coverage trend?" — by assembling a LIVE
metrics snapshot from the existing analytics/audit/dashboard modules and letting
a small LLM read it. NO raw SQL, NO chat text → safe + privacy-preserving.
Admin-only, flag-gated (OPS_AGENT_ENABLED).
"""
import json

from .config import OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL, OPS_AGENT_ENABLED

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def snapshot(days: int = 30) -> dict:
    """Gather a compact, fail-soft metrics snapshot. Counts/aggregates only."""
    snap: dict = {"window_days": days}

    def grab(key, fn):
        try:
            snap[key] = fn()
        except Exception as e:
            snap[key] = {"error": str(e)[:120]}

    from . import dashboard, analytics, audit, ops
    grab("cockpit", lambda: dashboard.cockpit(days))
    grab("corpus", lambda: analytics.corpus())
    grab("learning", lambda: analytics.learning(days))
    grab("doc_performance", lambda: analytics.doc_performance(days))
    grab("coverage_audit", lambda: audit.coverage_report(blind_limit=10, days=days))
    grab("system", lambda: ops.report())
    return snap


_SYS = (
    "You are the operations analyst for 'Aria', an internal runbook/SOP assistant. "
    "Answer the admin's question using ONLY the JSON metrics snapshot provided. "
    "Be concise and concrete: cite the actual numbers, name specific documents/gaps "
    "when relevant, and give one short actionable recommendation if useful. "
    "If the snapshot doesn't contain the answer, say so plainly — never invent data. "
    "Plain text, no markdown headers."
)


def ask(question: str, days: int = 30) -> dict:
    """Answer an admin ops question from the live snapshot. Returns {answer, sources}."""
    if not OPS_AGENT_ENABLED:
        return {"answer": "The ops agent is disabled (set OPS_AGENT_ENABLED=1).", "sources": []}
    if not _client:
        return {"answer": "No LLM configured.", "sources": []}
    snap = snapshot(days)
    try:
        r = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content":
                 f"QUESTION: {question}\n\nMETRICS SNAPSHOT (last {days} days):\n"
                 + json.dumps(snap, default=str)[:14000]},
            ],
            max_tokens=500, temperature=0.1,
        )
        answer = (r.choices[0].message.content or "").strip()
    except Exception as e:
        return {"answer": f"Could not answer right now: {e}", "sources": []}
    # which snapshot sections actually had data (for transparency)
    sources = [k for k, v in snap.items() if isinstance(v, dict) and "error" not in v]
    return {"answer": answer, "sources": sources}
