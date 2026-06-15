"""Usage analytics — management scorecard, per-user monitor and per-user profile.
Reads the usage_daily rollup (fast at scale). Privacy-safe: COUNTS and rates only,
plus intent buckets / keyword topics — never a question string. Per-user views are
identified by id but expose no chat content."""
from . import appcfg
from . import keywords as kw
from .db import get_conn

_SORTS = {
    "questions": "questions", "active_days": "active_days",
    "last": "last_ever", "blind": "blind", "name": "lname",
}


def _pct(part, whole):
    return round(100 * part / whole) if whole else 0


# ----------------------------------------------------------- management ---
def management(days: int = 30) -> dict:
    days = days if days in (7, 30, 90, 365) else 30
    cfg = appcfg.get_config()
    span = days - 1
    with get_conn() as conn:
        reg = conn.execute(
            "SELECT count(*) AS registered, count(*) FILTER (WHERE role='admin') AS admins "
            "FROM users WHERE role <> 'pending' AND active"
        ).fetchone()
        activated = conn.execute(
            "SELECT count(DISTINCT user_id) AS n FROM usage_daily WHERE questions > 0"
        ).fetchone()["n"]
        win = conn.execute(
            "SELECT count(DISTINCT user_id) FILTER (WHERE questions>0) AS active, "
            " COALESCE(sum(questions),0) AS questions, COALESCE(sum(convos),0) AS convos, "
            " COALESCE(sum(sourced),0) AS sourced, COALESCE(sum(blind),0) AS blind, "
            " COALESCE(sum(up),0) AS up, COALESCE(sum(down),0) AS down "
            "FROM usage_daily WHERE day >= (now()::date - make_interval(days => %s))::date",
            (span,),
        ).fetchone()
        dwm = conn.execute(
            "SELECT count(DISTINCT user_id) FILTER (WHERE day >= now()::date) AS dau, "
            " count(DISTINCT user_id) FILTER (WHERE day >= now()::date - 6) AS wau, "
            " count(DISTINCT user_id) FILTER (WHERE day >= now()::date - 29) AS mau "
            "FROM usage_daily WHERE questions > 0"
        ).fetchone()
        ret = conn.execute(
            "WITH curr AS (SELECT DISTINCT user_id FROM usage_daily WHERE questions>0 AND day >= now()::date-6), "
            " prev AS (SELECT DISTINCT user_id FROM usage_daily WHERE questions>0 AND day BETWEEN now()::date-13 AND now()::date-7) "
            "SELECT (SELECT count(*) FROM prev) AS prev_n, "
            " (SELECT count(*) FROM prev p JOIN curr c ON c.user_id=p.user_id) AS retained"
        ).fetchone()
        trend = conn.execute(
            "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(sum(u.questions),0) AS n "
            "FROM generate_series(now()::date - make_interval(days => %s), now()::date, interval '1 day') d "
            "LEFT JOIN usage_daily u ON u.day = d::date GROUP BY d ORDER BY d",
            (span,),
        ).fetchall()
        by_role = conn.execute(
            "SELECT u.role, count(*) AS registered, "
            " count(*) FILTER (WHERE a.uid IS NOT NULL) AS activated "
            "FROM users u LEFT JOIN (SELECT DISTINCT user_id uid FROM usage_daily WHERE questions>0) a "
            " ON a.uid = u.id WHERE u.role <> 'pending' GROUP BY u.role ORDER BY registered DESC"
        ).fetchall()
        # previous window (same length, immediately before) for ▲▼ deltas
        prev = conn.execute(
            "SELECT count(DISTINCT user_id) FILTER (WHERE questions>0) AS active, "
            " COALESCE(sum(questions),0) AS questions, "
            " COALESCE(sum(sourced),0) AS sourced, COALESCE(sum(blind),0) AS blind, "
            " COALESCE(sum(up),0) AS up, COALESCE(sum(down),0) AS down "
            "FROM usage_daily WHERE day BETWEEN (now()::date - make_interval(days => %s))::date "
            " AND (now()::date - make_interval(days => %s))::date",
            (2 * days - 1, days),
        ).fetchone()
        # per-user question counts in window → power-user threshold (P90)
        ucounts = conn.execute(
            "SELECT sum(questions) AS q FROM usage_daily "
            "WHERE day >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY user_id HAVING sum(questions) > 0 ORDER BY q DESC",
            (span,),
        ).fetchall()
        # weekly active users, last ~8 weeks
        wau_weeks = conn.execute(
            "SELECT to_char(date_trunc('week', day),'MM-DD') AS label, "
            " count(DISTINCT user_id) AS n FROM usage_daily "
            "WHERE questions>0 AND day >= now()::date - 55 GROUP BY 1 ORDER BY 1"
        ).fetchall()

    answered = win["sourced"] + win["blind"]
    minutes = cfg["minutes_saved_per_answer"]
    price = cfg["llm_price_per_mtok"]
    tpa = cfg["tokens_per_answer"]
    cost = answered * tpa * price / 1_000_000
    cost_per_q = tpa * price / 1_000_000
    monthly_run_rate = round((cost / days) * 30, 2) if days else 0

    def _delta(cur, prv):
        if not prv:
            return None
        return round(100 * (cur - prv) / prv)

    p_answered = prev["sourced"] + prev["blind"]
    deltas = {
        "questions": _delta(win["questions"], prev["questions"]),
        "active": _delta(win["active"], prev["active"]),
        "answered": _delta(answered, p_answered),
        "helpful": _delta(_pct(win["up"], win["up"] + win["down"]),
                          _pct(prev["up"], prev["up"] + prev["down"])),
    }
    # power users = window questions >= P90 (min 5)
    qs_sorted = [r["q"] for r in ucounts]
    p90 = qs_sorted[int(len(qs_sorted) * 0.1)] if qs_sorted else 0
    power_cut = max(5, p90)
    power_users = sum(1 for q in qs_sorted if q >= power_cut)
    funnel = {
        "registered": reg["registered"], "activated": activated,
        "weekly_active": dwm["wau"], "power_users": power_users,
    }

    topics = []
    try:
        topics = kw.keyword_analysis(days).get("keywords", [])[:10]
    except Exception:
        pass

    return {
        "days": days,
        "adoption": {
            "registered": reg["registered"], "activated": activated,
            "activation_pct": _pct(activated, reg["registered"]),
            "dau": dwm["dau"], "wau": dwm["wau"], "mau": dwm["mau"],
            "stickiness_pct": _pct(dwm["dau"], dwm["mau"]),
            "active_window": win["active"],
        },
        "engagement": {
            "questions": win["questions"], "convos": win["convos"],
            "qs_per_active": round(win["questions"] / win["active"], 1) if win["active"] else 0,
            "multi_pct": _pct(win["questions"] - win["convos"], win["questions"]),
            "retention_pct": _pct(ret["retained"], ret["prev_n"]),
        },
        "quality": {
            "helpful_pct": _pct(win["up"], win["up"] + win["down"]),
            "sourced_pct": _pct(win["sourced"], answered),
            "blind_pct": _pct(win["blind"], answered),
            "votes": win["up"] + win["down"], "downvotes": win["down"],
        },
        "value": {
            "answered": answered,
            "hours_saved": round(answered * minutes / 60, 1),
            "minutes_per_answer": minutes,
            "llm_cost": round(cost, 2),
            "cost_per_question": round(cost_per_q, 4),
            "monthly_run_rate": monthly_run_rate,
            "tokens_per_answer": tpa, "price_per_mtok": price,
        },
        "deltas": deltas,
        "funnel": funnel,
        "wau_weeks": wau_weeks,
        "trend": trend,
        "by_role": by_role,
        "topics": topics,
        "config": cfg,
    }


# ----------------------------------------------------------- monitor ---
def users_monitor(days: int = 30, sort: str = "questions", order: str = "desc",
                  q: str = "", status: str = "all", role: str = "", auth: str = "",
                  page: int = 1, page_size: int = 25) -> dict:
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    page = max(1, int(page))
    page_size = max(5, min(int(page_size), 2000))
    off = (page - 1) * page_size
    sort_col = _SORTS.get(sort, "questions")
    order_sql = "ASC" if order == "asc" else "DESC"

    where = ["u.role <> 'pending'"]
    params: dict = {"cut": None}
    if q:
        where.append("(u.name ILIKE %(q)s OR u.email ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    if role in ("admin", "user"):
        where.append("u.role = %(role)s")
        params["role"] = role
    if auth in ("local", "ldap", "oidc"):
        where.append("u.auth_source = %(auth)s")
        params["auth"] = auth
    if status == "never":
        where.append("at.last_ever IS NULL")
    elif status == "active":
        where.append("at.last_ever >= now()::date - 6")
    elif status == "dormant":
        where.append("(at.last_ever IS NOT NULL AND at.last_ever < now()::date - 6)")
    where_sql = " AND ".join(where)

    base = (
        "WITH agg AS ("
        " SELECT user_id, sum(questions) questions, sum(convos) convos, "
        "  sum(sourced) sourced, sum(blind) blind, sum(up) up, sum(down) down, "
        "  count(*) FILTER (WHERE questions>0) active_days "
        " FROM usage_daily WHERE day >= (now()::date - make_interval(days => %(span)s))::date "
        " GROUP BY user_id), "
        "alltime AS (SELECT user_id, max(day) last_ever FROM usage_daily GROUP BY user_id) "
        "SELECT u.id, u.name, lower(coalesce(u.name,u.email)) AS lname, u.email, u.role, "
        "  u.created_at, u.last_login, u.auth_source, "
        "  COALESCE(a.questions,0) questions, COALESCE(a.convos,0) convos, "
        "  COALESCE(a.sourced,0) sourced, COALESCE(a.blind,0) blind, "
        "  COALESCE(a.up,0) up, COALESCE(a.down,0) down, "
        "  COALESCE(a.active_days,0) active_days, at.last_ever "
        "FROM users u LEFT JOIN agg a ON a.user_id=u.id "
        "LEFT JOIN alltime at ON at.user_id=u.id "
        f"WHERE {where_sql} "
    )
    params["span"] = span
    with get_conn() as conn:
        total = conn.execute(
            "SELECT count(*) AS n FROM users u "
            "LEFT JOIN (SELECT user_id, max(day) last_ever FROM usage_daily GROUP BY user_id) at "
            "ON at.user_id=u.id WHERE " + where_sql, params
        ).fetchone()["n"]
        # segment summary across ALL non-pending users (ignores status filter)
        seg = conn.execute(
            "SELECT count(*) FILTER (WHERE at.last_ever IS NULL) AS never, "
            " count(*) FILTER (WHERE at.last_ever >= now()::date - 6) AS active, "
            " count(*) FILTER (WHERE at.last_ever BETWEEN now()::date - 29 AND now()::date - 7) AS dormant, "
            " count(*) FILTER (WHERE at.last_ever < now()::date - 29) AS inactive "
            "FROM users u LEFT JOIN (SELECT user_id, max(day) last_ever FROM usage_daily GROUP BY user_id) at "
            "ON at.user_id=u.id WHERE u.role <> 'pending'"
        ).fetchone()
        rows = conn.execute(
            base + f"ORDER BY {sort_col} {order_sql} NULLS LAST, u.id ASC "
            "LIMIT %(ps)s OFFSET %(off)s",
            {**params, "ps": page_size, "off": off},
        ).fetchall()

        ids = [r["id"] for r in rows]
        spark: dict[int, list] = {i: [0] * 14 for i in ids}
        intents: dict[int, str] = {}
        if ids:
            srows = conn.execute(
                "SELECT user_id, (now()::date - day) AS ago, questions FROM usage_daily "
                "WHERE user_id = ANY(%s) AND day >= now()::date - 13",
                (ids,),
            ).fetchall()
            for s in srows:
                a = int(s["ago"])
                if 0 <= a <= 13:
                    spark[s["user_id"]][13 - a] = s["questions"]
            # top intent per user from their recent questions (text never returned)
            qrows = conn.execute(
                "SELECT c.user_id, m.text FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE c.user_id = ANY(%s) AND m.role='user' AND m.created_at > now() - interval '90 days'",
                (ids,),
            ).fetchall()
            buckets: dict[int, dict] = {}
            for qr in qrows:
                b = kw._classify(kw._tokens(qr["text"]))
                buckets.setdefault(qr["user_id"], {})
                buckets[qr["user_id"]][b] = buckets[qr["user_id"]].get(b, 0) + 1
            for uid, bb in buckets.items():
                intents[uid] = max(bb, key=bb.get)

    out = []
    for r in rows:
        out.append({
            "id": r["id"], "name": r["name"] or r["email"], "email": r["email"], "role": r["role"],
            "auth_source": r["auth_source"], "created_at": r["created_at"], "last_active": r["last_ever"],
            "questions": r["questions"], "convos": r["convos"],
            "active_days": r["active_days"], "blind": r["blind"],
            "multi_pct": _pct(r["questions"] - r["convos"], r["questions"]),
            "helpful_pct": _pct(r["up"], r["up"] + r["down"]) if (r["up"] + r["down"]) else None,
            "sourced_pct": _pct(r["sourced"], r["sourced"] + r["blind"]) if (r["sourced"] + r["blind"]) else None,
            "status": _status(r["last_ever"]),
            "top_intent": intents.get(r["id"]),
            "spark": spark.get(r["id"], []),
        })
    return {"users": out, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size, "days": days,
            "sort": sort, "order": order, "status": status, "q": q,
            "segments": seg}


def _status(last_ever) -> str:
    if last_ever is None:
        return "never"
    import datetime
    today = datetime.date.today()
    age = (today - last_ever).days
    if age <= 6:
        return "active"
    if age <= 29:
        return "dormant"
    return "inactive"


# ----------------------------------------------------------- profile ---
def user_profile(user_id: int, days: int = 90) -> dict:
    days = days if days in (7, 30, 90, 365) else 90
    span = days - 1
    with get_conn() as conn:
        u = conn.execute(
            "SELECT id, name, email, role, created_at, last_login, active, auth_source FROM users WHERE id=%s",
            (user_id,),
        ).fetchone()
        if not u:
            return {"error": "not found"}
        extra = conn.execute(
            "SELECT (SELECT count(*) FROM conversations WHERE user_id=%s) AS conversations, "
            " (SELECT count(*) FROM message_feedback WHERE user_id=%s) AS feedback_given, "
            " (SELECT count(*) FROM message_feedback WHERE user_id=%s AND vote='down') AS downvotes_given",
            (user_id, user_id, user_id),
        ).fetchone()
        agg = conn.execute(
            "SELECT COALESCE(sum(questions),0) questions, COALESCE(sum(convos),0) convos, "
            " COALESCE(sum(sourced),0) sourced, COALESCE(sum(blind),0) blind, "
            " COALESCE(sum(up),0) up, COALESCE(sum(down),0) down, "
            " count(*) FILTER (WHERE questions>0) active_days, max(day) last_active "
            "FROM usage_daily WHERE user_id=%s", (user_id,),
        ).fetchone()
        activity = conn.execute(
            "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(u.questions,0) AS n "
            "FROM generate_series(now()::date - make_interval(days => %s), now()::date, interval '1 day') d "
            "LEFT JOIN usage_daily u ON u.day=d::date AND u.user_id=%s ORDER BY d",
            (span, user_id),
        ).fetchall()
        docs = conn.execute(
            "SELECT d.name, count(DISTINCT m.id) AS hits FROM messages m "
            "JOIN conversations c ON c.id=m.conversation_id "
            "CROSS JOIN LATERAL jsonb_array_elements(m.pages) e JOIN pages pg ON pg.id=(e->>'page_id')::int "
            "JOIN docs d ON d.id=pg.doc_id "
            "WHERE c.user_id=%s AND m.role='bot' AND m.pages::text <> '[]' "
            "GROUP BY d.name ORDER BY hits DESC LIMIT 6", (user_id,),
        ).fetchall()
        facts = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE status='active') AS active, "
            " count(*) FILTER (WHERE status='pending') AS pending FROM memory WHERE created_by=%s",
            (u["email"],),
        ).fetchone()
        qrows = conn.execute(
            "SELECT m.text FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE c.user_id=%s AND m.role='user' AND m.created_at > now() - interval '180 days'",
            (user_id,),
        ).fetchall()

    # intent mix (privacy: only bucket counts)
    intents: dict[str, int] = {}
    for qr in qrows:
        b = kw._classify(kw._tokens(qr["text"]))
        intents[b] = intents.get(b, 0) + 1
    intent_list = sorted(({"intent": k, "count": v} for k, v in intents.items()), key=lambda x: -x["count"])

    from . import routes  # _friendly_doc (lazy, avoids cycle)
    for d in docs:
        d["label"] = routes._friendly_doc(d["name"]) or d["name"]

    answered = agg["sourced"] + agg["blind"]
    return {
        "user": {"id": u["id"], "name": u["name"] or u["email"], "email": u["email"],
                 "role": u["role"], "created_at": u["created_at"], "last_login": u["last_login"],
                 "active": u["active"], "auth_source": u["auth_source"], "status": _status(agg["last_active"]),
                 "conversations": extra["conversations"], "feedback_given": extra["feedback_given"],
                 "downvotes_given": extra["downvotes_given"]},
        "kpis": {
            "questions": agg["questions"], "convos": agg["convos"],
            "active_days": agg["active_days"], "last_active": agg["last_active"],
            "multi_pct": _pct(agg["questions"] - agg["convos"], agg["questions"]),
            "helpful_pct": _pct(agg["up"], agg["up"] + agg["down"]) if (agg["up"] + agg["down"]) else None,
            "sourced_pct": _pct(agg["sourced"], answered) if answered else None,
            "blind": agg["blind"], "downvotes": agg["down"],
        },
        "activity": activity,
        "intents": intent_list,
        "top_docs": docs,
        "facts": facts,
        "days": days,
    }


# ----------------------------------------------------- answer telemetry ---
def record_metric(*, message_id=None, conversation_id=None, user_id=None,
                  model=None, mode=None, ms_first_token=None, ms_total=None,
                  tok_in=0, tok_out=0, seed_n=0, wide_n=0, wider_fired=False,
                  cited_n=0, blind=False) -> None:
    """Persist one answer's telemetry row. Cost derived from the ROI price knob
    (real token counts × $/Mtok). Fail-soft — caller wraps in try/except."""
    price = float(appcfg.get_config().get("llm_price_per_mtok", 0.30))
    cost = round((int(tok_in or 0) + int(tok_out or 0)) / 1_000_000 * price, 6)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO answer_metrics (message_id, conversation_id, user_id, model, "
            " mode, ms_first_token, ms_total, tok_in, tok_out, cost_usd, seed_n, "
            " wide_n, wider_fired, cited_n, blind) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (message_id, conversation_id, user_id, model, mode, ms_first_token,
             ms_total, int(tok_in or 0), int(tok_out or 0), cost, seed_n, wide_n,
             bool(wider_fired), cited_n, bool(blind)),
        )


def perf(days: int = 30) -> dict:
    """Admin performance scorecard: latency (p50/p95), real token spend, retrieval
    hit-rate + wider-retry rate, model split, daily trend. Reads answer_metrics."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    out: dict = {"days": days}
    with get_conn() as conn:
        agg = conn.execute(
            "SELECT count(*) AS answers, "
            " COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms_total),0) AS p50_total, "
            " COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms_total),0) AS p95_total, "
            " COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms_first_token),0) AS p50_first, "
            " COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms_first_token),0) AS p95_first, "
            " COALESCE(avg(tok_in),0) AS avg_tok_in, COALESCE(avg(tok_out),0) AS avg_tok_out, "
            " COALESCE(sum(tok_in+tok_out),0) AS tok_total, COALESCE(sum(cost_usd),0) AS cost_total, "
            " COALESCE(avg(seed_n),0) AS avg_seed, COALESCE(avg(cited_n),0) AS avg_cited, "
            " count(*) FILTER (WHERE cited_n > 0) AS grounded, "
            " count(*) FILTER (WHERE wider_fired) AS wider, "
            " count(*) FILTER (WHERE blind) AS blind, "
            " count(DISTINCT user_id) AS users "
            "FROM answer_metrics "
            "WHERE created_at >= (now()::date - make_interval(days => %s))::date",
            (span,),
        ).fetchone()
        a = dict(agg or {})
        answers = a.get("answers", 0) or 0
        out["kpis"] = {
            "answers": answers,
            "p50_total_ms": round(a.get("p50_total") or 0),
            "p95_total_ms": round(a.get("p95_total") or 0),
            "p50_first_ms": round(a.get("p50_first") or 0),
            "p95_first_ms": round(a.get("p95_first") or 0),
            "avg_tok_in": round(a.get("avg_tok_in") or 0),
            "avg_tok_out": round(a.get("avg_tok_out") or 0),
            "tok_total": int(a.get("tok_total") or 0),
            "cost_total": round(float(a.get("cost_total") or 0), 4),
            "cost_per_answer": round(float(a.get("cost_total") or 0) / answers, 5) if answers else 0,
            "avg_seed": round(float(a.get("avg_seed") or 0), 1),
            "avg_cited": round(float(a.get("avg_cited") or 0), 1),
            "hit_rate": _pct(a.get("grounded", 0), answers),
            "wider_rate": _pct(a.get("wider", 0), answers),
            "blind_rate": _pct(a.get("blind", 0), answers),
            "users": a.get("users", 0) or 0,
        }
        # daily latency + cost trend
        out["trend"] = [dict(r) for r in conn.execute(
            "SELECT to_char(created_at::date,'MM-DD') AS label, count(*) AS n, "
            " COALESCE(round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ms_total)),0) AS p50_ms, "
            " COALESCE(round(sum(cost_usd),4),0) AS cost "
            "FROM answer_metrics "
            "WHERE created_at >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY 1 ORDER BY 1", (span,),
        ).fetchall()]
        # model split
        out["models"] = [dict(r) for r in conn.execute(
            "SELECT COALESCE(model,'—') AS model, count(*) AS n, "
            " COALESCE(round(avg(ms_total)),0) AS avg_ms, COALESCE(round(sum(cost_usd),4),0) AS cost "
            "FROM answer_metrics "
            "WHERE created_at >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY 1 ORDER BY n DESC", (span,),
        ).fetchall()]
        # slowest recent answers (no chat text — ids + timings only)
        out["slowest"] = [dict(r) for r in conn.execute(
            "SELECT message_id, ms_total, ms_first_token, tok_out, cited_n, blind, "
            " to_char(created_at,'MM-DD HH24:MI') AS at "
            "FROM answer_metrics "
            "WHERE created_at >= (now()::date - make_interval(days => %s))::date "
            "ORDER BY ms_total DESC NULLS LAST LIMIT 8", (span,),
        ).fetchall()]
    return out


# ------------------------------------------------- document performance ---
def doc_performance(days: int = 30) -> dict:
    """Per-document scorecard + dead-weight detection. Pure reads:
      - cites      : distinct bot answers that cited any page of the doc
      - votes      : 👍/👎 on answers that cited the doc (via message_feedback.page_ids)
      - orphans    : docs never cited in ANY answer (dead weight)
      - cold       : docs cited before but not in the window (going stale)
    No new tables — joins messages.pages / message_feedback.page_ids → pages.doc_id."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    with get_conn() as conn:
        rows = conn.execute(
            "WITH cite AS ("
            "  SELECT DISTINCT m.id AS msg_id, pg.doc_id, m.created_at "
            "  FROM messages m "
            "  CROSS JOIN LATERAL jsonb_array_elements(m.pages) e "
            "  JOIN pages pg ON pg.id = (e->>'page_id')::int "
            "  WHERE m.role='bot' AND jsonb_typeof(m.pages)='array' AND m.pages <> '[]'::jsonb "
            "), fb AS ("
            "  SELECT pg.doc_id, f.vote "
            "  FROM message_feedback f "
            "  CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(f.page_ids,'[]'::jsonb)) pid "
            "  JOIN pages pg ON pg.id = pid::int "
            ") "
            "SELECT d.id, d.name, d.status, d.lang, d.page_count, d.created_at, "
            "  (SELECT count(*) FROM pages p2 WHERE p2.doc_id = d.id AND p2.vision_text <> '') AS vision_pages, "
            "  EXTRACT(DAY FROM now() - d.created_at)::int AS age_days, "
            "  COUNT(DISTINCT c.msg_id) AS cites_all, "
            "  COUNT(DISTINCT c.msg_id) FILTER ("
            "     WHERE c.created_at >= (now()::date - make_interval(days => %s))::date) AS cites_win, "
            "  MAX(c.created_at) AS last_cited_at, "
            "  COUNT(*) FILTER (WHERE fb.vote='up') AS up, "
            "  COUNT(*) FILTER (WHERE fb.vote='down') AS down "
            "FROM docs d "
            "LEFT JOIN cite c ON c.doc_id = d.id "
            "LEFT JOIN fb ON fb.doc_id = d.id "
            "GROUP BY d.id "
            "ORDER BY cites_win DESC, cites_all DESC",
            (span,),
        ).fetchall()

    docs = []
    for r in rows:
        d = dict(r)
        votes = (d["up"] or 0) + (d["down"] or 0)
        d["helpful_pct"] = _pct(d["up"] or 0, votes) if votes else None
        d["indexed_pct"] = _pct(d["vision_pages"] or 0, d["page_count"] or 0)
        last = d.pop("last_cited_at", None)
        d["last_cited_days"] = (
            None if last is None else
            int((__import__("datetime").datetime.now(last.tzinfo) - last).days)
        )
        d.pop("created_at", None)
        docs.append(d)

    orphans = [d for d in docs if (d["cites_all"] or 0) == 0]
    cold = [d for d in docs
            if (d["cites_all"] or 0) > 0 and (d["cites_win"] or 0) == 0]
    cold.sort(key=lambda d: d["last_cited_days"] or 0, reverse=True)
    return {
        "days": days,
        "docs": docs,
        "orphans": orphans,
        "cold": cold,
        "summary": {
            "total": len(docs),
            "orphans": len(orphans),
            "cold": len(cold),
            "cited_win": sum(1 for d in docs if (d["cites_win"] or 0) > 0),
            "downvoted": sum(1 for d in docs if (d["down"] or 0) > 0),
        },
    }


# -------------------------------------------------- citation trust (verify) ---
def verify_report(days: int = 30) -> dict:
    """Citation-accuracy summary: avg trust score, support rate, weakest answers,
    coverage of the verify pass. Reads answer_verify + answer_metrics."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    with get_conn() as conn:
        ver = conn.execute(
            "SELECT count(*) AS verdicts, "
            " count(*) FILTER (WHERE supports IS TRUE) AS supporting, "
            " count(*) FILTER (WHERE supports IS FALSE) AS refuting, "
            " count(DISTINCT message_id) AS answers, "
            " COALESCE(round(avg(confidence)::numeric,2),0) AS avg_conf "
            "FROM answer_verify "
            "WHERE created_at >= (now()::date - make_interval(days => %s))::date",
            (span,),
        ).fetchone()
        v = dict(ver or {})
        verdicts = v.get("verdicts", 0) or 0
        score = conn.execute(
            "SELECT COALESCE(round(avg(verify_score)),0) AS avg_score, "
            " count(*) AS scored, count(*) FILTER (WHERE verify_score < 60) AS weak "
            "FROM answer_metrics WHERE verify_score IS NOT NULL "
            "  AND created_at >= (now()::date - make_interval(days => %s))::date",
            (span,),
        ).fetchone()
        s = dict(score or {})
        # how much of the grounded corpus is still unverified
        pending = conn.execute(
            "SELECT count(*) AS n FROM messages m "
            "WHERE m.role='bot' AND jsonb_typeof(m.pages)='array' AND m.pages <> '[]'::jsonb "
            "  AND NOT EXISTS (SELECT 1 FROM answer_verify av WHERE av.message_id = m.id)"
        ).fetchone()["n"]
        # weakest answers (lowest score) — ids + a sample refuting reason, no chat text
        weak = [dict(r) for r in conn.execute(
            "SELECT am.message_id, am.verify_score, am.cited_n, "
            " to_char(am.verify_at,'MM-DD HH24:MI') AS at, "
            " (SELECT reason FROM answer_verify av WHERE av.message_id = am.message_id "
            "    AND av.supports IS FALSE ORDER BY av.confidence DESC NULLS LAST LIMIT 1) AS why "
            "FROM answer_metrics am "
            "WHERE am.verify_score IS NOT NULL "
            "  AND am.created_at >= (now()::date - make_interval(days => %s))::date "
            "ORDER BY am.verify_score ASC, am.cited_n DESC LIMIT 10", (span,),
        ).fetchall()]
    return {
        "days": days,
        "kpis": {
            "avg_score": int(s.get("avg_score") or 0),
            "scored": s.get("scored", 0) or 0,
            "weak": s.get("weak", 0) or 0,
            "verdicts": verdicts,
            "support_rate": _pct(v.get("supporting", 0), verdicts) if verdicts else 0,
            "refute_rate": _pct(v.get("refuting", 0), verdicts) if verdicts else 0,
            "avg_confidence": float(v.get("avg_conf") or 0),
            "answers_verified": v.get("answers", 0) or 0,
            "pending": pending,
        },
        "weak": weak,
    }


# ------------------------------------------------------------ engagement ---
def engagement(days: int = 30) -> dict:
    """Session depth, follow-up rate, signup retention (D1/D7/D30), dormant users."""
    days = days if days in (7, 30, 90, 365) else 30
    with get_conn() as conn:
        depth = conn.execute(
            "WITH t AS (SELECT c.id, count(*) FILTER (WHERE m.role='user') AS turns "
            "  FROM conversations c JOIN messages m ON m.conversation_id=c.id GROUP BY c.id) "
            "SELECT count(*) AS convos, COALESCE(round(avg(turns),1),0) AS avg_turns, "
            " count(*) FILTER (WHERE turns>1) AS multi, COALESCE(max(turns),0) AS max_turns, "
            " count(*) FILTER (WHERE turns=1) AS b1, "
            " count(*) FILTER (WHERE turns BETWEEN 2 AND 3) AS b2, "
            " count(*) FILTER (WHERE turns BETWEEN 4 AND 6) AS b3, "
            " count(*) FILTER (WHERE turns>=7) AS b4 FROM t WHERE turns>0"
        ).fetchone()
        d = dict(depth or {})
        convos = d.get("convos", 0) or 0
        ret = conn.execute(
            "SELECT "
            " count(*) FILTER (WHERE age>=1) AS elig1, count(*) FILTER (WHERE d1) AS r1, "
            " count(*) FILTER (WHERE age>=7) AS elig7, count(*) FILTER (WHERE d7) AS r7, "
            " count(*) FILTER (WHERE age>=30) AS elig30, count(*) FILTER (WHERE d30) AS r30 "
            "FROM (SELECT u.id, (now()::date - u.created_at::date) AS age, "
            "  EXISTS(SELECT 1 FROM usage_daily ud WHERE ud.user_id=u.id AND ud.questions>0 "
            "    AND ud.day > u.created_at::date AND ud.day <= u.created_at::date+1) AS d1, "
            "  EXISTS(SELECT 1 FROM usage_daily ud WHERE ud.user_id=u.id AND ud.questions>0 "
            "    AND ud.day > u.created_at::date AND ud.day <= u.created_at::date+7) AS d7, "
            "  EXISTS(SELECT 1 FROM usage_daily ud WHERE ud.user_id=u.id AND ud.questions>0 "
            "    AND ud.day > u.created_at::date AND ud.day <= u.created_at::date+30) AS d30 "
            "  FROM users u WHERE u.role<>'pending') t"
        ).fetchone()
        r = dict(ret or {})
        dormant = [dict(x) for x in conn.execute(
            "SELECT u.id, u.email, u.role, max(ud.day) AS last_day, "
            " (now()::date - max(ud.day)) AS quiet_days "
            "FROM users u JOIN usage_daily ud ON ud.user_id=u.id AND ud.questions>0 "
            "WHERE u.role<>'pending' GROUP BY u.id "
            "HAVING max(ud.day) < now()::date - 14 ORDER BY max(ud.day) ASC LIMIT 20"
        ).fetchall()]
    for x in dormant:
        x["last_day"] = x["last_day"].isoformat() if x.get("last_day") else None
    return {
        "days": days,
        "sessions": {
            "convos": convos, "avg_turns": float(d.get("avg_turns") or 0),
            "max_turns": d.get("max_turns", 0) or 0,
            "followup_pct": _pct(d.get("multi", 0), convos),
            "depth": {"one": d.get("b1", 0) or 0, "short": d.get("b2", 0) or 0,
                      "medium": d.get("b3", 0) or 0, "deep": d.get("b4", 0) or 0},
        },
        "retention": {
            "d1": _pct(r.get("r1", 0), r.get("elig1", 0)),
            "d7": _pct(r.get("r7", 0), r.get("elig7", 0)),
            "d30": _pct(r.get("r30", 0), r.get("elig30", 0)),
            "elig": {"d1": r.get("elig1", 0) or 0, "d7": r.get("elig7", 0) or 0, "d30": r.get("elig30", 0) or 0},
        },
        "dormant": dormant,
    }


# ----------------------------------------------------- self-learning loop ---
def learning(days: int = 30) -> dict:
    """Self-learning funnel: extracted→pending→approved→cited, reject-rate, and a
    fact-usefulness leaderboard. Counts only — no chat text."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    with get_conn() as conn:
        f = dict(conn.execute(
            "SELECT "
            " count(*) FILTER (WHERE source='chat') AS extracted, "
            " count(*) FILTER (WHERE source='chat' AND status='pending') AS pending, "
            " count(*) FILTER (WHERE source='chat' AND status='active') AS approved, "
            " count(*) FILTER (WHERE source='chat' AND status='rejected') AS rejected, "
            " count(*) FILTER (WHERE source='chat' AND status='active' AND COALESCE(cited_count,0)>0) AS cited, "
            " count(*) FILTER (WHERE source IS DISTINCT FROM 'chat') AS taught "
            "FROM memory"
        ).fetchone() or {})
        decided = (f.get("approved", 0) or 0) + (f.get("rejected", 0) or 0)
        top = [dict(x) for x in conn.execute(
            "SELECT id, COALESCE(key, left(value,60)) AS label, value, "
            " COALESCE(cited_count,0) AS cited_count, status, source "
            "FROM memory WHERE status='active' AND COALESCE(cited_count,0)>0 "
            "ORDER BY cited_count DESC LIMIT 10"
        ).fetchall()]
        trend = [dict(x) for x in conn.execute(
            "SELECT to_char(created_at::date,'MM-DD') AS label, count(*) AS n "
            "FROM memory WHERE source='chat' "
            "  AND created_at >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY 1 ORDER BY 1", (span,),
        ).fetchall()]
    return {
        "days": days,
        "funnel": {
            "extracted": f.get("extracted", 0) or 0,
            "pending": f.get("pending", 0) or 0,
            "approved": f.get("approved", 0) or 0,
            "rejected": f.get("rejected", 0) or 0,
            "cited": f.get("cited", 0) or 0,
            "taught": f.get("taught", 0) or 0,
        },
        "reject_rate": _pct(f.get("rejected", 0), decided) if decided else 0,
        "cited_rate": _pct(f.get("cited", 0), f.get("approved", 0)) if f.get("approved") else 0,
        "top_facts": [{"id": t["id"], "label": t["label"], "cited_count": t["cited_count"],
                       "source": t["source"]} for t in top],
        "trend": trend,
    }


def learning_overview(days: int = 30) -> dict:
    """Everything the Workspace 'Learning' page shows: the funnel (reused) PLUS
    totals, auto-vs-hand split, confidence buckets, citations, a learned-per-day
    series, and the most-recent learned facts. Counts/metadata only — no chat text
    beyond the fact itself (already stored)."""
    base = learning(days)
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    with get_conn() as conn:
        t = dict(conn.execute(
            "SELECT count(*) AS total, "
            " count(*) FILTER (WHERE status='active') AS active, "
            " count(*) FILTER (WHERE status='pending') AS pending, "
            " count(*) FILTER (WHERE auto=true) AS auto, "
            " count(*) FILTER (WHERE auto IS NOT true) AS hand, "
            " COALESCE(sum(cited_count),0) AS citations, "
            " ROUND(AVG(confidence) FILTER (WHERE auto=true)::numeric,2) AS avg_conf "
            "FROM memory WHERE status <> 'rejected'"
        ).fetchone() or {})
        conf = dict(conn.execute(
            "SELECT "
            " count(*) FILTER (WHERE confidence < 0.7) AS lo, "
            " count(*) FILTER (WHERE confidence >= 0.7 AND confidence < 0.9) AS mid, "
            " count(*) FILTER (WHERE confidence >= 0.9 AND confidence < 0.95) AS hi, "
            " count(*) FILTER (WHERE confidence >= 0.95) AS top "
            "FROM memory WHERE auto=true AND confidence IS NOT NULL"
        ).fetchone() or {})
        # learned per day, split auto vs hand
        per_day = [dict(x) for x in conn.execute(
            "SELECT to_char(created_at::date,'MM-DD') AS label, "
            " count(*) FILTER (WHERE auto=true) AS auto, "
            " count(*) FILTER (WHERE auto IS NOT true) AS hand "
            "FROM memory WHERE status<>'rejected' "
            " AND created_at >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY 1 ORDER BY 1", (span,),
        ).fetchall()]
        recent = [dict(x) for x in conn.execute(
            "SELECT id, left(value,140) AS value, status, source, auto, confidence, "
            " created_by, created_at, left(COALESCE(origin_question,''),120) AS origin "
            "FROM memory WHERE status<>'rejected' ORDER BY id DESC LIMIT 15"
        ).fetchall()]
    return {
        **base,
        "totals": {
            "total": t.get("total", 0) or 0,
            "active": t.get("active", 0) or 0,
            "pending": t.get("pending", 0) or 0,
            "auto": t.get("auto", 0) or 0,
            "hand": t.get("hand", 0) or 0,
            "citations": t.get("citations", 0) or 0,
            "avg_conf": float(t.get("avg_conf") or 0),
        },
        "confidence": {"lo": conf.get("lo", 0) or 0, "mid": conf.get("mid", 0) or 0,
                       "hi": conf.get("hi", 0) or 0, "top": conf.get("top", 0) or 0},
        "per_day": per_day,
        "recent": [{
            "id": r["id"], "value": r["value"], "status": r["status"], "source": r["source"],
            "auto": bool(r["auto"]), "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "created_by": r["created_by"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
            "origin": r["origin"],
        } for r in recent],
    }


# -------------------------------------------------------------- security ---
def security(days: int = 30) -> dict:
    """Auth events: failed logins, top offenders, role changes, recent feed."""
    days = days if days in (7, 30, 90, 365) else 30
    span = days - 1
    with get_conn() as conn:
        k = dict(conn.execute(
            "SELECT "
            " count(*) FILTER (WHERE event='login_ok') AS ok, "
            " count(*) FILTER (WHERE event='login_fail') AS fail, "
            " count(DISTINCT email) FILTER (WHERE event='login_fail') AS fail_accts, "
            " count(*) FILTER (WHERE event='role_change') AS role_changes, "
            " count(*) FILTER (WHERE event='user_deactivate') AS deactivations "
            "FROM auth_event WHERE created_at >= (now()::date - make_interval(days => %s))::date",
            (span,),
        ).fetchone() or {})
        top_fail = [dict(x) for x in conn.execute(
            "SELECT email, count(*) AS n, to_char(max(created_at),'MM-DD HH24:MI') AS last "
            "FROM auth_event WHERE event='login_fail' "
            "  AND created_at >= (now()::date - make_interval(days => %s))::date "
            "GROUP BY email ORDER BY n DESC LIMIT 8", (span,),
        ).fetchall()]
        recent = [dict(x) for x in conn.execute(
            "SELECT event, email, actor_email, meta, to_char(created_at,'MM-DD HH24:MI') AS at "
            "FROM auth_event ORDER BY id DESC LIMIT 30"
        ).fetchall()]
        last_logins = [dict(x) for x in conn.execute(
            "SELECT u.email, u.role, max(ae.created_at) AS last "
            "FROM users u LEFT JOIN auth_event ae ON ae.email=u.email AND ae.event='login_ok' "
            "WHERE u.role<>'pending' GROUP BY u.id ORDER BY max(ae.created_at) DESC NULLS LAST LIMIT 12"
        ).fetchall()]
    for x in last_logins:
        x["last"] = x["last"].isoformat() if x.get("last") else None
    fail = k.get("fail", 0) or 0
    ok = k.get("ok", 0) or 0
    return {
        "days": days,
        "kpis": {
            "logins_ok": ok, "logins_fail": fail,
            "fail_rate": _pct(fail, ok + fail) if (ok + fail) else 0,
            "fail_accounts": k.get("fail_accts", 0) or 0,
            "role_changes": k.get("role_changes", 0) or 0,
            "deactivations": k.get("deactivations", 0) or 0,
        },
        "top_fail": top_fail,
        "recent": recent,
        "last_logins": last_logins,
    }


# ---------------------------------------------------------- corpus hygiene ---
def corpus() -> dict:
    """Duplicate + conflicting facts (active memory). A conflict = same key, two
    different active values (a contradiction to resolve)."""
    with get_conn() as conn:
        conflicts = [dict(x) for x in conn.execute(
            "SELECT lower(trim(key)) AS key, count(DISTINCT value) AS variants, "
            " array_agg(DISTINCT left(value,80)) AS values, array_agg(id) AS ids "
            "FROM memory WHERE status='active' AND key IS NOT NULL AND key<>'' "
            "GROUP BY lower(trim(key)) HAVING count(DISTINCT value) > 1 "
            "ORDER BY count(DISTINCT value) DESC LIMIT 20"
        ).fetchall()]
        dups = [dict(x) for x in conn.execute(
            "SELECT lower(trim(value)) AS value, count(*) AS n, array_agg(id) AS ids "
            "FROM memory WHERE status='active' AND value IS NOT NULL AND value<>'' "
            "GROUP BY lower(trim(value)) HAVING count(*) > 1 "
            "ORDER BY count(*) DESC LIMIT 20"
        ).fetchall()]
        tot = dict(conn.execute(
            "SELECT count(*) AS active, "
            " count(*) FILTER (WHERE status='pending') AS pending "
            "FROM memory"
        ).fetchone() or {})
    return {
        "active": tot.get("active", 0) or 0,
        "pending": tot.get("pending", 0) or 0,
        "conflicts": conflicts,
        "duplicates": dups,
        "conflict_count": len(conflicts),
        "duplicate_count": len(dups),
    }


def activity_heatmap(days: int = 30) -> dict:
    """Question volume by weekday × hour — a privacy-safe rhythm map (counts only,
    no text). Returns ECharts-ready [hour, dow, n] points (dow 0=Sun)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT extract(dow from created_at)::int AS dow, "
            "       extract(hour from created_at)::int AS hour, count(*) AS n "
            "FROM messages "
            "WHERE role='user' AND created_at > now() - (%s||' days')::interval "
            "GROUP BY 1, 2", (days,),
        ).fetchall()
    grid = [[int(r["hour"]), int(r["dow"]), int(r["n"])] for r in rows]
    return {"grid": grid, "max": max((g[2] for g in grid), default=0), "days": days}
