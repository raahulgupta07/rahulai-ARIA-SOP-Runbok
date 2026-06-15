"""Dashboard aggregations — personal (per-user, scoped to caller) and org-wide
(admin cockpit). Pure reads, no ML. Personal scope joins messages ->
conversations.user_id; admin reuses audit.coverage_report() for knowledge health,
blind spots, review queue and stale facts so the numbers stay consistent with the
Audit tab. The frontend /dashboard branches on me.role to pick personal vs org."""
import datetime

from .db import get_conn


def _friendly(name: str) -> str:
    # local import avoids an import cycle (routes imports dashboard at module load)
    from .routes import _friendly_doc
    return _friendly_doc(name) or (name or "")


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def _clamp_days(days: int) -> int:
    return days if days in (7, 30, 90, 365) else 30


# ----------------------------------------------------------------- personal ---
def personal(user_id: int, name: str | None, email: str | None, days: int = 30) -> dict:
    """Everything scoped to ONE user: their questions, chats, helpful rate,
    daily activity, the docs that answered them most, recent questions, and the
    facts they personally taught (status + how often cited)."""
    days = _clamp_days(days)
    span = f"{days - 1} days"
    win = f"{days} days"
    with get_conn() as conn:
        # KPI counters
        k = conn.execute(
            "SELECT "
            " (SELECT count(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "    WHERE c.user_id=%s AND m.role='user') AS questions, "
            " (SELECT count(*) FROM conversations WHERE user_id=%s) AS chats",
            (user_id, user_id),
        ).fetchone()
        src = conn.execute(
            "SELECT count(*) AS bot, "
            " count(*) FILTER (WHERE m.pages IS NOT NULL AND m.pages::text <> '[]') AS sourced "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE c.user_id=%s AND m.role='bot'",
            (user_id,),
        ).fetchone()
        fb = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE vote='up') AS up "
            "FROM message_feedback WHERE user_id=%s",
            (user_id,),
        ).fetchone()

        # daily activity, zero-filled over the selected window
        activity = conn.execute(
            "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(s.c,0) AS n FROM "
            f" generate_series(now()::date - interval '{span}', now()::date, interval '1 day') d "
            " LEFT JOIN (SELECT m.created_at::date dd, count(*) c "
            "   FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            f"   WHERE c.user_id=%s AND m.role='user' AND m.created_at > now() - interval '{win}' "
            "   GROUP BY dd) s ON s.dd = d::date ORDER BY d",
            (user_id,),
        ).fetchall()

        # top docs that helped this user (distinct messages a doc was cited in)
        topdocs = conn.execute(
            "SELECT p->>'doc_name' AS name, count(DISTINCT m.id) AS hits "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            " CROSS JOIN LATERAL jsonb_array_elements(m.pages) p "
            "WHERE c.user_id=%s AND m.role='bot' AND m.pages::text <> '[]' "
            " AND p->>'doc_name' IS NOT NULL "
            "GROUP BY name ORDER BY hits DESC LIMIT 6",
            (user_id,),
        ).fetchall()

        # recent questions + the paired bot reply's first cited source
        recent = conn.execute(
            "SELECT u.id, u.text AS q, u.conversation_id, u.created_at, b.pages "
            "FROM messages u JOIN conversations c ON c.id=u.conversation_id "
            "LEFT JOIN LATERAL ("
            "  SELECT text, pages FROM messages b WHERE b.conversation_id=u.conversation_id "
            "  AND b.role='bot' AND b.id > u.id ORDER BY b.id LIMIT 1) b ON true "
            "WHERE c.user_id=%s AND u.role='user' ORDER BY u.id DESC LIMIT 8",
            (user_id,),
        ).fetchall()

        taught = conn.execute(
            "SELECT id, value, status, cited_count, last_cited_at, created_at "
            "FROM memory WHERE created_by=%s ORDER BY created_at DESC LIMIT 8",
            (email,),
        ).fetchall()
        taught_counts = conn.execute(
            "SELECT count(*) AS total, "
            " count(*) FILTER (WHERE status='active') AS active, "
            " count(*) FILTER (WHERE status='pending') AS pending "
            "FROM memory WHERE created_by=%s",
            (email,),
        ).fetchone()

    for d in topdocs:
        d["label"] = _friendly(d["name"])
    rec = []
    for r in recent:
        pages = r.get("pages") or []
        source = None
        if isinstance(pages, list) and pages:
            first = pages[0] or {}
            dn = _friendly(first.get("doc_name") or "")
            pno = first.get("page_no")
            source = f"{dn} · p.{pno}" if (dn and pno is not None) else (dn or None)
        rec.append({
            "id": r["id"], "q": r["q"], "conversation_id": r["conversation_id"],
            "created_at": r["created_at"], "source": source,
        })

    return {
        "user": {"name": name, "email": email},
        "kpis": {
            "questions": k["questions"], "chats": k["chats"],
            "sourced_pct": _pct(src["sourced"], src["bot"]),
            "helpful_pct": _pct(fb["up"], fb["total"]), "votes": fb["total"],
        },
        "activity": activity,
        "top_docs": topdocs,
        "recent": rec,
        "taught": taught,
        "taught_counts": taught_counts,
        "days": days,
    }


# --------------------------------------------------------------------- org ---
def org(days: int = 30) -> dict:
    """Org-wide cockpit (admin). Volume, leaderboard, most-cited docs, feedback
    trend, live activity — PLUS knowledge health / blind spots / review queue /
    stale facts lifted from audit.coverage_report() so they match the Audit tab."""
    from . import audit as audit_mod
    days = _clamp_days(days)
    span = f"{days - 1} days"
    win = f"{days} days"
    cov = audit_mod.coverage_report(blind_limit=6, days=days)

    with get_conn() as conn:
        users = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE active) AS active "
            "FROM users WHERE role <> 'pending'"
        ).fetchone()
        pending_users = conn.execute(
            "SELECT count(*) AS n FROM users WHERE role='pending'"
        ).fetchone()
        q = conn.execute(
            "SELECT count(*) AS total, "
            " count(*) FILTER (WHERE created_at::date = now()::date) AS today, "
            " count(*) FILTER (WHERE created_at > now() - interval '7 days') AS week "
            "FROM messages WHERE role='user'"
        ).fetchone()
        fb = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE vote='up') AS up, "
            " count(*) FILTER (WHERE vote='down') AS down FROM message_feedback"
        ).fetchone()

        volume = conn.execute(
            "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(s.c,0) AS n FROM "
            f" generate_series(now()::date - interval '{span}', now()::date, interval '1 day') d "
            " LEFT JOIN (SELECT created_at::date dd, count(*) c FROM messages "
            f"   WHERE role='user' AND created_at > now() - interval '{win}' GROUP BY dd) s "
            " ON s.dd = d::date ORDER BY d"
        ).fetchall()
        fb_trend = conn.execute(
            "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(s.up,0) AS up, COALESCE(s.dn,0) AS down FROM "
            f" generate_series(now()::date - interval '{span}', now()::date, interval '1 day') d "
            " LEFT JOIN (SELECT created_at::date dd, count(*) FILTER (WHERE vote='up') up, "
            "   count(*) FILTER (WHERE vote='down') dn FROM message_feedback "
            f"   WHERE created_at > now() - interval '{win}' GROUP BY dd) s "
            " ON s.dd = d::date ORDER BY d"
        ).fetchall()

        leaders = conn.execute(
            "SELECT u.id, u.name, u.email, count(*) AS qs "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            " JOIN users u ON u.id=c.user_id "
            "WHERE m.role='user' GROUP BY u.id, u.name, u.email ORDER BY qs DESC LIMIT 8"
        ).fetchall()
        votes_by_user = conn.execute(
            "SELECT user_id, count(*) AS total, count(*) FILTER (WHERE vote='up') AS up "
            "FROM message_feedback WHERE user_id IS NOT NULL GROUP BY user_id"
        ).fetchall()
        vmap = {v["user_id"]: v for v in votes_by_user}

        topdocs = conn.execute(
            "SELECT p->>'doc_name' AS name, count(DISTINCT m.id) AS hits "
            "FROM messages m CROSS JOIN LATERAL jsonb_array_elements(m.pages) p "
            "WHERE m.role='bot' AND m.pages::text <> '[]' AND p->>'doc_name' IS NOT NULL "
            "GROUP BY name ORDER BY hits DESC LIMIT 6"
        ).fetchall()

        corrections = conn.execute(
            "SELECT count(*) AS n FROM message_feedback "
            "WHERE vote='down' AND status='new' AND correction IS NOT NULL AND correction <> ''"
        ).fetchone()
        ingest = conn.execute(
            "SELECT id, doc_id, step, msg, level, ts FROM ingest_log "
            "ORDER BY id DESC LIMIT 8"
        ).fetchall()
        # weak pages = compiled pages with almost no usable text (thin / vision-miss)
        weak = conn.execute(
            "SELECT count(*) AS n FROM doc_pages_md WHERE chars < 80"
        ).fetchone()

    for d in topdocs:
        d["label"] = _friendly(d["name"])
    leaderboard = []
    for u in leaders:
        v = vmap.get(u["id"])
        leaderboard.append({
            "id": u["id"], "name": u["name"] or u["email"], "email": u["email"],
            "questions": u["qs"],
            "helpful_pct": _pct(v["up"], v["total"]) if v else None,
        })

    st = cov.get("stats", {})
    review = {
        "pending_facts": st.get("facts_pending", 0),
        "corrections": corrections["n"],
        "blind_questions": st.get("blind_questions", 0),
        "pending_users": pending_users["n"],
    }
    health = {
        "docs": st.get("docs", 0), "pages": st.get("pages", 0),
        "facts_active": st.get("facts_active", 0),
        "facts_pending": st.get("facts_pending", 0),
        "weak_pages": weak["n"],
        "coverage_pct": st.get("coverage_pct", 0),
        "answers_no_source": st.get("answers_no_source", 0),
        "answers": st.get("answers", 0),
    }

    return {
        "kpis": {
            "users_active": users["active"], "users_total": users["total"],
            "questions_total": q["total"], "questions_today": q["today"],
            "questions_week": q["week"],
            "helpful_pct": _pct(fb["up"], fb["total"]),
            "coverage_score": cov.get("score", 0),
        },
        "volume": volume,
        "fb_trend": fb_trend,
        "feedback": {"up": fb["up"], "down": fb["down"], "total": fb["total"]},
        "leaderboard": leaderboard,
        "top_docs": topdocs,
        "review": review,
        "health": health,
        "blind_spots": cov.get("blind_spots", []),
        "stale_facts": cov.get("stale_facts", []),
        "downvotes_recent": cov.get("downvotes_recent", []),
        "pillars": cov.get("pillars", {}),
        "ingest": ingest,
        "days": days,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ graphs ---
def graph_me(user_id: int, name: str | None, email: str | None) -> dict:
    """Ego knowledge map for ONE user: a central YOU node → the docs that
    answered them (sized by citation count) + the facts they taught; doc↔doc
    edges where two docs were cited in the SAME answer. All from the caller's own
    chats (conversations.user_id) — nothing about other users leaks."""
    nodes: list[dict] = []
    links: list[dict] = []
    me_id = "me"
    nodes.append({"id": me_id, "type": "me", "label": (name or email or "You"), "val": 30})

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.id AS msg_id, pg.doc_id, d.name AS doc_name "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            " JOIN jsonb_array_elements(m.pages) e ON true "
            " JOIN pages pg ON pg.id=(e->>'page_id')::int "
            " JOIN docs d ON d.id=pg.doc_id "
            "WHERE c.user_id=%s AND m.role='bot' AND m.pages::text <> '[]'",
            (user_id,),
        ).fetchall()
        facts = conn.execute(
            "SELECT id, value FROM memory WHERE created_by=%s ORDER BY created_at DESC LIMIT 30",
            (email,),
        ).fetchall()

    # aggregate: per-message distinct docs -> usage counts + co-citation pairs
    doc_name: dict[int, str] = {}
    doc_used: dict[int, int] = {}
    by_msg: dict[int, set] = {}
    for r in rows:
        did = r["doc_id"]
        doc_name[did] = r["doc_name"]
        by_msg.setdefault(r["msg_id"], set()).add(did)
    cocite: dict[tuple, int] = {}
    for dids in by_msg.values():
        for did in dids:
            doc_used[did] = doc_used.get(did, 0) + 1
        ordered = sorted(dids)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                cocite[(ordered[i], ordered[j])] = cocite.get((ordered[i], ordered[j]), 0) + 1

    for did, used in doc_used.items():
        nodes.append({"id": f"d{did}", "type": "doc", "label": _friendly(doc_name[did]),
                      "val": 8 + used * 2, "doc_id": did, "used": used})
        links.append({"source": me_id, "target": f"d{did}", "value": used})
    for (a, b), w in cocite.items():
        links.append({"source": f"d{a}", "target": f"d{b}", "value": w, "kind": "cocite"})
    for f in facts:
        nodes.append({"id": f"f{f['id']}", "type": "fact", "label": (f["value"] or "")[:48],
                      "val": 10, "fact_id": f["id"]})
        links.append({"source": me_id, "target": f"f{f['id']}", "value": 1, "kind": "taught"})

    return {"nodes": nodes, "links": links,
            "counts": {"docs": len(doc_used), "facts": len(facts), "answers": len(by_msg)}}


# ----------------------------------------------------------------- cockpit ---
def cockpit(days: int = 30) -> dict:
    """Command-center rollup for the Overview page.  Every sub-source is wrapped
    in its own try/except so one failing module never breaks the whole response.

    Contract:
    {
      "days": int,
      "kpis":  {users_active, users_total, questions, questions_today, cost_total, hours_saved},
      "rings": {coverage, trust, helpful, ingest_health},
      "alerts": [{key, label, count, severity, href}],
      "trends": {volume: [{label, n}], cost: [{label, v}]}
    }
    """
    days = _clamp_days(days)

    # ---- 1. management scorecard (adoption + quality + value) ----
    mgmt: dict = {}
    try:
        from . import analytics as analytics_mod
        mgmt = analytics_mod.management(days)
    except Exception:
        pass

    # ---- 2. audit coverage report ----
    cov: dict = {}
    try:
        from . import audit as audit_mod
        cov = audit_mod.coverage_report(blind_limit=12, days=days)
    except Exception:
        pass

    # ---- 3. ops / system health ----
    ops_data: dict = {}
    try:
        from . import ops as ops_mod
        ops_data = ops_mod.report()
    except Exception:
        pass

    # ---- 4. security ----
    sec_kpis: dict = {}
    try:
        from . import analytics as analytics_mod
        sec_kpis = analytics_mod.security(days).get("kpis", {})
    except Exception:
        pass

    # ---- 5. citation-trust verify ----
    ver_kpis: dict = {}
    try:
        from . import analytics as analytics_mod
        ver_kpis = analytics_mod.verify_report(days).get("kpis", {})
    except Exception:
        pass

    # ---- 6. perf trend (for cost series) ----
    perf_trend: list = []
    try:
        from . import analytics as analytics_mod
        perf_trend = analytics_mod.perf(days).get("trend", [])
    except Exception:
        pass

    # ---- 7. pending facts count ----
    pending_facts_n: int = 0
    try:
        from . import memory as memory_mod
        pending_facts_n = memory_mod.pending_count()
    except Exception:
        pass

    # ---- 8. daily question volume (reuse org() series if already available;
    #         fall back to a direct query so cockpit stays standalone) ----
    volume_series: list = []
    try:
        span = f"{days - 1} days"
        win = f"{days} days"
        with get_conn() as conn:
            volume_series = conn.execute(
                "SELECT to_char(d::date,'MM-DD') AS label, COALESCE(s.c,0) AS n FROM "
                f" generate_series(now()::date - interval '{span}', now()::date, interval '1 day') d "
                " LEFT JOIN (SELECT created_at::date dd, count(*) c FROM messages "
                f"   WHERE role='user' AND created_at > now() - interval '{win}' GROUP BY dd) s "
                " ON s.dd = d::date ORDER BY d"
            ).fetchall()
    except Exception:
        volume_series = []

    # ----------------------------------------------------------------- kpis --
    adoption = mgmt.get("adoption", {})
    quality  = mgmt.get("quality", {})
    value    = mgmt.get("value", {})
    engagement = mgmt.get("engagement", {})

    questions_total: int = 0
    questions_today: int = 0
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT count(*) AS total, "
                " count(*) FILTER (WHERE created_at::date = now()::date) AS today "
                "FROM messages WHERE role='user'"
            ).fetchone()
            questions_total = int(row["total"] or 0)
            questions_today = int(row["today"] or 0)
    except Exception:
        # fall back to what management() returned if available
        questions_total = int(engagement.get("questions", 0) or 0)

    kpis = {
        "users_active": int(adoption.get("active_window") or adoption.get("dau") or 0),
        "users_total":  int(adoption.get("registered", 0) or 0),
        "questions":    questions_total,
        "questions_today": questions_today,
        "cost_total":   float(value.get("llm_cost", 0) or 0),
        "hours_saved":  float(value.get("hours_saved", 0) or 0),
    }

    # ----------------------------------------------------------------- rings --
    ingest_data = ops_data.get("ingest", {})
    fail_rate   = int(ingest_data.get("fail_rate", 0) or 0)

    scored = int(ver_kpis.get("scored", 0) or 0)
    avg_trust = int(ver_kpis.get("avg_score", 0) or 0) if scored > 0 else None

    rings = {
        "coverage":     int(cov.get("score", 0) or 0),
        "trust":        avg_trust,
        "helpful":      int(quality.get("helpful_pct", 0) or 0),
        "ingest_health": max(0, 100 - fail_rate),
    }

    # ---------------------------------------------------------------- alerts --
    alerts: list[dict] = []

    def _alert(key: str, label: str, count: int, severity: str, href: str) -> None:
        if count > 0:
            alerts.append({"key": key, "label": label, "count": count,
                           "severity": severity, "href": href})

    # pending facts
    _alert("pending_facts", f"{pending_facts_n} pending fact{'s' if pending_facts_n != 1 else ''}",
           pending_facts_n, "warn", "/dashboard/review")

    # blind spots
    blind_spots_list = cov.get("blind_spots", []) or []
    bsn = len(blind_spots_list)
    if bsn > 0:
        _alert("blind_spots", f"{bsn} blind spot{'s' if bsn != 1 else ''}",
               bsn, "alert" if bsn >= 5 else "warn", "/dashboard/knowledge")

    # downvoted answers
    dvn = len(cov.get("downvotes_recent", []) or [])
    _alert("downvotes", f"{dvn} downvote{'s' if dvn != 1 else ''}",
           dvn, "warn", "/dashboard/review")

    # stuck ingest docs
    stuck_n = len(ingest_data.get("stuck", []) or [])
    _alert("stuck_ingest", f"{stuck_n} stuck ingest{'s' if stuck_n != 1 else ''}",
           stuck_n, "alert", "/dashboard/system")

    # stale daemons
    stale_daemons = sum(1 for d in (ops_data.get("daemons", []) or [])
                        if d.get("stale") is True)
    _alert("stale_daemon", f"{stale_daemons} stale daemon{'s' if stale_daemons != 1 else ''}",
           stale_daemons, "alert", "/dashboard/system")

    # failed logins
    fail_logins = int(sec_kpis.get("logins_fail", 0) or 0)
    if fail_logins >= 3:
        _alert("failed_logins", f"{fail_logins} failed login{'s' if fail_logins != 1 else ''}",
               fail_logins, "warn", "/dashboard/system")

    # low trust (weak answers)
    weak_n = int(ver_kpis.get("weak", 0) or 0)
    _alert("low_trust", f"{weak_n} low-trust answer{'s' if weak_n != 1 else ''}",
           weak_n, "warn", "/dashboard/performance")

    # --------------------------------------------------------------- trends --
    cost_trend = [{"label": r["label"], "v": float(r.get("cost", 0) or 0)}
                  for r in perf_trend]

    trends = {
        "volume": [{"label": r["label"], "n": int(r["n"] or 0)} for r in volume_series],
        "cost":   cost_trend,
    }

    return {
        "days":   days,
        "kpis":   kpis,
        "rings":  rings,
        "alerts": alerts,
        "trends": trends,
    }


def graph_people() -> dict:
    """Org 'who-knows-what' bipartite: user nodes ↔ doc nodes, edge weight = how
    many times that user's answers cited that doc. Surfaces who is active on
    which runbook (coverage / expertise by person)."""
    nodes: list[dict] = []
    links: list[dict] = []
    seen_u: set = set()
    seen_d: set = set()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id AS uid, u.name, u.email, pg.doc_id, d.name AS doc_name, count(*) AS cnt "
            "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            " JOIN users u ON u.id=c.user_id "
            " JOIN jsonb_array_elements(m.pages) e ON true "
            " JOIN pages pg ON pg.id=(e->>'page_id')::int "
            " JOIN docs d ON d.id=pg.doc_id "
            "WHERE m.role='bot' AND m.pages::text <> '[]' "
            "GROUP BY u.id, u.name, u.email, pg.doc_id, d.name"
        ).fetchall()
    for r in rows:
        uid = f"u{r['uid']}"
        did = f"d{r['doc_id']}"
        if uid not in seen_u:
            seen_u.add(uid)
            nodes.append({"id": uid, "type": "user", "label": r["name"] or r["email"], "val": 22})
        if did not in seen_d:
            seen_d.add(did)
            nodes.append({"id": did, "type": "doc", "label": _friendly(r["doc_name"]),
                          "val": 10, "doc_id": r["doc_id"]})
        links.append({"source": uid, "target": did, "value": r["cnt"]})
    # bump user node size by total citations (engagement)
    tot: dict[str, int] = {}
    for l in links:
        tot[l["source"]] = tot.get(l["source"], 0) + l["value"]
    for n in nodes:
        if n["type"] == "user":
            n["val"] = 14 + min(40, tot.get(n["id"], 0))
    return {"nodes": nodes, "links": links,
            "counts": {"users": len(seen_u), "docs": len(seen_d)}}


def vitals() -> dict:
    """Live system vitals for the Mission Control status bar + vitals card:
    DB size, ingest queue/stuck, background-daemon liveness, plus right-now
    activity (active users in the last 5 min, today's questions + cost)."""
    from . import ops
    rep = ops.report()
    with get_conn() as conn:
        active = conn.execute(
            "SELECT count(DISTINCT c.user_id) AS n FROM conversations c "
            "JOIN messages m ON m.conversation_id = c.id "
            "WHERE m.created_at > now() - interval '5 minutes'").fetchone()
        today_q = conn.execute(
            "SELECT count(*) AS q FROM messages "
            "WHERE role='user' AND created_at::date = current_date").fetchone()
        today_c = conn.execute(
            "SELECT coalesce(sum(cost_usd),0) AS c FROM answer_metrics "
            "WHERE created_at::date = current_date").fetchone()
    ing = rep.get("ingest", {})
    return {
        "db_bytes": rep.get("storage", {}).get("db_bytes", 0),
        "tables": rep.get("storage", {}).get("tables", [])[:6],
        "ingest_queue": ing.get("queue_depth", 0),
        "ingest_stuck": len(ing.get("stuck", [])),
        "ingest_failed": len(ing.get("failed", [])),
        # weekly digest runs every 7d — an hourly staleness check always flags it,
        # so don't treat its age as a liveness problem in Mission Control
        "daemons": [{"name": d["name"], "enabled": d["enabled"],
                     "stale": False if d["name"] == "weekly digest" else d["stale"]}
                    for d in rep.get("daemons", [])],
        "active_now": int((active or {}).get("n") or 0),
        "questions_today": int((today_q or {}).get("q") or 0),
        "cost_today": float((today_c or {}).get("c") or 0),
    }
