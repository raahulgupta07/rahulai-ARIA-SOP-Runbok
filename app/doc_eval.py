"""Per-document answer-accuracy self-eval (UAT) — importable + background-runnable.

Runs each doc's mined golden Q&A (qa_pairs) through the REAL answer pipeline and
scores grounded / right-doc / page-hit / faithful per doc. Powers the dashboard
Accuracy page (POST /analytics/doc-eval/run + GET /analytics/doc-eval). Results
persist to `doc_eval_runs` (latest row = last run). A run is ~a few seconds per
question (compose + a verify judge), so it runs in a background thread; the route
kicks it off and the UI polls GET for {running, result}.

CLI: `PYTHONPATH=/app python scripts/doc_eval.py` (thin wrapper around run_eval).
"""
from __future__ import annotations

import json
import threading

from .db import get_conn
from .retrieve import search_pages, DEFAULT_K
from .agent import stream_reply, parse_pages, render_cited
from .answer_quality import cited_page_ids
from . import verify

# in-process run state (single-flight); persisted result lives in doc_eval_runs
_STATE: dict = {"running": False, "started_at": None, "error": None}
_LOCK = threading.Lock()


def _golden(max_q: int):
    out: dict[int, dict] = {}
    with get_conn() as c:
        docs = c.execute(
            "SELECT id, name FROM docs WHERE status='ready' ORDER BY id"
        ).fetchall()
        for d in docs:
            qs = c.execute(
                "SELECT question, answer, page_ids FROM qa_pairs "
                "WHERE doc_id=%s AND status='active' ORDER BY id LIMIT %s",
                (d["id"], max_q),
            ).fetchall()
            out[d["id"]] = {
                "name": d["name"],
                "qs": [{"q": r["question"], "pages": list(r["page_ids"] or [])}
                       for r in qs],
            }
    return out


def _answer(q):
    seed = search_pages(q, k=DEFAULT_K)
    acc = "".join(stream_reply(q, seed, mode="quick", session_id="eval",
                               history=[], meter={}))
    clean, _ = parse_pages(acc, [])
    page_ids = cited_page_ids(acc, seed)
    clean = render_cited(clean, seed)
    by_id = {p["page_id"]: p for p in seed}
    cited = [by_id[pid] for pid in page_ids if pid in by_id]
    return clean, page_ids, {p["doc_id"] for p in cited}, cited


def _faithful(answer, cited):
    md = "\n\n".join(verify._page_md(p["doc_id"], p["page_no"]) for p in cited)
    if not md.strip():
        return False
    return verify._judge(answer, md).get("supports") is True


def run_eval(max_q: int = 6) -> dict:
    """Synchronous full eval → report dict (also persisted to doc_eval_runs)."""
    golden = _golden(max_q)
    docs_out = []
    tot = {"n": 0, "grounded": 0, "right_doc": 0, "page_hit": 0, "faithful": 0}
    gaps = []
    for doc_id, info in golden.items():
        if not info["qs"]:
            gaps.append({"doc_id": doc_id, "name": info["name"]})
            continue
        d = {"doc_id": doc_id, "name": info["name"], "n": 0,
             "grounded": 0, "right_doc": 0, "page_hit": 0, "faithful": 0, "rows": []}
        for item in info["qs"]:
            q = item["q"]
            try:
                ans, pids, cdocs, cited = _answer(q)
            except Exception as e:
                ans, pids, cdocs, cited = f"(error: {e})", [], set(), []
            grounded = bool(pids)
            right = doc_id in cdocs
            phit = bool(set(pids) & set(item["pages"]))
            faith = False
            if grounded:
                try:
                    faith = _faithful(ans, cited)
                except Exception:
                    faith = False
            for k, ok in (("grounded", grounded), ("right_doc", right),
                          ("page_hit", phit), ("faithful", faith)):
                d[k] += int(ok)
            d["n"] += 1
            d["rows"].append({"q": q, "grounded": grounded, "right_doc": right,
                              "page_hit": phit, "faithful": faith})
        for k in ("n", "grounded", "right_doc", "page_hit", "faithful"):
            tot[k] += d[k]
        n = d["n"] or 1
        d["pct"] = {k: round(100 * d[k] / n) for k in
                    ("grounded", "right_doc", "page_hit", "faithful")}
        docs_out.append(d)

    n = tot["n"] or 1
    report = {
        "overall": {k: round(100 * tot[k] / n) for k in
                    ("grounded", "right_doc", "page_hit", "faithful")},
        "questions": tot["n"],
        "docs": docs_out,
        "coverage_gaps": gaps,
    }
    _persist(report)
    return report


def _persist(report: dict):
    try:
        with get_conn() as c:
            c.execute(
                "INSERT INTO doc_eval_runs (overall, docs, gaps, questions) "
                "VALUES (%s,%s,%s,%s)",
                (json.dumps(report["overall"]), json.dumps(report["docs"]),
                 json.dumps(report["coverage_gaps"]), report["questions"]),
            )
    except Exception as e:
        print(f"[doc_eval] persist skipped: {e!r}")


def latest() -> dict:
    """Last persisted run + the in-process running flag."""
    row = None
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT overall, docs, gaps, questions, created_at "
                "FROM doc_eval_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        row = None
    result = None
    if row:
        result = {
            "overall": row["overall"], "docs": row["docs"],
            "coverage_gaps": row["gaps"], "questions": row["questions"],
            "created_at": row["created_at"],
        }
    return {"running": _STATE["running"], "started_at": _STATE["started_at"],
            "error": _STATE["error"], "result": result}


def _run_bg(max_q: int):
    try:
        run_eval(max_q)
    except Exception as e:
        _STATE["error"] = str(e)
        print(f"[doc_eval] run failed: {e!r}")
    finally:
        _STATE["running"] = False


def start_eval(max_q: int = 6) -> bool:
    """Kick off a background run. Returns False if one is already running."""
    with _LOCK:
        if _STATE["running"]:
            return False
        from datetime import datetime, timezone
        _STATE.update(running=True, error=None,
                      started_at=datetime.now(timezone.utc).isoformat())
    threading.Thread(target=_run_bg, args=(max_q,), daemon=True).start()
    return True
