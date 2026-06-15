"""Agent auto-writes learnings mid-chat (Scout-style write-back, no ML).

Two layers, both land facts as 'pending' (Intern Rule) unless auto-approved:

  Layer A — REACTIVE (always on): intent-gated. Only when the user's message
    looks like teaching/correcting ("remember…", "from now on…", မှတ်ထား) do we
    run the extraction LLM. Zero extra cost on normal questions.

  Layer B — ALWAYS-EXTRACT (flag AUTO_LEARN_ENABLED, default OFF): mines EVERY
    answer for a durable fact the USER asserted. Confidence-gated, deduped, and
    capped per conversation so the review queue never floods.
"""
import json
import re

from .config import (
    OPENROUTER_API_KEY, CHAT_MODEL, OPENROUTER_BASE_URL,
    AUTO_LEARN_ENABLED, AUTO_LEARN_MIN_CONF, AUTO_APPROVE_MIN_CONF,
    AUTO_LEARN_MAX_PER_CONV,
)
from .memory import add_memory, find_similar
from .db import get_conn, log_ingest

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

# English teach/correct/remember intent + a few Burmese equivalents.
_INTENT = re.compile(
    r"\b(remember|note that|keep in mind|don'?t forget|for future|"
    r"from now on|always|actually it'?s|correction|i meant|"
    r"the (correct|right) (answer|value|one) is|fyi|save this|"
    r"add to (the )?brain|teach you|just so you know)\b"
    r"|မှတ်ထား|မှတ်သား|မမေ့",
    re.IGNORECASE,
)

_PROMPT = (
    "You decide whether the USER taught the assistant a DURABLE fact, rule, or "
    "correction worth remembering for future conversations (e.g. a policy, a "
    "preferred value, a name/owner, a correction to a previous answer).\n"
    "Do NOT save: one-off questions, chit-chat, anything already obvious from "
    "the documents, transient context, or the assistant's own answer.\n"
    "If yes, rewrite it as ONE concise standalone fact (no 'the user said'). "
    "Give a confidence 0-1 for how clearly the USER asserted a durable fact.\n"
    'Reply ONLY as JSON: {"save": true|false, "fact": "<one fact or empty>", "confidence": 0.0-1.0}'
)


def wants_to_teach(q: str) -> bool:
    """Cheap gate so the Layer-A extraction LLM only runs on plausible teaching turns."""
    return bool(_INTENT.search(q or ""))


def _extract(query: str, answer: str) -> tuple[str | None, float]:
    """Run the extraction LLM. Returns (fact|None, confidence)."""
    if not _client:
        return None, 0.0
    try:
        resp = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user",
                 "content": f"USER MESSAGE:\n{query}\n\nASSISTANT ANSWER:\n{answer[:1500]}"},
            ],
            max_tokens=180, temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.IGNORECASE).strip()
        data = json.loads(raw)
        if data.get("save") and (fact := (data.get("fact") or "").strip()):
            try:
                conf = float(data.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            return fact, max(0.0, min(1.0, conf))
    except Exception as e:
        print(f"[learn] extract skipped: {e!r}")
    return None, 0.0


def _is_questionish(fact: str) -> bool:
    """Drop extracts that are actually questions, not assertions."""
    f = (fact or "").strip().lower()
    return f.endswith("?") or f.startswith(("how ", "what ", "why ", "where ", "when ", "who ", "is ", "are ", "can "))


def _save(fact: str, conf: float, created_by: str | None, query: str,
          auto: bool, is_admin: bool) -> str | None:
    """Dedupe + confidence/quality checks, then land the fact. Returns fact or None."""
    if not fact or _is_questionish(fact):
        return None
    if find_similar(fact):                       # near-duplicate of an existing fact
        return None
    # approval policy (Settings → governance): per-source auto-approve vs review.
    from .governance import decide_status
    status = decide_status("chat", conf, is_admin=is_admin)
    mem_id = add_memory(fact, source="chat", created_by=created_by, status=status,
                        origin_question=query, confidence=conf, auto=auto)
    tag = "auto" if auto else "learned"
    state = "active" if status == "active" else "pending review"
    log_ingest(None, "learn", f'{tag} ({state}): "{fact[:120]}"')
    if status == "active":                        # audit-trail the auto-approve
        try:
            from . import notify
            notify.emit("memory", "Fact auto-approved", f"#{mem_id} now active", "info",
                        dedupe_hours=1)
        except Exception:
            pass
    return fact


def maybe_learn(query: str, answer: str, created_by: str | None = None) -> str | None:
    """Layer A — reactive, intent-gated. Returns saved fact or None."""
    if not wants_to_teach(query):
        return None
    fact, conf = _extract(query, answer)
    if not fact:
        return None
    # hand-signalled teaching is trusted → confidence floor, not auto.
    return _save(fact, max(conf, 0.6), created_by, query, auto=False, is_admin=False)


def _conv_auto_count(created_by: str | None) -> int:
    """Flood guard: how many auto facts this user produced in the last hour."""
    if not created_by:
        return 0
    try:
        with get_conn() as conn:
            return conn.execute(
                "SELECT count(*) AS n FROM memory WHERE auto = true AND created_by = %s "
                "AND created_at > now() - interval '1 hour'",
                (created_by,),
            ).fetchone()["n"]
    except Exception:
        return 0


def auto_learn(query: str, answer: str, created_by: str | None = None,
               is_admin: bool = False) -> str | None:
    """Layer B — always-extract (flag-gated). Mines every answer. Returns fact or None."""
    if not AUTO_LEARN_ENABLED:
        return None
    q = (query or "").strip()
    if len(q) < 12:                               # too short to assert a durable fact
        return None
    if _conv_auto_count(created_by) >= AUTO_LEARN_MAX_PER_CONV:
        return None                               # flood cap
    fact, conf = _extract(query, answer)
    if not fact or conf < AUTO_LEARN_MIN_CONF:    # confidence gate
        return None
    return _save(fact, conf, created_by, query, auto=True, is_admin=is_admin)


# ---- Phase E: nightly batch (AUTO_LEARN_MODE=nightly) ----
def auto_learn_batch(hours: int = 24) -> int:
    """Mine the last `hours` of user messages for durable facts. Returns count saved.
    Cheaper than per-answer: runs once/day instead of on every turn."""
    if not AUTO_LEARN_ENABLED:
        return 0
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT m.text AS q, u.email AS email "
                "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                "LEFT JOIN users u ON u.id = c.user_id "
                "WHERE m.role = 'user' AND m.text IS NOT NULL "
                "AND m.created_at > now() - (%s || ' hours')::interval "
                "ORDER BY m.created_at",
                (str(hours),),
            ).fetchall()
    except Exception as e:
        print(f"[learn] batch query failed: {e!r}")
        return 0
    saved = 0
    for r in rows:
        try:
            if auto_learn(r["q"], "", r.get("email"), is_admin=False):  # batch never auto-approves
                saved += 1
        except Exception:
            continue
    if saved:
        log_ingest(None, "learn", f"nightly auto-learn proposed {saved} fact(s) for review")
    return saved


def start_auto_learn_daemon() -> None:
    """Daemon: nightly batch mining when AUTO_LEARN_ENABLED and MODE=nightly."""
    from .config import AUTO_LEARN_MODE
    if not AUTO_LEARN_ENABLED or AUTO_LEARN_MODE != "nightly":
        return
    import threading
    import time

    def loop():
        time.sleep(60)  # let boot settle
        while True:
            try:
                auto_learn_batch(24)
            except Exception as e:
                print(f"[learn] daemon tick failed: {e!r}")
            time.sleep(24 * 3600)

    threading.Thread(target=loop, daemon=True, name="auto-learn").start()
    print("[learn] nightly auto-learn daemon started")
