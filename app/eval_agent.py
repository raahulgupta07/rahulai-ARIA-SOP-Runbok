"""Eval Agent — offline answer-quality scoring (the nightly lane).

Separate from upload + answer paths → zero runtime cost. For every 'ready' doc:

  1. golden questions (auto-generated once, then reused):
       positive    — answer IS in the doc        → agent must answer correctly
       negative    — answer is NOT in the doc     → agent must REFUSE (hallucination trap)
       adversarial — a false premise              → agent must correct it
  2. the agent answers each question through the REAL retrieval+answer path
  3. an LLM judge grades each answer → pass | partial | fail (+ reason)
  4. per-doc score + corpus health are rolled up into doc_health / eval_run.

Triggered nightly at EVAL_HOUR (server local time) and on demand ("Run now").
Incremental: nightly only re-scores docs changed since their last eval (watermark);
a manual run scores everything. Leader-gated via background.start_all so exactly
one process runs it. Pausable. Never raises into anything user-facing.
"""

import json
import re
import threading
import time
from datetime import datetime

from .db import get_conn
from .config import (
    EVAL_ENABLED, EVAL_HOUR, EVAL_CONCURRENCY, EVAL_POLL_SECONDS,
    EVAL_QUESTIONS_PER_DOC, EVAL_JUDGE_MODEL, CHAT_MODEL,
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
)

_started = False
_lock = threading.Lock()
_scoring: set[int] = set()          # doc_ids being scored right now (this process)
_paused = False
_concurrency = EVAL_CONCURRENCY
_run_active: dict | None = None     # {"id","trigger","total","done"} (this process's live pass)
_last_tick_hour = -1                # nightly de-dupe: hour we last auto-fired in

# the LLM the judge + question-gen use (display + calls). CHAT_MODEL = the live
# answering model; the judge model defaults to it unless EVAL_JUDGE_MODEL is set.
JUDGE_MODEL = EVAL_JUDGE_MODEL or CHAT_MODEL
ANSWER_MODEL = CHAT_MODEL

_client = None
if OPENROUTER_API_KEY:
    from openai import OpenAI
    _client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL,
                     timeout=90.0, max_retries=2)


# ── controls (called from routes) ──────────────────────────────────────────────
def is_paused() -> bool:
    return _paused


def pause() -> None:
    global _paused
    _paused = True


def resume() -> None:
    global _paused
    _paused = False


def set_concurrency(n: int) -> int:
    global _concurrency
    _concurrency = max(1, min(int(n), 8))
    return _concurrency


def force_run() -> bool:
    """Queue a manual eval pass (scores ALL ready docs). DB-backed so it works no
    matter which uvicorn worker serves the request — the leader's loop claims the
    queued row within EVAL_POLL_SECONDS. Returns False if a run is already pending."""
    with get_conn() as conn:
        busy = conn.execute(
            "SELECT 1 FROM eval_run WHERE status IN ('queued','running') LIMIT 1"
        ).fetchone()
        if busy:
            return False
        conn.execute(
            "INSERT INTO eval_run (trigger, judge_model, status, n_docs) "
            "VALUES ('manual', %s, 'queued', 0)", (JUDGE_MODEL,),
        )
    return True


# ── LLM helpers ─────────────────────────────────────────────────────────────────
def _chat(model: str, system: str, user: str, max_tokens: int = 900) -> str:
    if _client is None:
        return ""
    r = _client.chat.completions.create(
        model=model, temperature=0,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens,
    )
    return (r.choices[0].message.content or "").strip()


def _json_block(text: str):
    """Tolerant JSON extractor — pulls the first {...} / [...] out of an LLM reply."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


# ── golden questions ─────────────────────────────────────────────────────────────
_GEN_SYS = (
    "You write evaluation questions to test a document-grounded Q&A assistant. "
    "You are given excerpts of ONE document. Produce a JSON array of question objects, "
    "each: {\"question\":str, \"expected\":str, \"kind\":\"positive\"|\"negative\"|\"adversarial\"}.\n"
    "- positive: answerable from the excerpts. expected = the correct concise answer.\n"
    "- negative: plausible for this topic but NOT covered in the excerpts. "
    "expected = \"REFUSE — not in document\". The assistant MUST decline these.\n"
    "- adversarial: states a false premise contradicting the document. "
    "expected = the correction grounded in the document.\n"
    "Return ONLY the JSON array, no prose."
)


def _doc_excerpt(doc_id: int, limit_chars: int = 9000) -> str:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, COALESCE(NULLIF(vision_text,''), text) AS body "
            "FROM pages WHERE doc_id=%s ORDER BY page_no LIMIT 40",
            (doc_id,),
        ).fetchall()
    out, used = [], 0
    for r in rows:
        body = (r["body"] or "").strip()
        if not body:
            continue
        chunk = f"[p.{r['page_no']}] {body}"
        out.append(chunk)
        used += len(chunk)
        if used >= limit_chars:
            break
    return "\n\n".join(out)


def _ensure_questions(doc_id: int, name: str) -> list[dict]:
    """Return active golden questions for a doc, generating them once if absent."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, question, expected, kind FROM eval_question "
            "WHERE doc_id=%s AND active ORDER BY id", (doc_id,),
        ).fetchall()
    if rows:
        return [dict(r) for r in rows]

    excerpt = _doc_excerpt(doc_id)
    if not excerpt:
        return []
    n = EVAL_QUESTIONS_PER_DOC
    user = (f"Document: {name}\nGenerate {n} questions "
            f"(~{max(1, n // 2)} positive, ~{max(1, n // 4)} negative, "
            f"~{max(1, n // 4)} adversarial).\n\nExcerpts:\n{excerpt}")
    parsed = _json_block(_chat(JUDGE_MODEL, _GEN_SYS, user, max_tokens=1600)) or []
    saved = []
    with get_conn() as conn:
        for q in parsed:
            if not isinstance(q, dict) or not q.get("question"):
                continue
            kind = q.get("kind") if q.get("kind") in ("positive", "negative", "adversarial") else "positive"
            row = conn.execute(
                "INSERT INTO eval_question (doc_id, question, expected, kind, source) "
                "VALUES (%s,%s,%s,%s,'auto') RETURNING id, question, expected, kind",
                (doc_id, str(q["question"])[:1000], str(q.get("expected") or "")[:2000], kind),
            ).fetchone()
            saved.append(dict(row))
    print(f"[eval] generated {len(saved)} golden questions for doc {doc_id} ({name})")
    return saved


# ── ask + judge one question ──────────────────────────────────────────────────────
_JUDGE_SYS = (
    "You are a strict grader for a document-grounded assistant. Given a QUESTION, "
    "its EXPECTED answer/behaviour, the assistant's ACTUAL answer, and whether any "
    "document pages were RETRIEVED, return ONLY JSON: "
    "{\"verdict\":\"pass\"|\"partial\"|\"fail\", \"score\":0..1, \"grounded\":bool, "
    "\"refused\":bool, \"reason\":str}.\n"
    "- positive: pass if the actual answer matches the expected facts.\n"
    "- negative: the assistant MUST refuse / say it's not in the document. "
    "pass if it refused; FAIL (hallucination) if it fabricated an answer. set refused accordingly.\n"
    "- adversarial: pass if it corrects the false premise; fail if it accepts it.\n"
    "reason = one short sentence."
)


def _judge(q: dict, got: str, retrieved_n: int) -> dict:
    user = (f"KIND: {q.get('kind')}\nQUESTION: {q.get('question')}\n"
            f"EXPECTED: {q.get('expected')}\nRETRIEVED_PAGES: {retrieved_n}\n"
            f"ACTUAL ANSWER:\n{got}")
    parsed = _json_block(_chat(JUDGE_MODEL, _JUDGE_SYS, user, max_tokens=400)) or {}
    verdict = parsed.get("verdict")
    if verdict not in ("pass", "partial", "fail"):
        verdict = "fail"
    try:
        score = float(parsed.get("score"))
    except Exception:
        score = {"pass": 1.0, "partial": 0.5, "fail": 0.0}[verdict]
    return {
        "verdict": verdict,
        "score": max(0.0, min(1.0, score)),
        "grounded": bool(parsed.get("grounded")),
        "refused": bool(parsed.get("refused")),
        "reason": str(parsed.get("reason") or "")[:500],
    }


def _ask_agent(question: str) -> tuple[str, int]:
    """Answer a question through the real retrieval + answer path. Returns
    (answer_text, n_pages_retrieved)."""
    from . import agent, retrieve
    seed = retrieve.search_pages(question, k=8)   # corpus-wide, like a real user
    if not seed:
        return ("", 0)
    res = agent.answer(question, seed, mode="quick")
    return (res.get("text") or "", len(seed))


# ── score one doc ─────────────────────────────────────────────────────────────────
def _score_doc(run_id: int, doc_id: int, name: str) -> dict | None:
    qs = _ensure_questions(doc_id, name)
    if not qs:
        print(f"[eval] doc {doc_id} ({name}) — no questions, skipped")
        return None
    n_pass = n_fail = n_part = 0
    grounded_hits = neg_total = neg_caught = halluc = 0
    for q in qs:
        try:
            got, rn = _ask_agent(q["question"])
            j = _judge(q, got, rn)
        except Exception as e:
            print(f"[eval] q{q.get('id')} doc {doc_id} error: {e!r}")
            continue
        v = j["verdict"]
        if v == "pass":
            n_pass += 1
        elif v == "partial":
            n_part += 1
        else:
            n_fail += 1
        if j["grounded"]:
            grounded_hits += 1
        if q.get("kind") == "negative":
            neg_total += 1
            if j["refused"] and v == "pass":
                neg_caught += 1
            else:
                halluc += 1
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO eval_result (run_id, doc_id, question_id, question, kind, "
                "expected, got, verdict, judge_score, judge_reason, grounded, refused, retrieved_n) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, doc_id, q.get("id"), q["question"], q.get("kind"),
                 q.get("expected"), got[:6000], v, j["score"], j["reason"],
                 j["grounded"], j["refused"], rn),
            )
    n_total = n_pass + n_fail + n_part
    score = round(100.0 * (n_pass + 0.5 * n_part) / n_total, 1) if n_total else 0.0
    grounded_pct = round(100.0 * grounded_hits / n_total, 1) if n_total else 0.0
    needs_review = score < 70 or halluc > 0
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO doc_health (doc_id, last_run_id, score_pct, prev_score, n_total, "
            "n_pass, n_fail, n_partial, grounded_pct, halluc_count, neg_total, neg_caught, "
            "needs_review, evaluated_at) "
            "VALUES (%s,%s,%s,(SELECT score_pct FROM doc_health WHERE doc_id=%s),"
            "%s,%s,%s,%s,%s,%s,%s,%s,%s,now()) "
            "ON CONFLICT (doc_id) DO UPDATE SET last_run_id=EXCLUDED.last_run_id, "
            "prev_score=doc_health.score_pct, score_pct=EXCLUDED.score_pct, "
            "n_total=EXCLUDED.n_total, n_pass=EXCLUDED.n_pass, n_fail=EXCLUDED.n_fail, "
            "n_partial=EXCLUDED.n_partial, grounded_pct=EXCLUDED.grounded_pct, "
            "halluc_count=EXCLUDED.halluc_count, neg_total=EXCLUDED.neg_total, "
            "neg_caught=EXCLUDED.neg_caught, needs_review=EXCLUDED.needs_review, "
            "evaluated_at=now()",
            (doc_id, run_id, score, doc_id, n_total, n_pass, n_fail, n_part,
             grounded_pct, halluc, neg_total, neg_caught, needs_review),
        )
    print(f"[eval] doc {doc_id} ({name}) scored {score}% "
          f"({n_pass}/{n_total} pass, {halluc} halluc)")
    return {"n_total": n_total, "n_pass": n_pass, "n_fail": n_fail, "n_partial": n_part}


# ── a full pass ──────────────────────────────────────────────────────────────────
def _docs_to_eval(incremental: bool) -> list[dict]:
    """Ready docs to score. Incremental (nightly) skips docs unchanged since their
    last eval (updated_at <= doc_health.evaluated_at). Manual scores all."""
    sql = ("SELECT d.id, d.name FROM docs d LEFT JOIN doc_health h ON h.doc_id=d.id "
           "WHERE d.status='ready' ")
    if incremental:
        sql += "AND (h.evaluated_at IS NULL OR d.updated_at > h.evaluated_at) "
    sql += "ORDER BY d.id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def _claim_queued() -> dict | None:
    """Atomically claim a queued manual run (any worker may have inserted it)."""
    with get_conn() as conn:
        row = conn.execute(
            "UPDATE eval_run SET status='running', started_at=now() "
            "WHERE id = (SELECT id FROM eval_run WHERE status='queued' "
            "            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, trigger"
        ).fetchone()
    return dict(row) if row else None


def _run_pass(trigger: str, run_id: int | None = None) -> None:
    global _run_active
    incremental = (trigger == "nightly")
    docs = _docs_to_eval(incremental)
    with get_conn() as conn:
        if run_id is None:
            run = conn.execute(
                "INSERT INTO eval_run (trigger, judge_model, status, n_docs) "
                "VALUES (%s,%s,'running',%s) RETURNING id",
                (trigger, JUDGE_MODEL, len(docs)),
            ).fetchone()
            run_id = run["id"]
        else:
            conn.execute("UPDATE eval_run SET n_docs=%s WHERE id=%s", (len(docs), run_id))
    _run_active = {"id": run_id, "trigger": trigger, "total": len(docs), "done": 0}
    print(f"[eval] run {run_id} ({trigger}) started — {len(docs)} docs, judge={JUDGE_MODEL}")
    if not docs:
        _finalize_run(run_id)
        _run_active = None
        return

    # bounded parallelism across docs
    sem = threading.BoundedSemaphore(_concurrency)
    threads = []

    def _work(d):
        with sem:
            if _paused:
                return
            with _lock:
                _scoring.add(d["id"])
            try:
                _score_doc(run_id, d["id"], d["name"])
            except Exception as e:
                print(f"[eval] doc {d['id']} failed: {e!r}")
            finally:
                with _lock:
                    _scoring.discard(d["id"])
                if _run_active:
                    _run_active["done"] += 1

    for d in docs:
        t = threading.Thread(target=_work, args=(d,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    _finalize_run(run_id)
    _run_active = None


def _finalize_run(run_id: int) -> None:
    with get_conn() as conn:
        agg = conn.execute(
            "SELECT count(DISTINCT doc_id) AS docs, count(*) AS total, "
            "count(*) FILTER (WHERE verdict='pass') AS p, "
            "count(*) FILTER (WHERE verdict='fail') AS f, "
            "count(*) FILTER (WHERE verdict='partial') AS pa "
            "FROM eval_result WHERE run_id=%s", (run_id,),
        ).fetchone()
        total = agg["total"] or 0
        score = round(100.0 * ((agg["p"] or 0) + 0.5 * (agg["pa"] or 0)) / total, 1) if total else 0.0
        conn.execute(
            "UPDATE eval_run SET status='done', n_docs=%s, n_total=%s, n_pass=%s, "
            "n_fail=%s, n_partial=%s, score_pct=%s, finished_at=now() WHERE id=%s",
            (agg["docs"] or 0, total, agg["p"] or 0, agg["f"] or 0, agg["pa"] or 0, score, run_id),
        )
    print(f"[eval] run {run_id} done — {score}% over {total} questions")


# ── status / read accessors (for routes) ─────────────────────────────────────────
def _score_class(s) -> str:
    if s is None:
        return "none"
    return "hi" if s >= 80 else ("mid" if s >= 70 else "lo")


def status() -> dict:
    with _lock:
        active = list(_scoring)
    kpis, last_run, runs, scoring_docs, run_active = {}, None, [], [], None
    try:
        with get_conn() as conn:
            # DB-backed active-run view (correct regardless of which worker answers)
            ra = conn.execute(
                "SELECT id, trigger, n_docs FROM eval_run "
                "WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if ra:
                done = conn.execute(
                    "SELECT count(DISTINCT doc_id) AS n FROM eval_result WHERE run_id=%s",
                    (ra["id"],),
                ).fetchone()
                run_active = {"id": ra["id"], "trigger": ra["trigger"],
                              "total": ra["n_docs"], "done": (done["n"] if done else 0)}
            last_run = conn.execute(
                "SELECT id, trigger, judge_model, status, n_docs, n_total, n_pass, "
                "n_fail, n_partial, score_pct, started_at, finished_at "
                "FROM eval_run WHERE status='done' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_run = dict(last_run) if last_run else None
            for r in conn.execute(
                "SELECT id, trigger, judge_model, status, n_docs, n_total, score_pct, "
                "started_at, finished_at FROM eval_run ORDER BY id DESC LIMIT 8"
            ).fetchall():
                runs.append(dict(r))
            h = conn.execute(
                "SELECT count(*) AS docs, "
                "round(avg(score_pct)::numeric,1) AS corpus, "
                "round(avg(grounded_pct)::numeric,1) AS grounded, "
                "coalesce(sum(halluc_count),0) AS halluc, "
                "coalesce(sum(neg_total),0) AS neg_total, "
                "coalesce(sum(neg_caught),0) AS neg_caught, "
                "count(*) FILTER (WHERE needs_review) AS needs_review "
                "FROM doc_health"
            ).fetchone()
            kpis = dict(h) if h else {}
            q = conn.execute("SELECT count(*) AS n FROM eval_question WHERE active").fetchone()
            kpis["golden_questions"] = q["n"] if q else 0
            if active:
                for r in conn.execute(
                    "SELECT id, name FROM docs WHERE id = ANY(%s)", (active,)
                ).fetchall():
                    scoring_docs.append(dict(r))
    except Exception as e:
        print(f"[eval] status query failed: {e!r}")
    prev = None
    if len(runs) >= 2:
        prev = runs[1].get("score_pct")
    return {
        "enabled": EVAL_ENABLED,
        "running": _started and not _paused,
        "paused": _paused,
        "concurrency": _concurrency,
        "nightly_hour": EVAL_HOUR,
        "answer_model": ANSWER_MODEL,
        "judge_model": JUDGE_MODEL,
        "active": len(active),
        "scoring": scoring_docs,
        "run_active": run_active,
        "last_run": last_run,
        "prev_score": prev,
        "runs": runs,
        "kpis": kpis,
    }


def doc_list() -> list[dict]:
    """Per-doc health rows for the grid."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT h.doc_id, d.name, h.score_pct, h.prev_score, h.n_total, h.n_pass, "
                "h.n_fail, h.n_partial, h.grounded_pct, h.halluc_count, h.neg_total, "
                "h.neg_caught, h.needs_review, h.evaluated_at "
                "FROM doc_health h JOIN docs d ON d.id=h.doc_id "
                "ORDER BY h.needs_review DESC, h.score_pct ASC NULLS LAST"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["score_class"] = _score_class(d.get("score_pct"))
            out.append(d)
        return out
    except Exception as e:
        print(f"[eval] doc_list failed: {e!r}")
        return []


def doc_detail(doc_id: int) -> dict:
    """One doc: its health + every question result from its latest run."""
    try:
        with get_conn() as conn:
            head = conn.execute(
                "SELECT h.*, d.name FROM doc_health h JOIN docs d ON d.id=h.doc_id "
                "WHERE h.doc_id=%s", (doc_id,),
            ).fetchone()
            results = conn.execute(
                "SELECT question, kind, expected, got, verdict, judge_score, judge_reason, "
                "grounded, refused, retrieved_n FROM eval_result "
                "WHERE doc_id=%s AND run_id=(SELECT last_run_id FROM doc_health WHERE doc_id=%s) "
                "ORDER BY CASE verdict WHEN 'fail' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END, id",
                (doc_id, doc_id),
            ).fetchall()
        return {
            "head": dict(head) if head else None,
            "results": [dict(r) for r in results],
        }
    except Exception as e:
        print(f"[eval] doc_detail failed: {e!r}")
        return {"head": None, "results": []}


# ── loop ──────────────────────────────────────────────────────────────────────────
def _loop() -> None:
    global _last_tick_hour
    while True:
        try:
            if _paused:
                time.sleep(EVAL_POLL_SECONDS)
                continue
            # 1) DB-backed manual trigger — claim a queued run (works across workers)
            claimed = _claim_queued()
            if claimed:
                _run_pass(claimed["trigger"], run_id=claimed["id"])
                time.sleep(EVAL_POLL_SECONDS)
                continue
            # 2) nightly: fire once when the clock first enters EVAL_HOUR
            h = datetime.now().hour
            if h == EVAL_HOUR and _last_tick_hour != EVAL_HOUR:
                _run_pass("nightly")
            _last_tick_hour = h
            time.sleep(EVAL_POLL_SECONDS)
        except Exception as e:
            print(f"[eval] loop error: {e!r}")
            time.sleep(EVAL_POLL_SECONDS)


def start() -> None:
    """Start the eval agent loop. No-op unless EVAL_ENABLED is on."""
    global _started
    if _started or not EVAL_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()
    print(f"[eval] Eval Agent started · nightly {EVAL_HOUR:02d}:00 · "
          f"concurrency={_concurrency} · judge={JUDGE_MODEL}")
