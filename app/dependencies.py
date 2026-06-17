"""Cross-document PREREQUISITE detection — build a dependency graph (Phase 5).

Some runbooks/SOPs only make sense AFTER another one is done ("you must complete
SOP X — provision the site — before SOP Y — onboard users to it"). This module
runs ONE LLM pass that compares THIS document's task against a compact catalog of
every OTHER ready document and decides which of them are genuine prerequisites
(must-be-done-first), then stores those as edges in `doc_dependency`
(from_doc = this dependent doc, to_doc = the prerequisite). The agent reads them
back via `dependencies_for()` to tell a user what to do first.

Mirrors compile_lookup.py / doc_qa.py (one JSON-returning LLM call, fence
stripping + first-[...]-block fallback parse, page/id validation) and writes the
deps as a REPLACE (delete-then-insert) keyed on from_doc. Fail-soft throughout:
any error -> print + return 0 / []. Never raises.
"""
import json
import re

from .db import get_conn
from .config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, COMPILE_MODEL, CHAT_MODEL,
)

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

_SYS = (
    "You decide which OTHER documents describe a task that MUST be completed "
    "BEFORE the current document's task. Only pick a document if the current "
    "document genuinely depends on it being done first (a real prerequisite, not "
    "merely a related topic). Reply ONLY strict JSON: a list like "
    '[{"doc_id": <id from the catalog>, "reason": "short why"}]. If none, reply [].'
)

# cap the catalog so a huge corpus can't blow the context window; one compact
# line per other doc (id + title + one-line goal/summary) is all the model needs.
_CATALOG_MAX = 200
_SUMMARY_CAP = 200


def _model() -> str:
    return COMPILE_MODEL or CHAT_MODEL


def _parse_json(raw: str) -> list:
    """Robustly parse the model's JSON list: strip ```json fences, tolerate
    surrounding prose, fall back to the first [...] block. Raises on total failure."""
    s = (raw or "").strip()
    s = re.sub(r"^```(json)?|```$", "", s, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\[.*\]", s, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def _this_doc_signal(doc_id: int) -> tuple[str, str, list]:
    """Return (name, goal-or-summary, prerequisites[]) for THIS doc.

    Prefer the compiled playbook (goal + prerequisites list); fall back to the
    doc_wiki summary. ('', '', []) if the doc has no usable signal.
    """
    with get_conn() as conn:
        d = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
        pb = conn.execute(
            "SELECT goal, prerequisites FROM doc_playbook WHERE doc_id = %s",
            (doc_id,),
        ).fetchone()
        wk = conn.execute(
            "SELECT summary FROM doc_wiki WHERE doc_id = %s", (doc_id,)
        ).fetchone()
    name = (d or {}).get("name", "") or ""
    goal = ""
    prereqs: list = []
    if pb:
        goal = (pb.get("goal") or "").strip()
        raw_prereq = pb.get("prerequisites")
        if isinstance(raw_prereq, list):
            prereqs = [str(p).strip() for p in raw_prereq if str(p).strip()]
    if not goal and wk:
        goal = (wk.get("summary") or "").strip()
    return name, goal, prereqs


def _catalog(doc_id: int) -> list[dict]:
    """Other ready docs as [{id, title, goal}], goal = wiki summary or playbook goal."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name AS title, "
            "       COALESCE(w.summary, p.goal, '') AS goal "
            "FROM docs d "
            "LEFT JOIN doc_wiki w     ON w.doc_id = d.id "
            "LEFT JOIN doc_playbook p ON p.doc_id = d.id "
            "WHERE d.status = 'ready' AND d.id <> %s "
            "ORDER BY d.id",
            (doc_id,),
        ).fetchall()
    out = []
    for r in rows[:_CATALOG_MAX]:
        goal = (r.get("goal") or "").strip().replace("\n", " ")
        if len(goal) > _SUMMARY_CAP:
            goal = goal[:_SUMMARY_CAP] + " …"
        out.append({"id": r["id"], "title": (r.get("title") or "").strip(), "goal": goal})
    return out


def detect_dependencies(doc_id: int) -> int:
    """Detect cross-doc prerequisites for ONE doc and REPLACE its dependency
    edges in doc_dependency (from_doc = doc_id). Returns the number of rows
    written (0 on skip / parse-fail / error). Never raises."""
    if not _client:
        return 0
    try:
        name, goal, prereqs = _this_doc_signal(doc_id)
        if not name and not goal and not prereqs:
            return 0

        catalog = _catalog(doc_id)
        if not catalog:
            return 0
        valid_ids = {c["id"] for c in catalog}

        # numbered catalog block (id + title + goal) the model picks doc_ids from
        cat_lines = []
        for c in catalog:
            line = f"- doc_id {c['id']}: {c['title']}"
            if c["goal"]:
                line += f" — {c['goal']}"
            cat_lines.append(line)
        prereq_line = ""
        if prereqs:
            prereq_line = "\nSTATED PREREQUISITES:\n- " + "\n- ".join(prereqs)
        user = (
            f"CURRENT DOCUMENT:\nName: {name}\nGoal: {goal or '(unknown)'}"
            f"{prereq_line}\n\n"
            f"OTHER DOCUMENTS (catalog):\n" + "\n".join(cat_lines)
        )

        resp = _client.chat.completions.create(
            model=_model(),
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user},
            ],
            max_tokens=1200,
            temperature=0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _parse_json(raw)
        if not isinstance(data, list):
            print(f"[dependencies] {doc_id}: parsed JSON not a list")
            return 0

        # validate: id must be in the catalog (drop hallucinations) + not self;
        # dedupe so one picked id can't double-insert.
        picks: list[tuple[int, str]] = []
        seen: set[int] = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("doc_id"))
            except (TypeError, ValueError):
                continue
            if pid == doc_id or pid not in valid_ids or pid in seen:
                continue
            seen.add(pid)
            reason = (item.get("reason") or "").strip()
            picks.append((pid, reason))

        with get_conn() as conn:
            conn.execute("DELETE FROM doc_dependency WHERE from_doc = %s", (doc_id,))
            for pid, reason in picks:
                conn.execute(
                    "INSERT INTO doc_dependency (from_doc, to_doc, reason) "
                    "VALUES (%s, %s, %s) "
                    "ON CONFLICT (from_doc, to_doc) DO NOTHING",
                    (doc_id, pid, reason),
                )
        return len(picks)
    except Exception as e:
        print(f"[dependencies] detect {doc_id} failed: {e!r}")
        return 0


def dependencies_for(doc_id: int) -> list[dict]:
    """Return this doc's prerequisites (the docs that must be done first) as
    [{to_doc, doc_name, reason}], ordered by name. Fail-soft -> []."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT dd.to_doc, d.name AS doc_name, dd.reason "
                "FROM doc_dependency dd "
                "JOIN docs d ON d.id = dd.to_doc "
                "WHERE dd.from_doc = %s "
                "ORDER BY d.name",
                (doc_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[dependencies] dependencies_for {doc_id} failed: {e!r}")
        return []
