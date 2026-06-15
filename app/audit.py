"""Coverage Audit — Aria grades her own knowledge gaps (AIS-OS /audit, ported).

Pure reads over data we already log (messages, memory, message_feedback, docs).
No ML, no new ingest. Surfaces the highest-leverage gaps so you fix the blind
spot that breaks the most answers first.

Signals:
  - blind spots  : questions answered with NO source page (empty messages.pages),
                   clustered by normalized text and ranked by frequency.
  - downvotes    : 👎 answers (message_feedback) joined to the question.
  - stale facts  : active facts never cited (cited_count = 0) past a grace window.
  - score        : four-pillar coverage score (Context/Coverage/Freshness/Signal),
                   each /25, plus leverage-weighted gap ranking (AIS-OS trick).
"""
import datetime

from .db import get_conn

STALE_DAYS = 14


def _scalar(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    return list(row.values())[0] if row else 0


def coverage_report(blind_limit: int = 12, days: int = 30) -> dict:
    """One self-audit snapshot. Never raises on empty data."""
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    with get_conn() as conn:
        # ---- corpus / context ----
        docs = _scalar(conn, "SELECT count(*) FROM docs WHERE status = 'ready'")
        pages = _scalar(conn, "SELECT count(*) FROM pages")
        facts_active = _scalar(conn, "SELECT count(*) FROM memory WHERE status = 'active'")
        facts_pending = _scalar(conn, "SELECT count(*) FROM memory WHERE status = 'pending'")

        # ---- answer coverage: bot replies with vs without a source page ----
        total_ans = _scalar(
            conn,
            "SELECT count(*) FROM messages WHERE role = 'bot' AND created_at >= %s",
            (since,),
        )
        no_src = _scalar(
            conn,
            "SELECT count(*) FROM messages WHERE role = 'bot' AND created_at >= %s "
            "AND (pages IS NULL OR jsonb_array_length(pages) = 0)",
            (since,),
        )

        # ---- blind-spot clusters: the question behind each source-less answer ----
        blind_rows = conn.execute(
            """
            SELECT lower(btrim(u.text)) AS q, count(*) AS n, max(b.created_at) AS last_at,
                   max(u.text) AS sample
            FROM messages b
            JOIN LATERAL (
                SELECT text FROM messages u
                WHERE u.conversation_id = b.conversation_id
                  AND u.role = 'user' AND u.id < b.id
                ORDER BY u.id DESC LIMIT 1
            ) u ON true
            WHERE b.role = 'bot' AND b.created_at >= %s
              AND (b.pages IS NULL OR jsonb_array_length(b.pages) = 0)
              AND u.text IS NOT NULL AND btrim(u.text) <> ''
            GROUP BY lower(btrim(u.text))
            ORDER BY n DESC, last_at DESC
            LIMIT %s
            """,
            (since, blind_limit),
        ).fetchall()
        blind_spots = [
            {"question": r["sample"], "count": int(r["n"]),
             "last_at": r["last_at"].isoformat() if r["last_at"] else ""}
            for r in blind_rows
        ]

        # ---- downvoted answers (+ the dislike→teach loop fields) ----
        # learned_memory_id links to the pending fact auto-created from a
        # correction; LEFT JOIN memory so the UI can show "already a taught fact"
        # (status='active') vs still pending.
        down_rows = conn.execute(
            "SELECT f.note, f.answer, f.created_at, "
            "       f.question, f.correction, f.status, "
            "       f.learned_memory_id AS memory_id, "
            "       m.status AS memory_status "
            "FROM message_feedback f "
            "LEFT JOIN memory m ON m.id = f.learned_memory_id "
            "WHERE f.vote = 'down' ORDER BY f.created_at DESC LIMIT 10"
        ).fetchall()
        downvotes = [
            {"note": r["note"] or "",
             "answer": (r["answer"] or "")[:160],
             "created_at": r["created_at"].isoformat() if r["created_at"] else "",
             "question": r["question"] or "",
             "correction": r["correction"] or "",
             "status": r["status"] or "",
             "memory_id": r["memory_id"],
             "memory_status": r["memory_status"],
             "memory_active": r["memory_status"] == "active"}
            for r in down_rows
        ]
        downvote_total = _scalar(conn, "SELECT count(*) FROM message_feedback WHERE vote = 'down'")
        upvote_total = _scalar(conn, "SELECT count(*) FROM message_feedback WHERE vote = 'up'")

        # ---- stale facts: active, never cited, older than the grace window ----
        stale_cut = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=STALE_DAYS)
        stale_rows = conn.execute(
            "SELECT id, key, value, source, created_by, created_at "
            "FROM memory WHERE status = 'active' AND cited_count = 0 "
            "AND created_at < %s ORDER BY created_at ASC LIMIT 20",
            (stale_cut,),
        ).fetchall()
        stale_facts = [
            {"id": r["id"], "value": r["value"], "key": r["key"], "source": r["source"],
             "created_by": r["created_by"],
             "created_at": r["created_at"].isoformat() if r["created_at"] else ""}
            for r in stale_rows
        ]

    # ---------- four-pillar score (each capped at 25) ----------
    covered = total_ans - no_src
    coverage_pct = round(100 * covered / total_ans) if total_ans else 100
    vote_total = upvote_total + downvote_total
    signal_pct = round(100 * upvote_total / vote_total) if vote_total else 100

    context_pts = min(25, (10 if docs >= 1 else 0) + min(10, pages // 20)
                     + (5 if facts_active >= 3 else 0))
    coverage_pts = round(25 * coverage_pct / 100)
    fresh_pts = 25 if not facts_active else round(
        25 * (facts_active - len(stale_facts)) / facts_active)
    signal_pts = round(25 * signal_pct / 100)
    score = context_pts + coverage_pts + fresh_pts + signal_pts

    # ---------- leverage-weighted gaps (AIS-OS: lost_pts x impact) ----------
    gaps = []

    def gap(title, detail, lost, mult):
        if lost > 0:
            gaps.append({"title": title, "detail": detail,
                         "lost": lost, "leverage": round(lost * mult, 1)})

    blind_q = sum(s["count"] for s in blind_spots)
    gap("Blind spots", f"{no_src} answer(s) had no source page"
        + (f" — top: \"{blind_spots[0]['question'][:60]}\"" if blind_spots else ""),
        25 - coverage_pts, 4 if coverage_pct < 50 else 2)
    gap("Thin corpus", f"only {docs} ready document(s), {pages} pages",
        25 - context_pts, 3)
    gap("Stale facts", f"{len(stale_facts)} active fact(s) never used in {STALE_DAYS}+ days",
        25 - fresh_pts, 1.5)
    gap("Negative feedback", f"{downvote_total} 👎 vs {upvote_total} 👍",
        25 - signal_pts, 1.5)
    gaps.sort(key=lambda g: g["leverage"], reverse=True)

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "window_days": days,
        "score": score,
        "pillars": {
            "context": context_pts, "coverage": coverage_pts,
            "freshness": fresh_pts, "signal": signal_pts,
        },
        "stats": {
            "docs": docs, "pages": pages,
            "facts_active": facts_active, "facts_pending": facts_pending,
            "answers": total_ans, "answers_no_source": no_src,
            "coverage_pct": coverage_pct,
            "upvotes": upvote_total, "downvotes": downvote_total,
            "blind_questions": blind_q,
        },
        "gaps": gaps[:3],            # top-3 by leverage (AIS-OS gap-analysis)
        "blind_spots": blind_spots,
        "downvotes_recent": downvotes,
        "stale_facts": stale_facts,
    }


# ---------------------------------------------------------- admin audit log ---
def log(actor: dict | None, action: str, target_type: str | None = None,
        target_id=None, meta: dict | None = None) -> None:
    """Append one governance row (who did what). Fail-soft — never breaks the
    mutation that triggered it. `actor` is the current_user dict."""
    import json as _json
    a = actor or {}
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO admin_audit (actor_id, actor_email, actor_role, action, "
                " target_type, target_id, meta) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (a.get("id"), a.get("email"), a.get("role"), action, target_type,
                 None if target_id is None else str(target_id), _json.dumps(meta or {})),
            )
    except Exception as e:
        print(f"[audit] log skipped: {e!r}")


def recent(days: int = 30, action: str = "", page: int = 1, page_size: int = 50) -> dict:
    """Paginated audit trail + per-action counts for the governance panel."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    where = "created_at >= (now()::date - make_interval(days => %s))::date"
    params: list = [span]
    if action:
        where += " AND action = %s"
        params.append(action)
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM admin_audit WHERE {where}", tuple(params)
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT id, actor_email, actor_role, action, target_type, target_id, "
            f" meta, to_char(created_at,'MM-DD HH24:MI') AS at "
            f"FROM admin_audit WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s",
            tuple(params) + (page_size, (page - 1) * page_size),
        ).fetchall()
        by_action = conn.execute(
            f"SELECT action, count(*) AS n FROM admin_audit WHERE {where} "
            f"GROUP BY action ORDER BY n DESC", tuple(params),
        ).fetchall()
    return {
        "rows": [dict(r) for r in rows],
        "by_action": [dict(r) for r in by_action],
        "total": total, "page": page, "page_size": page_size, "days": days,
    }
