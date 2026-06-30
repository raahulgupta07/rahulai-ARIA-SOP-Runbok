"""Two pure helpers that fix real answer-quality bugs in the chat pipeline.

FIX 1 — grounding (`cited_page_ids`)
    Groundedness was derived ONLY from the trailing `PAGES:` line the model is
    asked to emit. The model frequently omits that line (especially on long or
    Burmese answers) even when it cites pages inline with `[N]` markers — so a
    genuinely-sourced answer gets flagged "unsourced" and the spurious blind-spot
    banner fires. This collects the cited page_ids from BOTH signals: the inline
    `[N]` markers AND the `PAGES:` line, mapping 1-based source numbers to real
    page_ids exactly like `agent.map_cited` (re-implemented locally to avoid an
    import cycle). An answer is only "blind" when it truly cites nothing valid.

FIX 2 — topic contamination (`resolve_query`)
    The follow-up heuristic was length-based (`<=6 words OR regex`), so any short
    NEW-topic question ("new windows installation", "why such error") was treated
    as a follow-up: the previous question got folded into retrieval via
    `_search_query` AND prior turns were injected into the prompt, so the agent
    answered the OLD topic. We now fold ONLY when the question carries an explicit
    anaphoric / continuation cue (followup_re: that/it/above/more/detail/why/…).
    A bare new topic therefore stands on its own — if it has no real source it
    honestly hits the blind-spot banner instead of confidently answering the
    previous topic. (Score-based "is this a strong standalone topic" probing was
    rejected: a bare follow-up "and site B?" and a bare new topic both retrieve
    weakly, so absolute score can't tell them apart; a confidently-wrong answer is
    the worse failure, so the tie breaks toward NOT folding.)

Both functions are pure (no module-level side effects, no DB, no LLM) and take
their collaborators (followup_re) by argument so this module imports nothing
from agent/retrieve/routes.
"""
from __future__ import annotations

import re

_INLINE_RE = re.compile(r"\[(\d{1,3})\]")
_PAGES_RE = re.compile(r"PAGES:\s*([0-9, ]+|none)", re.IGNORECASE)


def cited_page_ids(answer_text: str, seed_pages: list[dict]) -> list[int]:
    """All real page_ids the answer actually cites, derived from the inline [N]
    markers AND the trailing PAGES: line (the model reliably emits both for a
    grounded answer; `PAGES: none` + no [N] => genuinely ungrounded). 1-based
    source numbers -> page_ids via the same mapping as agent.map_cited.
    De-duplicated, order-preserved. Returns [] only when the answer cites nothing
    that maps to a seed page.

    NOTE: bare un-bracketed trailing numbers are deliberately NOT counted — they
    false-ground hallucinations (a made-up steppy answer over unrelated seed
    pages has trailing list-numbers that would otherwise map), masking the very
    blind spots this is meant to surface."""
    text = answer_text or ""
    nums: list[int] = [int(n) for n in _INLINE_RE.findall(text)]
    m = _PAGES_RE.search(text)
    if m and m.group(1).strip().lower() != "none":
        nums += [int(x) for x in re.findall(r"\d+", m.group(1))]

    out: list[int] = []
    seen: set[int] = set()
    for n in nums:
        if 1 <= n <= len(seed_pages):
            pid = seed_pages[n - 1]["page_id"]
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def resolve_query(q: str, history: list[dict], *,
                  followup_re) -> tuple[str, bool]:
    """Decide the retrieval query + whether to inject conversation history.
    history = list of {role, text}. followup_re = the anaphora/continuation cue
    pattern. Returns (search_query, use_history).

    Fold the prior user question + use history ONLY when q carries an explicit
    follow-up cue. A bare question (even a short one) is treated as a fresh topic
    so it can't inherit the previous topic's context."""
    if not followup_re.search(q or ""):
        return q, False
    prior = next(
        (m["text"] for m in reversed(history or [])
         if m.get("role") == "user" and (m.get("text") or "").strip()),
        None,
    )
    if not prior:
        return q, False
    return f"{prior}\n{q}", True
