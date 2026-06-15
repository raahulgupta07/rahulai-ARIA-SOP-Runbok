"""Citation-accuracy verify pass (admin-tracking hole #2 — the trust moat).

For each cited page of a grounded answer, an LLM judge reads the page's compiled
markdown (doc_pages_md) and rules whether it actually backs the answer's claims.
We store one verdict per (answer, page) and roll a per-answer verify_score = % of
cited pages that support the answer.

Cheap + bounded: small model, capped text, capped batch. Default OFF — runs only
when VERIFY_ENABLED=1 (daemon) or when an admin triggers a one-off batch.
A blind/uncited answer is skipped (nothing to verify)."""
import json
import os
import re
import threading
import time

from .db import get_conn

VERIFY_ENABLED = os.getenv("VERIFY_ENABLED", "0") == "1"
_FLAG = int(os.getenv("VERIFY_FLAG_THRESHOLD", "70"))   # < this % → flag for review
_INTERVAL = int(os.getenv("VERIFY_INTERVAL_SEC", "120"))
_BATCH = int(os.getenv("VERIFY_BATCH", "10"))
_ANS_CHARS = 1500
_MD_CHARS = 3500

_JUDGE = (
    "You are a citation auditor. An ANSWER usually draws on SEVERAL pages, so each "
    "page only needs to back PART of it. Given the ANSWER and ONE cited SOURCE PAGE, "
    "decide whether this page is a legitimate source for AT LEAST ONE claim in the "
    "answer (partial support counts as supports=true). Answer supports=false ONLY if "
    "the page is unrelated to the answer's topic or contradicts it. Ignore missing "
    "page numbers or steps that other cited pages would cover. "
    'Reply ONLY compact JSON: {"supports": true|false, "confidence": 0.0-1.0, '
    '"reason": "<=15 words"}.'
)


def _page_md(doc_id, page_no) -> str:
    if doc_id is None or page_no is None:
        return ""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT md FROM doc_pages_md WHERE doc_id = %s AND page_no = %s",
            (doc_id, page_no),
        ).fetchone()
    return (row or {}).get("md") or ""


def _judge(answer: str, md: str) -> dict:
    """One LLM verdict. Fail-soft → supports=None on any error."""
    from .agent import _client, CHAT_MODEL
    if not _client or not md.strip():
        return {"supports": None, "confidence": None, "reason": "no source text"}
    try:
        resp = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _JUDGE},
                {"role": "user", "content":
                    f"ANSWER:\n{answer[:_ANS_CHARS]}\n\nSOURCE PAGE:\n{md[:_MD_CHARS]}"},
            ],
            max_tokens=120, temperature=0,
        )
        txt = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", txt, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {
            "supports": bool(data.get("supports")) if "supports" in data else None,
            "confidence": data.get("confidence"),
            "reason": (data.get("reason") or "")[:160],
        }
    except Exception as e:
        return {"supports": None, "confidence": None, "reason": f"judge error: {e}"[:160]}


def _unverified(limit: int) -> list[dict]:
    """Recent grounded bot answers with cited pages not yet verified."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT m.id AS message_id, m.text, m.pages "
            "FROM messages m "
            "LEFT JOIN answer_metrics am ON am.message_id = m.id "
            "WHERE m.role = 'bot' AND jsonb_typeof(m.pages) = 'array' "
            "  AND m.pages <> '[]'::jsonb "
            "  AND (am.id IS NULL OR am.verify_score IS NULL) "
            "  AND NOT EXISTS (SELECT 1 FROM answer_verify v WHERE v.message_id = m.id) "
            "ORDER BY m.id DESC LIMIT %s",
            (limit,),
        ).fetchall()]


def verify_one(message_id: int, answer: str, pages: list) -> dict:
    """Judge every cited page of one answer, persist verdicts + roll the score."""
    verdicts = []
    supports = 0
    counted = 0
    with get_conn() as conn:
        for p in pages or []:
            pid = p.get("page_id")
            doc_id = p.get("doc_id")
            page_no = p.get("page_no")
            md = _page_md(doc_id, page_no)
            v = _judge(answer, md)
            conn.execute(
                "INSERT INTO answer_verify (message_id, page_id, doc_id, page_no, "
                " supports, confidence, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (message_id, pid, doc_id, page_no, v["supports"], v["confidence"], v["reason"]),
            )
            if v["supports"] is not None:
                counted += 1
                if v["supports"]:
                    supports += 1
            verdicts.append({"page_id": pid, **v})
        score = round(100 * supports / counted) if counted else None
        # attach the score to the answer's metrics row (it always exists for new
        # answers; for older answers without one, create a minimal row).
        if score is not None:
            updated = conn.execute(
                "UPDATE answer_metrics SET verify_score = %s, verify_at = now() "
                "WHERE message_id = %s RETURNING id", (score, message_id),
            ).fetchone()
            if not updated:
                conn.execute(
                    "INSERT INTO answer_metrics (message_id, cited_n, verify_score, verify_at) "
                    "VALUES (%s,%s,%s, now())", (message_id, len(pages or []), score),
                )
    # close the loop: a low-scoring answer is pushed to the Review queue so a
    # human can fix the doc / teach a correction instead of it just being a number
    # only NOTIFY on genuinely low scores (<50%) to keep the feed quiet; dedupe
    # repeats of the same flagged answer within 12h. (Review-queue logic unchanged.)
    if score is not None and score < 50:
        try:
            from . import notify
            notify.emit("audit", f"Low-accuracy answer flagged ({score}%)",
                        (answer or "")[:90], "warn", dedupe_hours=12)
        except Exception:
            pass
    return {"message_id": message_id, "score": score, "verdicts": verdicts}


def verify_message(message_id: int) -> dict:
    """Verify ONE bot answer on demand (chat 'check accuracy' button). Loads the
    answer text + cited pages and judges each cited page. Idempotent-ish: clears
    any prior verdicts for this message so a re-check re-scores fresh."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT text, pages FROM messages WHERE id = %s AND role = 'bot'",
            (message_id,),
        ).fetchone()
        if not row:
            return {"message_id": message_id, "score": None, "verdicts": [], "error": "not found"}
        conn.execute("DELETE FROM answer_verify WHERE message_id = %s", (message_id,))
    pages = row["pages"] if isinstance(row["pages"], list) else json.loads(row["pages"] or "[]")
    if not pages:
        return {"message_id": message_id, "score": None, "verdicts": [], "error": "no citations"}
    return verify_one(message_id, row["text"] or "", pages)


def low_accuracy(threshold: int | None = None, days: int = 30, limit: int = 50) -> dict:
    """Verified answers scoring below `threshold` — the Review queue's fix-list.
    Pairs each with the user's question + the cited pages the judge marked as NOT
    supporting, so an admin can fix the doc or teach a correction."""
    threshold = threshold if threshold is not None else _FLAG
    out = []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT am.message_id, am.verify_score AS score, am.verify_at, "
            "  m.text AS answer, m.conversation_id, "
            "  (SELECT um.text FROM messages um WHERE um.conversation_id = m.conversation_id "
            "     AND um.role = 'user' AND um.id < m.id ORDER BY um.id DESC LIMIT 1) AS question "
            "FROM answer_metrics am JOIN messages m ON m.id = am.message_id "
            "WHERE am.verify_score IS NOT NULL AND am.verify_score < %s "
            "  AND am.verify_at > now() - (%s||' days')::interval "
            "ORDER BY am.verify_score ASC, am.verify_at DESC LIMIT %s",
            (threshold, days, limit),
        ).fetchall()
        for r in rows:
            fails = conn.execute(
                "SELECT v.page_no, v.reason, d.name AS doc_name "
                "FROM answer_verify v LEFT JOIN docs d ON d.id = v.doc_id "
                "WHERE v.message_id = %s AND v.supports = false "
                "ORDER BY v.page_no", (r["message_id"],),
            ).fetchall()
            out.append({
                "message_id": r["message_id"],
                "score": r["score"],
                "verify_at": r["verify_at"],
                "conversation_id": r["conversation_id"],
                "question": (r["question"] or "")[:240],
                "answer": (r["answer"] or "")[:300],
                "failing": [{"doc_name": f["doc_name"], "page_no": f["page_no"],
                             "reason": f["reason"]} for f in fails],
            })
    return {"threshold": threshold, "days": days, "count": len(out), "answers": out}


def run_batch(limit: int | None = None) -> dict:
    """Verify up to `limit` unverified answers. Returns a small summary."""
    limit = limit or _BATCH
    items = _unverified(limit)
    done = []
    for it in items:
        pages = it["pages"] if isinstance(it["pages"], list) else json.loads(it["pages"] or "[]")
        try:
            done.append(verify_one(it["message_id"], it["text"] or "", pages))
        except Exception as e:
            print(f"[verify] answer {it['message_id']} failed: {e!r}")
    scored = [d["score"] for d in done if d["score"] is not None]
    return {
        "verified": len(done),
        "avg_score": round(sum(scored) / len(scored)) if scored else None,
        "remaining": max(0, len(_unverified(1))),  # 1 if more left, else 0
    }


# ----------------------------------------------------------------- daemon ---
def _loop():
    while True:
        try:
            res = run_batch(_BATCH)
            if res["verified"]:
                print(f"[verify] batch: {res}")
        except Exception as e:
            print(f"[verify] loop error: {e!r}")
        time.sleep(_INTERVAL)


def start_daemon():
    if not VERIFY_ENABLED:
        return
    threading.Thread(target=_loop, daemon=True, name="verify").start()
    print("[verify] daemon started (VERIFY_ENABLED=1)")
