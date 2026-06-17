"""Compiled "playbook" layer — turn a compiled SOP into plain, follow-along steps.

The compiled wiki (doc_pages_md / doc_wiki, see compile_wiki.py) is faithful but
still reads like the original runbook. This module runs ONE LLM pass over that
compiled markdown and REFORMATS it into a user-facing playbook a non-expert can
follow: Goal / Who / Prerequisites / Steps / Verify / If-fails / Escalate. Every
exact code, path, SQL, filename and screen/menu/button name is kept inline
verbatim — we reformat, never invent.

Stored in doc_playbook (already exists; the driver adds it to db.py). One row per
doc, UPSERT on re-run. Fail-soft: any error -> print + return False (never raise).
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, CHAT_MODEL,
)

# PLAYBOOK_MODEL is optional (the driver may add it to config later); fall back to
# CHAT_MODEL when it's unset or empty. Read defensively so a missing attr is fine.
try:
    from .config import PLAYBOOK_MODEL  # type: ignore
except Exception:
    PLAYBOOK_MODEL = ""

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You REFORMAT one IT SOP/runbook into a plain, user-facing PLAYBOOK that a "
    "non-expert can follow step by step.\n"
    "RULES:\n"
    "- REFORMAT ONLY. Never invent, add, guess, or pad anything that is not in the "
    "source text.\n"
    "- Keep EVERY exact code, path, SQL query, filename, value, threshold and "
    "screen / menu / button / field name inline VERBATIM.\n"
    "- Write the steps as plain actions a beginner can follow, in order.\n"
    "- Preserve the original language (English or Burmese) of the document.\n"
    "- If a section has nothing in the source, return an empty list or empty string "
    "for it — do NOT make something up.\n"
    "Reply ONLY as STRICT JSON (no prose, no markdown fences), exactly this shape:\n"
    '{"goal": "...", "who": "...", "prerequisites": ["..."], '
    '"steps": [{"n": 1, "action": "...", "detail": "..."}], '
    '"verify": ["..."], "if_fails": ["..."], "escalate": "..."}'
)


_TYPE_HINT = {
    "runbook": (
        "\nThis is a RUNBOOK (incident response). Frame 'steps' as the response flow: "
        "detect/confirm the symptom, diagnose, apply the fix, then verify/rollback. "
        "'if_fails' = the rollback + escalation path."
    ),
    "policy": (
        "\nThis is a POLICY. 'goal' = what the policy governs; 'prerequisites' = who/what "
        "it applies to; 'steps' = the concrete rules and required actions (one per rule); "
        "'verify' = how compliance is checked; keep do's and don'ts explicit."
    ),
}


def _model() -> str:
    return PLAYBOOK_MODEL or CHAT_MODEL


# cap any single page's contribution so one degenerate/looped page (e.g. a 126k
# repeated-table compile) can't eat the whole window and starve the real steps.
_PER_PAGE_CAP = 6000


def _doc_text(doc_id: int) -> str:
    """Compiled text for a doc: concat doc_pages_md in page order, each page
    truncated to _PER_PAGE_CAP so no single monster page dominates. Falls back to
    doc_wiki.md only if there are no per-page rows. Empty string if neither."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, md FROM doc_pages_md WHERE doc_id = %s ORDER BY page_no",
            (doc_id,),
        ).fetchall()
        if rows:
            parts = []
            for r in rows:
                md = (r["md"] or "").strip()
                if not md:
                    continue
                if len(md) > _PER_PAGE_CAP:
                    md = md[:_PER_PAGE_CAP] + " …[truncated]"
                parts.append(f"## Page {r['page_no']}\n\n{md}")
            if parts:
                return "\n\n".join(parts).strip()
        w = conn.execute(
            "SELECT md FROM doc_wiki WHERE doc_id = %s", (doc_id,)
        ).fetchone()
    return (w.get("md") or "").strip() if w else ""


def _parse_json(raw: str) -> dict:
    """Robustly parse the model's JSON: strip ```json fences, tolerate surrounding
    prose, fall back to the first {...} block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def compile_playbook(doc_id: int, doc_type: str = "sop") -> bool:
    """Compile one doc's already-compiled text into a user-facing playbook and
    UPSERT it into doc_playbook. `doc_type` tunes the prompt (sop/runbook/policy).
    Returns True on success, False (fail-soft) on any missing input, parse failure,
    or error. Never raises."""
    try:
        if not _client:
            return False
        body = _doc_text(doc_id)
        if not body:
            return False

        sys_prompt = _SYS + _TYPE_HINT.get(doc_type or "", "")
        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": body[:40000]},
            ],
            max_tokens=4000,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, dict):
            print(f"[compile_playbook] {doc_id}: parsed JSON not an object")
            return False

        goal = (data.get("goal") or "").strip()
        who = (data.get("who") or "").strip()
        escalate = (data.get("escalate") or "").strip()

        def _list(key) -> list:
            v = data.get(key)
            return v if isinstance(v, list) else []

        prerequisites = _list("prerequisites")
        verify = _list("verify")
        if_fails = _list("if_fails")

        steps = []
        for i, st in enumerate(_list("steps"), start=1):
            if not isinstance(st, dict):
                continue
            try:
                n = int(st.get("n", i))
            except (TypeError, ValueError):
                n = i
            steps.append({
                "n": n,
                "action": (st.get("action") or "").strip(),
                "detail": (st.get("detail") or "").strip(),
            })

        playbook = {
            "goal": goal, "who": who, "prerequisites": prerequisites,
            "steps": steps, "verify": verify, "if_fails": if_fails,
            "escalate": escalate,
        }
        chars = len(json.dumps(playbook, ensure_ascii=False))

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO doc_playbook (doc_id, goal, who, prerequisites, steps, "
                "verify, if_fails, escalate, chars) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s) "
                "ON CONFLICT (doc_id) DO UPDATE SET "
                "goal = EXCLUDED.goal, who = EXCLUDED.who, "
                "prerequisites = EXCLUDED.prerequisites, steps = EXCLUDED.steps, "
                "verify = EXCLUDED.verify, if_fails = EXCLUDED.if_fails, "
                "escalate = EXCLUDED.escalate, chars = EXCLUDED.chars",
                (doc_id, goal, who, json.dumps(prerequisites), json.dumps(steps),
                 json.dumps(verify), json.dumps(if_fails), escalate, chars),
            )
        return True
    except Exception as e:
        print(f"[compile_playbook] {doc_id} failed: {e!r}")
        return False
