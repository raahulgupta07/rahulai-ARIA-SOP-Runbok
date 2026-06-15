"""Phase 3 — gap-driven Q&A daemon.

Where Phase 1 mines Q&A from docs and Phase 2 harvests them from upvoted chat,
this fills the bank where users actually struggle: it reads the demand/coverage
GAPS (keywords.keyword_analysis — terms asked a lot but answered without a source)
and, for each, synthesizes the natural question, runs the REAL answer pipeline to
get a grounded answer, and stores it as a pending Q&A pair for review.

Honest by construction:
- only saves a pair when retrieval finds grounding (cited pages) — never invents.
- rate-capped per day (AUTO_QA_GAP_PER_DAY) so the bank trickles, never floods.
- pairs land 'pending' (source='gap', governance) — a human approves before they serve.

Default OFF (AUTO_QA_GAP_ENABLED) because each gap costs LLM (question synth +
answer pipeline). Dedup by question is handled in qa.add_qa.
"""
import threading
import time

from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, CHAT_MODEL,
    AUTO_QA_GAP_ENABLED, AUTO_QA_GAP_PER_DAY, AUTO_QA_GAP_INTERVAL_H,
)
from .db import get_conn, log_ingest

_started = False
_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYNTH_SYS = (
    "You turn a frequently-asked TOPIC keyword into the single most likely natural "
    "question a user of an IT SOP/runbook assistant would actually type about it. "
    "Reply with ONLY the question text — no quotes, no preamble. Keep the user's "
    "language (English or Burmese)."
)


_NONANSWER = (
    "do not contain", "does not contain", "not contain", "no information",
    "not found", "could not find", "couldn't find", "cannot find", "can't find",
    "not mentioned", "not specified", "not covered", "no instructions",
    "i'm sorry", "i am sorry", "no source", "unable to",
    # Burmese: "မပါ" (not included) / "မတွေ့" (not found)
    "မပါ", "မတွေ့",
)


def _is_nonanswer(text: str) -> bool:
    """True if the answer is really 'the docs don't cover this' — not worth banking."""
    t = (text or "").lower()
    return any(k in t for k in _NONANSWER)


def _added_today() -> int:
    """How many gap-sourced pairs were created since midnight (per-day cap)."""
    try:
        with get_conn() as conn:
            r = conn.execute(
                "SELECT count(*) AS n FROM qa_pairs "
                "WHERE source = 'gap' AND created_at >= date_trunc('day', now())"
            ).fetchone()
        return int(r["n"])
    except Exception:
        return 0


def _synth_question(term: str) -> str:
    if not _client:
        return ""
    try:
        resp = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYNTH_SYS},
                {"role": "user", "content": f"TOPIC: {term}"},
            ],
            max_tokens=60, temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip().strip('"').strip()
    except Exception as e:
        print(f"[auto_qa_gaps] synth failed for {term!r}: {e!r}")
        return ""


def run_gap_fill() -> int:
    """One pass: top gaps → grounded Q&A pairs (pending), up to today's remaining cap.
    Returns count saved. Fail-soft."""
    if not _client:
        return 0
    remaining = AUTO_QA_GAP_PER_DAY - _added_today()
    if remaining <= 0:
        return 0

    from .keywords import keyword_analysis
    try:
        gaps = (keyword_analysis(days=30).get("gaps") or [])
    except Exception as e:
        print(f"[auto_qa_gaps] keyword_analysis failed: {e!r}")
        return 0
    if not gaps:
        return 0

    from .retrieve import search_pages
    from .agent import answer as agent_answer
    from .qa import add_qa, find_similar_qa
    from .governance import decide_status

    saved = 0
    for g in gaps:
        if saved >= remaining:
            break
        term = (g.get("term") or "").strip()
        if len(term) < 3:
            continue
        q = _synth_question(term)
        if len(q) < 6:
            continue
        # skip if we already have a near-identical question (saves the answer LLM call)
        if find_similar_qa(q):
            continue
        try:
            seed = search_pages(q, k=8)
        except Exception as e:
            print(f"[auto_qa_gaps] search failed for {q!r}: {e!r}")
            continue
        if not seed:
            continue                     # no grounding → honest skip
        try:
            res = agent_answer(q, seed, mode="quick")
        except Exception as e:
            print(f"[auto_qa_gaps] answer failed for {q!r}: {e!r}")
            continue
        a = (res.get("text") or "").strip()
        page_ids = res.get("page_ids") or []
        if len(a) < 12 or not page_ids:
            continue                     # ungrounded / empty → skip
        if _is_nonanswer(a):
            continue                     # "the docs don't cover this" → not worth banking
        status = decide_status("gap", None, is_admin=False)
        try:
            qid = add_qa(q, a, source="gap", status=status, confidence=None,
                         doc_id=None, page_ids=page_ids,
                         created_by="daemon:gap",
                         origin=f"From a coverage gap: \"{term}\"")
            if qid:
                saved += 1
        except Exception as e:
            print(f"[auto_qa_gaps] save failed: {e!r}")
    if saved:
        log_ingest(None, "qa", f"❓ {saved} gap-driven Q&A pair(s) for review")
    return saved


def _loop():
    time.sleep(90)                       # let boot settle
    while True:
        try:
            run_gap_fill()
        except Exception as e:
            print(f"[auto_qa_gaps] loop skipped: {e!r}")
        time.sleep(max(1, AUTO_QA_GAP_INTERVAL_H) * 3600)


def start() -> None:
    """Launch the gap-fill thread once (called from app lifespan)."""
    global _started
    if _started or not AUTO_QA_GAP_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, name="auto-qa-gaps", daemon=True).start()
    print(f"[auto_qa_gaps] on — up to {AUTO_QA_GAP_PER_DAY}/day, every {AUTO_QA_GAP_INTERVAL_H}h")
