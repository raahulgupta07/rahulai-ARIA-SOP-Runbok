"""Self-Heal agent — eval-gated, AI-only, per-doc self-improvement loop.

A PARALLEL background worker (NO coupling to ingest). For each ready document it
runs a tight loop: mine golden Q&A, evaluate them through the REAL answer
pipeline, re-answer the misses constrained to THAT doc's own pages (so the new
answer is grounded in the source by construction), then put each healed answer
through an ADVERSARIAL judge panel (independent refute-framed faithfulness
judges). Only majority-confirmed answers get BANKED as active Q&A (source
'selfheal') so the serve path returns them verbatim next round. Couldn't-verify
questions go to a review list. The loop runs until it stops gaining (plateau).

Everything reuses existing machinery:
  - doc_qa.extract_doc_qa     — seed/top-up golden Q&A
  - doc_eval._answer          — the REAL quick pipeline (grade grounded/right-doc/
                                page-hit/faithful)
  - verify._page_md / _judge  — the LLM faithfulness judge (pure, no DB write)
  - retrieve.search_pages +
    agent.stream_reply         — HEAL: re-answer constrained to the doc's pages
  - qa.add_qa                  — BANK a confirmed pair (active, serves verbatim)
  - leader.try_acquire        — single-leader gate (mirrors dream.py / worker.py)

Fail-soft everywhere: any per-question error logs tag='warn' and continues; the
daemon never crashes. Discovery is independent: claim_next() picks a ready doc
that has no completed heal run yet, with FOR UPDATE SKIP LOCKED (mirrors
worker._claim) so parallel callers never grab the same doc.
"""
import json
import os
import threading
from datetime import datetime, timezone

from .db import get_conn

# ---- flags (env, mirrors config idiom; all fail-soft) -------------------------
SELFHEAL_ENABLED = os.getenv("SELFHEAL_ENABLED", "0") == "1"
MAX_ROUNDS = int(os.getenv("SELFHEAL_MAX_ROUNDS", "3"))
JUDGES = int(os.getenv("SELFHEAL_JUDGES", "3"))
POLL_MIN = int(os.getenv("SELFHEAL_POLL_MIN", "5"))

# ---- single-flight state ------------------------------------------------------
_LOCK = threading.Lock()
_STATE: dict = {"running": False, "current_doc": None}


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# log helper                                                                   #
# --------------------------------------------------------------------------- #
def _log(doc_id, run_id, tag: str, msg: str) -> None:
    """Append one streamed activity line. Fail-soft (own autocommit conn)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO selfheal_log (doc_id, run_id, tag, msg) "
                "VALUES (%s, %s, %s, %s)",
                (doc_id, run_id, tag, (msg or "")[:500]),
            )
    except Exception as e:  # logging must never crash the worker
        print(f"[selfheal] log skipped: {e!r}")


# --------------------------------------------------------------------------- #
# adversarial refute judge                                                     #
# --------------------------------------------------------------------------- #
_REFUTE_PREFIX = (
    "ADVERSARIAL CHECK. Your job is to DISPROVE that the source page genuinely "
    "backs the answer. Look hard for any claim, value, code, step or screen in "
    "the answer that the page does NOT actually support. Only return "
    "supports=true if, after trying to refute it, you still cannot — i.e. the "
    "page really does back at least one concrete claim in the answer. When in "
    "doubt, return supports=false.\n\n"
)


def _refute_judge(answer: str, md: str) -> dict:
    """One refute-framed faithfulness verdict. Wraps verify._judge by prepending
    an adversarial instruction to the answer text it sees. Pure (no DB write),
    fail-soft → supports=None on any error."""
    from . import verify
    try:
        return verify._judge(_REFUTE_PREFIX + (answer or ""), md or "")
    except Exception as e:
        return {"supports": None, "confidence": None, "reason": f"judge error: {e}"[:160]}


def _panel_confirms(answer: str, md: str) -> tuple[bool, str]:
    """Run SELFHEAL_JUDGES independent refute-framed judges over the source md.
    CONFIRM only if a strict majority say supports. Returns (confirmed, reason)."""
    if not (md or "").strip():
        return False, "no source text to verify against"
    n = max(1, JUDGES)
    yes = 0
    counted = 0
    last_reason = ""
    for _ in range(n):
        v = _refute_judge(answer, md)
        s = v.get("supports")
        if s is None:
            continue
        counted += 1
        if s:
            yes += 1
        else:
            last_reason = v.get("reason") or last_reason
    if counted == 0:
        return False, "judges returned no verdict"
    confirmed = yes > (counted / 2)
    if confirmed:
        return True, f"{yes}/{counted} judges support"
    return False, (last_reason or f"only {yes}/{counted} judges support")


# --------------------------------------------------------------------------- #
# golden set + mine                                                            #
# --------------------------------------------------------------------------- #
def _doc_name(doc_id: int) -> str:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT name FROM docs WHERE id = %s", (doc_id,)).fetchone()
        return (row or {}).get("name") or f"doc {doc_id}"
    except Exception:
        return f"doc {doc_id}"


def _active_qa(doc_id: int) -> list[dict]:
    """Active golden Q&A for this doc: question + grounding page_ids."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT question, page_ids FROM qa_pairs "
            "WHERE doc_id = %s AND status = 'active' ORDER BY id",
            (doc_id,),
        ).fetchall()
    return [{"q": r["question"], "pages": list(r["page_ids"] or [])} for r in rows]


# --------------------------------------------------------------------------- #
# heal: re-answer constrained to the doc's own pages                          #
# --------------------------------------------------------------------------- #
def _heal_answer(doc_id: int, question: str) -> tuple[str, list[dict]]:
    """Re-answer `question` using ONLY pages from this doc. Retrieve, drop any
    page whose doc_id != this doc, compose via stream_reply over those pages.
    Returns (clean_answer, doc_pages). Empty pages → ('', [])."""
    from .retrieve import search_pages, DEFAULT_K
    from .agent import stream_reply, parse_pages, render_cited

    pool = search_pages(question, k=DEFAULT_K)
    doc_pages = [p for p in pool if p.get("doc_id") == doc_id]
    if not doc_pages:
        return "", []
    acc = "".join(stream_reply(question, doc_pages, mode="quick",
                               session_id="selfheal", history=[], meter={}))
    clean, _ = parse_pages(acc, [])
    clean = render_cited(clean, doc_pages)
    return clean, doc_pages


# --------------------------------------------------------------------------- #
# the loop                                                                     #
# --------------------------------------------------------------------------- #
def run_doc(doc_id: int) -> dict:
    """SYNCHRONOUS full heal loop for one doc. Inserts a selfheal_runs row
    (running → done/failed), streams selfheal_log lines, returns the summary."""
    from . import doc_eval, verify
    from . import qa as qa_mod

    name = _doc_name(doc_id)

    # open the run row
    run_id = None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "INSERT INTO selfheal_runs (doc_id, status, started_at) "
                "VALUES (%s, 'running', %s) RETURNING id",
                (doc_id, _now()),
            ).fetchone()
            run_id = row["id"]
    except Exception as e:
        print(f"[selfheal] could not open run for doc {doc_id}: {e!r}")
        return {"doc_id": doc_id, "error": str(e), "rounds": 0,
                "before_acc": 0, "after_acc": 0, "banked": 0, "review": []}

    _STATE["current_doc"] = {"id": doc_id, "name": name}
    review: list[dict] = []
    banked = 0
    round_accs: list[int] = []
    rounds_run = 0

    try:
        # ---- 1. MINE: top up golden set if thin --------------------------------
        try:
            golden = _active_qa(doc_id)
            if len(golden) < 3:
                _log(doc_id, run_id, "mine",
                     f"only {len(golden)} golden Q&A — mining more from the wiki")
                try:
                    from .doc_qa import extract_doc_qa
                    added = extract_doc_qa(doc_id, display_name=name)
                    _log(doc_id, run_id, "mine", f"mined {added} candidate Q&A")
                except Exception as e:
                    _log(doc_id, run_id, "warn", f"mine failed: {e}")
                golden = _active_qa(doc_id)
            _log(doc_id, run_id, "mine", f"golden set = {len(golden)} questions")
        except Exception as e:
            _log(doc_id, run_id, "warn", f"golden load failed: {e}")
            golden = []

        # ---- 2. rounds ---------------------------------------------------------
        for rnd in range(1, max(1, MAX_ROUNDS) + 1):
            rounds_run = rnd
            if not golden:
                _log(doc_id, run_id, "eval", "no golden questions — nothing to heal")
                round_accs.append(0)
                break

            # a. EVAL
            grounded_n = 0
            misses: list[str] = []
            for item in golden:
                q = item["q"]
                try:
                    ans, pids, cdocs, cited = doc_eval._answer(q)
                except Exception as e:
                    _log(doc_id, run_id, "warn", f"eval error on '{q[:60]}': {e}")
                    misses.append(q)
                    continue
                grounded = bool(pids)
                right_doc = doc_id in cdocs
                page_hit = bool(set(pids) & set(item["pages"]))
                faithful = False
                if grounded:
                    try:
                        md = "\n\n".join(
                            verify._page_md(p["doc_id"], p["page_no"]) for p in cited)
                        faithful = verify._judge(ans, md).get("supports") is True
                    except Exception:
                        faithful = False
                ok = grounded and right_doc and faithful
                if grounded:
                    grounded_n += 1
                _log(doc_id, run_id, "eval",
                     f"{'PASS' if ok else 'MISS'} · grounded={grounded} "
                     f"right_doc={right_doc} page_hit={page_hit} faithful={faithful} "
                     f"· {q[:70]}")
                if not ok:
                    misses.append(q)

            acc_pct = round(100 * grounded_n / len(golden)) if golden else 0
            round_accs.append(acc_pct)
            _log(doc_id, run_id, "eval",
                 f"round {rnd}: grounded {acc_pct}% ({grounded_n}/{len(golden)}), "
                 f"{len(misses)} miss(es)")

            banked_this_round = 0

            # b/c/d. HEAL → JUDGE → BANK each miss
            for q in misses:
                try:
                    healed, doc_pages = _heal_answer(doc_id, q)
                except Exception as e:
                    _log(doc_id, run_id, "warn", f"heal error on '{q[:60]}': {e}")
                    continue
                if not healed or not doc_pages:
                    review.append({"doc": name, "question": q,
                                   "reason": "no source pages in this doc to ground an answer"})
                    _log(doc_id, run_id, "judge",
                         f"REFUTE · no doc-grounded answer · {q[:70]}")
                    continue
                _log(doc_id, run_id, "heal",
                     f"re-answered over {len(doc_pages)} doc page(s) · {q[:70]}")

                # adversarial panel over the cited doc pages' md
                try:
                    md = "\n\n".join(
                        verify._page_md(p.get("doc_id"), p.get("page_no")) for p in doc_pages)
                except Exception as e:
                    md = ""
                    _log(doc_id, run_id, "warn", f"md load failed: {e}")
                confirmed, reason = _panel_confirms(healed, md)

                if confirmed:
                    try:
                        page_ids = [p.get("page_id") for p in doc_pages if p.get("page_id")]
                        qa_mod.add_qa(
                            question=q, answer=healed, source="selfheal",
                            status="active", confidence=None, doc_id=doc_id,
                            page_ids=page_ids, created_by=f"selfheal:{doc_id}",
                            origin="self-heal loop",
                        )
                        banked += 1
                        banked_this_round += 1
                        _log(doc_id, run_id, "bank",
                             f"BANKED ({reason}) · {q[:70]}")
                    except Exception as e:
                        _log(doc_id, run_id, "warn", f"bank failed on '{q[:60]}': {e}")
                else:
                    review.append({"doc": name, "question": q, "reason": reason})
                    _log(doc_id, run_id, "judge", f"REFUTE ({reason}) · {q[:70]}")

            # e. plateau / loop-until-dry
            gained = (len(round_accs) >= 2 and round_accs[-1] > round_accs[-2])
            if banked_this_round == 0 and not gained:
                _log(doc_id, run_id, "info",
                     f"round {rnd}: no new banks, no accuracy gain — stopping")
                break

        # ---- 3. persist final run ---------------------------------------------
        before_acc = round_accs[0] if round_accs else 0
        after_acc = round_accs[-1] if round_accs else 0
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE selfheal_runs SET status='done', rounds=%s, "
                    "before_acc=%s, after_acc=%s, banked=%s, review=%s::jsonb, "
                    "finished_at=%s WHERE id=%s",
                    (rounds_run, before_acc, after_acc, banked,
                     json.dumps(review), _now(), run_id),
                )
        except Exception as e:
            print(f"[selfheal] final persist skipped doc {doc_id}: {e!r}")
        _log(doc_id, run_id, "info",
             f"done · rounds {rounds_run} · acc {before_acc}%→{after_acc}% · "
             f"banked {banked} · review {len(review)}")
        return {"doc_id": doc_id, "rounds": rounds_run, "before_acc": before_acc,
                "after_acc": after_acc, "banked": banked, "review": review}

    except Exception as e:
        # hard failure — mark the run failed, never propagate
        print(f"[selfheal] run_doc {doc_id} FAILED: {e!r}")
        _log(doc_id, run_id, "warn", f"run failed: {e}")
        try:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE selfheal_runs SET status='failed', rounds=%s, "
                    "banked=%s, review=%s::jsonb, finished_at=%s WHERE id=%s",
                    (rounds_run, banked, json.dumps(review), _now(), run_id),
                )
        except Exception:
            pass
        return {"doc_id": doc_id, "error": str(e), "rounds": rounds_run,
                "before_acc": round_accs[0] if round_accs else 0,
                "after_acc": round_accs[-1] if round_accs else 0,
                "banked": banked, "review": review}
    finally:
        _STATE["current_doc"] = None


# --------------------------------------------------------------------------- #
# discovery — parallel claim (no coupling to ingest)                          #
# --------------------------------------------------------------------------- #
def claim_next() -> int | None:
    """Atomically pick the next eligible doc: ready AND no completed heal run.
    Locked with FOR UPDATE SKIP LOCKED (mirrors worker._claim). Returns the
    doc_id or None. NOTE: the lock is held only for the duration of this SELECT
    (autocommit) — eligibility flips to ineligible once run_doc inserts a 'done'
    row, so two callers racing the same doc collapse to one effective heal."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM docs "
                "WHERE status = 'ready' "
                "  AND id NOT IN (SELECT doc_id FROM selfheal_runs WHERE status = 'done') "
                "ORDER BY id "
                "FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
        return row["id"] if row else None
    except Exception as e:
        print(f"[selfheal] claim_next error: {e!r}")
        return None


# --------------------------------------------------------------------------- #
# background runner — single-flight                                            #
# --------------------------------------------------------------------------- #
def _run_thread(doc_id: int | None):
    try:
        if doc_id is not None:
            run_doc(doc_id)
        else:
            while True:
                nxt = claim_next()
                if nxt is None:
                    break
                run_doc(nxt)
    except Exception as e:
        print(f"[selfheal] background run error: {e!r}")
    finally:
        with _LOCK:
            _STATE["running"] = False
            _STATE["current_doc"] = None


def start_selfheal(doc_id: int | None = None) -> bool:
    """Kick a background heal in a daemon thread. doc_id given = that doc; None =
    drain claim_next() until empty. Single-flight: False if already running."""
    with _LOCK:
        if _STATE["running"]:
            return False
        _STATE["running"] = True
        _STATE["current_doc"] = None
    threading.Thread(target=_run_thread, args=(doc_id,),
                     daemon=True, name="selfheal").start()
    return True


# --------------------------------------------------------------------------- #
# status / logs (for the UI)                                                   #
# --------------------------------------------------------------------------- #
def status() -> dict:
    """Snapshot for the UI: running flag, current doc, per-doc latest-run table,
    round accuracy trend (from the most recent run), banked/review totals."""
    out: dict = {
        "running": _STATE["running"],
        "current_doc": _STATE["current_doc"],
        "docs": [],
        "rounds": [],
        "banked_total": 0,
        "review_total": 0,
        "review": [],
    }
    try:
        with get_conn() as conn:
            # docs = ready docs LEFT JOIN their latest selfheal_run
            docs = conn.execute(
                """
                SELECT d.id AS doc_id, d.name,
                       r.status, r.after_acc, r.banked, r.review
                FROM docs d
                LEFT JOIN LATERAL (
                    SELECT status, after_acc, banked, review
                    FROM selfheal_runs s
                    WHERE s.doc_id = d.id
                    ORDER BY s.started_at DESC LIMIT 1
                ) r ON true
                WHERE d.status = 'ready'
                ORDER BY d.id
                """
            ).fetchall()
            banked_total = 0
            review_total = 0
            review_all: list[dict] = []
            for d in docs:
                rev = d["review"] if isinstance(d["review"], list) else []
                review_n = len(rev)
                out["docs"].append({
                    "doc_id": d["doc_id"],
                    "name": d["name"],
                    "status": d["status"] or "pending",
                    "accuracy": int(d["after_acc"] or 0),
                    "banked": int(d["banked"] or 0),
                    "review_n": review_n,
                })
                banked_total += int(d["banked"] or 0)
                review_total += review_n
                for item in rev:
                    if isinstance(item, dict):
                        review_all.append({
                            "doc": item.get("doc") or d["name"],
                            "question": item.get("question", ""),
                            "reason": item.get("reason", ""),
                        })
            out["banked_total"] = banked_total
            out["review_total"] = review_total
            out["review"] = review_all[:200]

            # round trend = the most recent run's per-round eval accuracies,
            # reconstructed from its log lines (best-effort, fail-soft).
            last = conn.execute(
                "SELECT id, before_acc, after_acc, rounds FROM selfheal_runs "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if last:
                logs = conn.execute(
                    "SELECT msg FROM selfheal_log "
                    "WHERE run_id = %s AND tag = 'eval' AND msg LIKE 'round %%grounded%%' "
                    "ORDER BY id",
                    (last["id"],),
                ).fetchall()
                rounds = []
                import re
                for lg in logs:
                    m = re.search(r"round (\d+): grounded (\d+)%", lg["msg"] or "")
                    if m:
                        rounds.append({"round": int(m.group(1)), "acc": int(m.group(2))})
                if not rounds:  # fall back to before/after endpoints
                    rounds = [{"round": 1, "acc": int(last["before_acc"] or 0)},
                              {"round": max(2, int(last["rounds"] or 1)),
                               "acc": int(last["after_acc"] or 0)}]
                out["rounds"] = rounds
    except Exception as e:
        print(f"[selfheal] status error: {e!r}")
    return out


def logs(doc_id: int | None = None, after_id: int = 0) -> list[dict]:
    """Streamed activity log lines with id > after_id (optional doc filter),
    ascending, capped ~200. Powers live polling."""
    try:
        params: list = [after_id]
        where = "id > %s"
        if doc_id is not None:
            where += " AND doc_id = %s"
            params.append(doc_id)
        with get_conn() as conn:
            rows = conn.execute(
                f"SELECT id, doc_id, tag, msg, created_at FROM selfheal_log "
                f"WHERE {where} ORDER BY id ASC LIMIT 200",
                tuple(params),
            ).fetchall()
        return [{
            "id": r["id"], "doc_id": r["doc_id"], "tag": r["tag"],
            "msg": r["msg"],
            "ts": r["created_at"].isoformat() if r["created_at"] else "",
        } for r in rows]
    except Exception as e:
        print(f"[selfheal] logs error: {e!r}")
        return []


# --------------------------------------------------------------------------- #
# daemon — leader-gated poll loop                                             #
# --------------------------------------------------------------------------- #
_started = False


def _daemon_loop():
    import time
    time.sleep(120)  # let boot settle
    while True:
        try:
            if SELFHEAL_ENABLED:
                # drain all eligible docs this tick (single-flight via _STATE)
                while True:
                    with _LOCK:
                        if _STATE["running"]:
                            break
                        nxt = claim_next()
                        if nxt is None:
                            break
                        _STATE["running"] = True
                        _STATE["current_doc"] = None
                    try:
                        run_doc(nxt)
                    finally:
                        with _LOCK:
                            _STATE["running"] = False
                            _STATE["current_doc"] = None
        except Exception as e:
            print(f"[selfheal] daemon loop skipped: {e!r}")
        time.sleep(max(1, POLL_MIN) * 60)


def start_daemon() -> None:
    """Launch the leader-gated poll loop once. Safe to call at startup. Daemons
    are already leader-gated by background.start_all (it only runs in the leader
    process), so this just starts the thread; the loop itself no-ops unless
    SELFHEAL_ENABLED is set."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_daemon_loop, name="selfheal-daemon", daemon=True).start()
    print(f"[selfheal] daemon on — poll every {POLL_MIN}m "
          f"(enabled={SELFHEAL_ENABLED})")
