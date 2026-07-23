"""Productivity / value scorecard — one aggregate the management dashboard reads.

Pure reads over the usage_daily rollup + answer_metrics + messages + users. No
LLM, no writes. Every sub-metric is wrapped so a missing table/column degrades
to a safe default instead of breaking the page — overview() NEVER raises.

Heuristics (labelled, not ground truth):
  - resolved     : an ANSWERED (sourced) question is counted resolved unless it
                   was downvoted → resolved_rate = (sourced - down)/max(1,sourced).
  - deflection   : share of ALL questions that ended fully answered =
                   resolved_rate × sourced/questions.
  - tickets      : tickets_avoided = int(questions × deflection × DEFLECT_TICKET_SHARE)
                   — assumes ~1 in 10 answered questions would else become a ticket.
  - hours_saved  : answered × minutes_saved / 60 (minutes_saved from the ROI knob).
"""
import datetime

from . import appcfg
from .db import get_conn

# knob: fraction of answered questions that would otherwise have become a ticket
DEFLECT_TICKET_SHARE = 0.1
# default value of an hour of staff time when the ROI knob isn't configured yet
DEFAULT_HOURLY_RATE = 4.0


def _pct(part, whole):
    return round(100 * part / whole) if whole else 0


def _delta_pct(cur, prev):
    """Integer percent change cur-vs-prev, None-safe → 0 when prev is 0/None."""
    if not prev:
        return 0
    return round(100 * ((cur or 0) - prev) / prev)


def _clamp01(x):
    return max(0.0, min(1.0, x))


def _window(days, date_from, date_to):
    """(start, end, prev_start, prev_end, n_days). Explicit dates win over days."""
    today = datetime.date.today()
    s = e = None
    if date_from and date_to:
        try:
            s = datetime.date.fromisoformat(date_from)
            e = datetime.date.fromisoformat(date_to)
        except Exception:
            s = e = None
    if s is None or e is None:
        e = today
        s = e - datetime.timedelta(days=max(1, int(days)) - 1)
    if e < s:
        s, e = e, s
    n = (e - s).days + 1
    pe = s - datetime.timedelta(days=1)
    ps = pe - datetime.timedelta(days=n - 1)
    return s, e, ps, pe, n


def _cfg():
    try:
        cfg = appcfg.get_config()
    except Exception:
        cfg = {}
    minutes = float(cfg.get("minutes_saved_per_answer") or 6)
    rate = float(cfg.get("hourly_rate_usd") or DEFAULT_HOURLY_RATE)
    return minutes, rate


def _has_department(conn) -> bool:
    # autocommit → a failed probe leaves the connection usable
    try:
        conn.execute("SELECT department FROM users LIMIT 1")
        return True
    except Exception:
        return False


# ---- period-level metrics (used for both the current + prior window) ----------
def _usage_sums(conn, s, e):
    return conn.execute(
        "SELECT COALESCE(sum(questions),0) AS questions, COALESCE(sum(convos),0) AS convos, "
        " COALESCE(sum(sourced),0) AS sourced, COALESCE(sum(blind),0) AS blind, "
        " COALESCE(sum(up),0) AS up, COALESCE(sum(down),0) AS down, "
        " count(DISTINCT user_id) FILTER (WHERE questions>0) AS active "
        "FROM usage_daily WHERE day BETWEEN %s AND %s",
        (s, e),
    ).fetchone()


def _refused(conn, s, e):
    """(refused_bot_msgs, total_bot_msgs) in window. Refusal = the scope-gate line."""
    row = conn.execute(
        "SELECT count(*) AS total, "
        " count(*) FILTER (WHERE text ILIKE %s) AS refused "
        "FROM messages WHERE role='bot' AND created_at::date BETWEEN %s AND %s",
        ("That's outside the runbooks%", s, e),
    ).fetchone()
    return int(row["refused"] or 0), int(row["total"] or 0)


def _avg_answer_s(conn, s, e):
    row = conn.execute(
        "SELECT COALESCE(avg(ms_total),0)/1000.0 AS s FROM answer_metrics "
        "WHERE created_at::date BETWEEN %s AND %s",
        (s, e),
    ).fetchone()
    return round(float(row["s"] or 0), 1)


def _period(conn, s, e, minutes):
    """Everything needed to build totals + prev for one window. Sub-metrics fail-soft."""
    u = _usage_sums(conn, s, e)
    sourced = int(u["sourced"] or 0)
    blind = int(u["blind"] or 0)
    down = int(u["down"] or 0)
    questions = int(u["questions"] or 0)
    answered = sourced + blind
    hours = round(answered * minutes / 60, 1)
    resolved_rate = _clamp01((sourced - down) / max(1, sourced))
    try:
        avg_s = _avg_answer_s(conn, s, e)
    except Exception:
        avg_s = 0.0
    try:
        refused_n, bot_total = _refused(conn, s, e)
        refused_rate = round(refused_n / bot_total, 4) if bot_total else 0.0
    except Exception:
        refused_rate = 0.0
    return {
        "questions": questions, "convos": int(u["convos"] or 0),
        "sourced": sourced, "blind": blind, "down": down, "up": int(u["up"] or 0),
        "answered": answered, "hours_saved": hours,
        "resolved_rate": round(resolved_rate, 4), "avg_answer_s": avg_s,
        "refused_rate": refused_rate, "active": int(u["active"] or 0),
    }


# ---- sections -----------------------------------------------------------------
def _totals(conn, s, e, ps, pe, minutes, rate):
    cur = _period(conn, s, e, minutes)
    prev = _period(conn, ps, pe, minutes)

    # ai_cost from real token spend (fail-soft 0)
    try:
        ai_cost = float(conn.execute(
            "SELECT COALESCE(sum(cost_usd),0) AS c FROM answer_metrics "
            "WHERE created_at::date BETWEEN %s AND %s", (s, e),
        ).fetchone()["c"] or 0)
    except Exception:
        ai_cost = 0.0

    # registered = real members only (no widget/embed/pending)
    try:
        registered = int(conn.execute(
            "SELECT count(*) AS n FROM users "
            "WHERE COALESCE(role,'')<>'widget' AND COALESCE(role,'')<>'pending' "
            "  AND COALESCE(auth_source,'')<>'embed'"
        ).fetchone()["n"] or 0)
    except Exception:
        registered = 0

    labour_value = round(cur["hours_saved"] * rate, 2)
    roi = round(labour_value / ai_cost, 2) if ai_cost > 0 else None
    q = cur["questions"]
    deflection_rate = round(cur["resolved_rate"] * (cur["sourced"] / q), 4) if q else 0.0
    tickets_avoided = int(q * deflection_rate * DEFLECT_TICKET_SHARE)

    lang = {"en": 0, "my": 0, "mixed": 0}
    try:
        lang = _lang_split(conn, s, e)
    except Exception:
        pass

    return {
        "questions": q, "answered": cur["answered"],
        "hours_saved": cur["hours_saved"], "labour_value": labour_value,
        "ai_cost": round(ai_cost, 4), "roi": roi,
        "resolved_rate": cur["resolved_rate"],
        "deflection_rate": deflection_rate, "tickets_avoided": tickets_avoided,
        "active_users": cur["active"], "registered_users": registered,
        "avg_answer_s": cur["avg_answer_s"], "refused_rate": cur["refused_rate"],
        "lang": lang,
        "prev": {
            "questions": prev["questions"], "hours_saved": prev["hours_saved"],
            "resolved_rate": prev["resolved_rate"], "avg_answer_s": prev["avg_answer_s"],
            "refused_rate": prev["refused_rate"],
        },
    }


def _lang_split(conn, s, e):
    rows = conn.execute(
        "SELECT text FROM messages WHERE role='user' AND text IS NOT NULL "
        "  AND created_at::date BETWEEN %s AND %s LIMIT 2000",
        (s, e),
    ).fetchall()
    c = {"en": 0, "my": 0, "mixed": 0}
    for r in rows:
        t = r["text"] or ""
        has_my = any("က" <= ch <= "႟" for ch in t)
        has_ascii = any("a" <= ch.lower() <= "z" for ch in t)
        c["mixed" if (has_my and has_ascii) else "my" if has_my else "en"] += 1
    tot = sum(c.values())
    if not tot:
        return c
    return {k: round(100 * v / tot) for k, v in c.items()}


def _trend(conn, s, e, minutes):
    """One row per day in window: questions, hours, by_domain (top-5 + other)."""
    rows = conn.execute(
        "SELECT m.created_at::date AS day, "
        "  lower(split_part(u.email,'@',2)) AS domain, count(*) AS n "
        "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
        "JOIN users u ON u.id=c.user_id "
        "WHERE m.role='user' AND m.created_at::date BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed' "
        "GROUP BY 1,2",
        (s, e),
    ).fetchall()
    # rank domains by total volume → top 5 kept, rest folded to 'other'
    vol: dict[str, int] = {}
    for r in rows:
        vol[r["domain"] or "unknown"] = vol.get(r["domain"] or "unknown", 0) + int(r["n"])
    top = {d for d, _ in sorted(vol.items(), key=lambda kv: -kv[1])[:5]}

    by_day: dict[datetime.date, dict] = {}
    for r in rows:
        d = r["day"]
        dom = r["domain"] or "unknown"
        dom = dom if dom in top else "other"
        slot = by_day.setdefault(d, {"questions": 0, "by_domain": {}})
        slot["questions"] += int(r["n"])
        slot["by_domain"][dom] = slot["by_domain"].get(dom, 0) + int(r["n"])

    out = []
    day = s
    while day <= e:
        slot = by_day.get(day, {"questions": 0, "by_domain": {}})
        by_domain_hours = {k: round(v * minutes / 60, 2) for k, v in slot["by_domain"].items()}
        out.append({
            "day": day.isoformat(),
            "questions": slot["questions"],
            "hours": round(slot["questions"] * minutes / 60, 2),
            "by_domain": by_domain_hours,
        })
        day += datetime.timedelta(days=1)
    return out


def _hourly(conn, s, e):
    rows = conn.execute(
        "SELECT extract(hour from created_at)::int AS h, count(*) AS n "
        "FROM messages WHERE role='user' AND created_at::date BETWEEN %s AND %s "
        "GROUP BY 1", (s, e),
    ).fetchall()
    out = [0] * 24
    for r in rows:
        h = int(r["h"])
        if 0 <= h <= 23:
            out[h] = int(r["n"])
    return out


def _grouped(conn, s, e, ps, pe, minutes, group_expr, extra_cols=""):
    """Shared aggregate for domains/departments: users/questions/hours + delta_pct."""
    cur = conn.execute(
        f"SELECT {group_expr} AS grp, count(DISTINCT ud.user_id) AS users, "
        f"  COALESCE(sum(ud.questions),0) AS questions, "
        f"  COALESCE(sum(ud.sourced+ud.blind),0) AS answered{extra_cols} "
        "FROM usage_daily ud JOIN users u ON u.id=ud.user_id "
        "WHERE ud.day BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed' "
        "GROUP BY 1 ORDER BY questions DESC",
        (s, e),
    ).fetchall()
    prev = conn.execute(
        f"SELECT {group_expr} AS grp, COALESCE(sum(ud.questions),0) AS questions "
        "FROM usage_daily ud JOIN users u ON u.id=ud.user_id "
        "WHERE ud.day BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed' "
        "GROUP BY 1",
        (ps, pe),
    ).fetchall()
    prevq = {r["grp"]: int(r["questions"]) for r in prev}
    return cur, prevq


def _domains(conn, s, e, ps, pe, minutes):
    cur, prevq = _grouped(conn, s, e, ps, pe, minutes,
                          "lower(split_part(u.email,'@',2))")
    out = []
    for r in cur:
        answered = int(r["answered"] or 0)
        out.append({
            "domain": r["grp"] or "unknown",
            "users": int(r["users"] or 0),
            "questions": int(r["questions"] or 0),
            "hours": round(answered * minutes / 60, 1),
            "delta_pct": _delta_pct(int(r["questions"] or 0), prevq.get(r["grp"], 0)),
        })
    return out


def _departments(conn, s, e, ps, pe, minutes):
    if not _has_department(conn):
        return []
    grp = "COALESCE(NULLIF(u.department,''),'Unassigned')"
    cur, prevq = _grouped(conn, s, e, ps, pe, minutes, grp,
                          extra_cols=", COALESCE(sum(ud.sourced),0) AS sourced, "
                                     "COALESCE(sum(ud.down),0) AS down")
    out = []
    for r in cur:
        answered = int(r["answered"] or 0)
        sourced = int(r["sourced"] or 0)
        down = int(r["down"] or 0)
        resolved = _clamp01((sourced - down) / max(1, sourced))
        out.append({
            "dept": r["grp"] or "Unassigned",
            "users": int(r["users"] or 0),
            "questions": int(r["questions"] or 0),
            "hours": round(answered * minutes / 60, 1),
            "resolved_rate": round(resolved, 4),
            "delta_pct": _delta_pct(int(r["questions"] or 0), prevq.get(r["grp"], 0)),
        })
    return out


def _funnel(conn, s, e, registered):
    asked = int(conn.execute(
        "SELECT count(DISTINCT user_id) AS n FROM usage_daily "
        "WHERE questions>0 AND day BETWEEN %s AND %s", (s, e),
    ).fetchone()["n"] or 0)
    week_cut = e - datetime.timedelta(days=6)
    weekly = int(conn.execute(
        "SELECT count(DISTINCT user_id) AS n FROM usage_daily "
        "WHERE questions>0 AND day BETWEEN %s AND %s", (week_cut, e),
    ).fetchone()["n"] or 0)
    # power = users in top decile by window questions (min 1)
    counts = [int(r["q"]) for r in conn.execute(
        "SELECT sum(questions) AS q FROM usage_daily WHERE day BETWEEN %s AND %s "
        "GROUP BY user_id HAVING sum(questions)>0 ORDER BY q DESC", (s, e),
    ).fetchall()]
    power = 0
    if counts:
        cut = counts[int(len(counts) * 0.1)]
        power = max(1, sum(1 for q in counts if q >= cut))
    dormant = max(0, registered - asked)

    def _retention(first_end, last_start, last_end):
        try:
            row = conn.execute(
                "WITH early AS (SELECT DISTINCT user_id FROM usage_daily "
                "  WHERE questions>0 AND day BETWEEN %s AND %s), "
                "late AS (SELECT DISTINCT user_id FROM usage_daily "
                "  WHERE questions>0 AND day BETWEEN %s AND %s) "
                "SELECT (SELECT count(*) FROM early) AS base, "
                " (SELECT count(*) FROM early e JOIN late l ON l.user_id=e.user_id) AS kept",
                (s, first_end, last_start, last_end),
            ).fetchone()
            return _pct(int(row["kept"] or 0), int(row["base"] or 0))
        except Exception:
            return 0

    d7 = _retention(s + datetime.timedelta(days=6), e - datetime.timedelta(days=6), e)
    d30_first = s + datetime.timedelta(days=14)
    d30 = _retention(d30_first, e - datetime.timedelta(days=14), e)
    return {"registered": registered, "asked": asked, "weekly": weekly,
            "power": power, "dormant": dormant, "d7": d7, "d30": d30}


def _gaps(conn, s, e, n):
    try:
        from . import audit
        spots = audit.coverage_report(days=n).get("blind_spots", []) or []
    except Exception:
        return []
    out = []
    for sp in spots[:8]:
        out.append({"topic": sp.get("question", ""), "count": int(sp.get("count", 0) or 0)})
    return out


def _people(conn, s, e, minutes, rate, has_dept):
    """One row per real member (NO cap — the Excel export needs everyone). Adds
    created_at, last_active (all-time), value_usd, per-user token spend (joined
    via answer_metrics.user_id), login methods, and a 'new' flag."""
    dept_sel = "COALESCE(NULLIF(u.department,''),'Unassigned')" if has_dept else "NULL"
    rows = conn.execute(
        "WITH agg AS (SELECT user_id, sum(questions) q, sum(sourced) sourced, "
        "  sum(blind) blind, sum(up) up, sum(down) down, "
        "  count(*) FILTER (WHERE questions>0) active_days "
        "  FROM usage_daily WHERE day BETWEEN %s AND %s GROUP BY user_id), "
        "la AS (SELECT user_id, max(day) d FROM usage_daily WHERE questions>0 GROUP BY user_id), "
        "tok AS (SELECT user_id, sum(COALESCE(tok_in,0)+COALESCE(tok_out,0)) t "
        "  FROM answer_metrics WHERE created_at::date BETWEEN %s AND %s "
        "  AND user_id IS NOT NULL GROUP BY user_id) "
        f"SELECT u.id, u.name, u.email, {dept_sel} AS dept, "
        "  u.created_at, u.auth_methods, u.auth_source, "
        "  COALESCE(a.q,0) q, COALESCE(a.sourced,0) sourced, COALESCE(a.blind,0) blind, "
        "  COALESCE(a.up,0) up, COALESCE(a.down,0) down, COALESCE(a.active_days,0) active_days, "
        "  la.d AS last_active, COALESCE(tok.t,0) tokens "
        "FROM users u LEFT JOIN agg a ON a.user_id=u.id "
        "  LEFT JOIN la ON la.user_id=u.id LEFT JOIN tok ON tok.user_id=u.id "
        "WHERE COALESCE(u.role,'')<>'widget' AND COALESCE(u.role,'')<>'pending' "
        "  AND COALESCE(u.auth_source,'')<>'embed' "
        "ORDER BY q DESC NULLS LAST, u.id",
        (s, e, s, e),
    ).fetchall()
    qs = sorted((int(r["q"]) for r in rows if int(r["q"]) > 0), reverse=True)
    power_cut = qs[int(len(qs) * 0.1)] if qs else None
    out = []
    for r in rows:
        q = int(r["q"] or 0)
        sourced = int(r["sourced"] or 0)
        down = int(r["down"] or 0)
        up = int(r["up"] or 0)
        answered = sourced + int(r["blind"] or 0)
        hours = round(answered * minutes / 60, 1)
        resolved = _clamp01((sourced - down) / max(1, sourced)) if sourced else 0.0
        created = r["created_at"]
        new_in_window = bool(created and s <= created.date() <= e)
        if new_in_window and q < 5:
            flag = "new"
        elif q == 0:
            flag = "dormant"
        elif power_cut is not None and q >= power_cut:
            flag = "power"
        else:
            flag = "active"
        methods = r["auth_methods"] or ([r["auth_source"]] if r["auth_source"] else [])
        out.append({
            "id": r["id"], "name": r["name"] or r["email"], "email": r["email"],
            "dept": r["dept"], "questions": q, "active_days": int(r["active_days"] or 0),
            "hours": hours,
            "helpful": _pct(up, up + down) if (up + down) else None,
            "resolved": round(resolved, 4),
            "created_at": created.date().isoformat() if created else None,
            "last_active": r["last_active"].isoformat() if r["last_active"] else None,
            "value_usd": round(hours * rate, 2),
            "tokens": int(r["tokens"] or 0),
            "methods": list(methods),
            "flag": flag,
        })
    return out


# ---- platform + adoption (management-dashboard extras) ------------------------
def _platform(conn, s, e):
    """Whole-platform vitals for the window: token spend, model mix, cache serves,
    new signups, conversation shape, and KB size. Each read fails soft to a safe
    default via the caller's per-section try/except."""
    # token spend (answer_metrics tok_in/tok_out are the real column names)
    tk = conn.execute(
        "SELECT COALESCE(sum(tok_in),0) AS tin, COALESCE(sum(tok_out),0) AS tout, "
        "  count(*) AS n FROM answer_metrics WHERE created_at::date BETWEEN %s AND %s",
        (s, e),
    ).fetchone()
    tin = int(tk["tin"] or 0)
    tout = int(tk["tout"] or 0)
    n = int(tk["n"] or 0)
    total = tin + tout
    tokens = {"total": total, "tin": tin, "tout": tout,
              "per_answer": round(total / n) if n else 0}

    # answers by model — the 'qa-bank' pseudo-model = zero-LLM cache serves
    by_model = []
    for r in conn.execute(
        "SELECT COALESCE(model,'?') AS model, count(*) AS answers FROM answer_metrics "
        "WHERE created_at::date BETWEEN %s AND %s GROUP BY 1 ORDER BY 2 DESC",
        (s, e),
    ).fetchall():
        m = r["model"] or "?"
        by_model.append({
            "model": "cache" if m == "qa-bank" else m,
            "answers": int(r["answers"] or 0),
            "share_pct": _pct(int(r["answers"] or 0), n),
        })

    cr = conn.execute(
        "SELECT count(*) FILTER (WHERE cache_hit) AS serves, "
        "  COALESCE(avg(ms_total) FILTER (WHERE cache_hit),0)/1000.0 AS avg_s "
        "FROM answer_metrics WHERE created_at::date BETWEEN %s AND %s",
        (s, e),
    ).fetchone()
    serves = int(cr["serves"] or 0)
    cache = {"serves": serves, "hit_rate": round(serves / n, 4) if n else 0.0,
             "avg_s": round(float(cr["avg_s"] or 0), 1)}

    # new signups in window (real members only) + newest member overall
    nu = int(conn.execute(
        "SELECT count(*) AS n FROM users WHERE created_at::date BETWEEN %s AND %s "
        "  AND COALESCE(role,'')<>'widget' AND COALESCE(auth_source,'')<>'embed'",
        (s, e),
    ).fetchone()["n"] or 0)
    last = conn.execute(
        "SELECT email, name, created_at, auth_methods, auth_source FROM users "
        "WHERE COALESCE(role,'')<>'widget' AND COALESCE(auth_source,'')<>'embed' "
        "ORDER BY created_at DESC NULLS LAST, id DESC LIMIT 1"
    ).fetchone()
    last_u = None
    if last:
        methods = last["auth_methods"] or []
        last_u = {
            "email": last["email"], "name": last["name"] or last["email"],
            "created_at": last["created_at"].date().isoformat() if last["created_at"] else None,
            "method": methods[-1] if methods else (last["auth_source"] or "local"),
        }
    new_users = {"count": nu, "last": last_u}

    # conversations started in window; turns = bot messages per conversation
    cv = conn.execute(
        "WITH conv AS (SELECT c.id, count(*) FILTER (WHERE m.role='bot') AS turns "
        "  FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id "
        "  WHERE c.created_at::date BETWEEN %s AND %s GROUP BY c.id) "
        "SELECT count(*) AS n, COALESCE(avg(turns),0) AS avg_turns, "
        "  count(*) FILTER (WHERE turns<=1) AS one_turn, COALESCE(max(turns),0) AS max_turns "
        "FROM conv",
        (s, e),
    ).fetchone()
    cn = int(cv["n"] or 0)
    conversations = {
        "count": cn, "avg_turns": round(float(cv["avg_turns"] or 0), 1),
        "one_turn_pct": _pct(int(cv["one_turn"] or 0), cn),
        "max_turns": int(cv["max_turns"] or 0),
    }

    # knowledge base size (whole corpus, not window-scoped)
    ready = "status IN ('ready','ready_lite')"
    docs = int(conn.execute(f"SELECT count(*) AS n FROM docs WHERE {ready}").fetchone()["n"] or 0)
    pages = int(conn.execute(
        f"SELECT count(*) AS n FROM pages p JOIN docs d ON d.id=p.doc_id WHERE d.{ready}"
    ).fetchone()["n"] or 0)
    qa_active = int(conn.execute(
        "SELECT count(*) AS n FROM qa_pairs WHERE status='active'"
    ).fetchone()["n"] or 0)
    ld_row = conn.execute(
        f"SELECT name, created_at FROM docs WHERE {ready} "
        "ORDER BY created_at DESC NULLS LAST LIMIT 1"
    ).fetchone()
    last_doc = None
    if ld_row:
        last_doc = {"name": ld_row["name"],
                    "created_at": ld_row["created_at"].date().isoformat() if ld_row["created_at"] else None}
    kb = {"docs": docs, "pages": pages, "qa_active": qa_active, "last_doc": last_doc}

    return {"tokens": tokens, "by_model": by_model, "cache": cache,
            "new_users": new_users, "conversations": conversations, "kb": kb}


def _active_between(conn, d1, d2):
    """Distinct real members who asked ≥1 question in [d1,d2] (widgets/embed excluded)."""
    return int(conn.execute(
        "SELECT count(DISTINCT ud.user_id) AS n FROM usage_daily ud "
        "JOIN users u ON u.id=ud.user_id "
        "WHERE ud.questions>0 AND ud.day BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed'",
        (d1, d2),
    ).fetchone()["n"] or 0)


def _adoption(conn, s, e, d7, d30):
    """DAU/WAU/MAU (anchored at window end e), 14-day DAU trend, stickiness,
    after-hours activity, plus the funnel's d7/d30 retention carried through."""
    dau = _active_between(conn, e, e)
    wau = _active_between(conn, e - datetime.timedelta(days=6), e)
    mau = _active_between(conn, e - datetime.timedelta(days=29), e)

    start14 = e - datetime.timedelta(days=13)
    by_day = {r["day"]: int(r["n"]) for r in conn.execute(
        "SELECT ud.day AS day, count(DISTINCT ud.user_id) AS n FROM usage_daily ud "
        "JOIN users u ON u.id=ud.user_id "
        "WHERE ud.questions>0 AND ud.day BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed' "
        "GROUP BY ud.day",
        (start14, e),
    ).fetchall()}
    trend = []
    d = start14
    while d <= e:
        trend.append(by_day.get(d, 0))
        d += datetime.timedelta(days=1)
    dau_avg_7d = sum(trend[-7:]) / 7 if trend else 0
    stickiness = round(100 * dau_avg_7d / mau) if mau else 0

    # user messages outside Mon–Fri 09:00–18:00 Asia/Yangon
    after = int(conn.execute(
        "SELECT count(*) AS n FROM messages m "
        "JOIN conversations c ON c.id=m.conversation_id JOIN users u ON u.id=c.user_id "
        "WHERE m.role='user' AND m.created_at::date BETWEEN %s AND %s "
        "  AND COALESCE(u.role,'')<>'widget' AND COALESCE(u.auth_source,'')<>'embed' "
        "  AND (extract(isodow FROM m.created_at AT TIME ZONE 'Asia/Yangon')>5 "
        "    OR extract(hour FROM m.created_at AT TIME ZONE 'Asia/Yangon')<9 "
        "    OR extract(hour FROM m.created_at AT TIME ZONE 'Asia/Yangon')>=18)",
        (s, e),
    ).fetchone()["n"] or 0)

    return {"dau": dau, "wau": wau, "mau": mau, "dau_trend": trend,
            "stickiness_pct": stickiness, "after_hours": after, "d7": d7, "d30": d30}


# ---- public API ---------------------------------------------------------------
def overview(days: int = 30, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Full productivity scorecard for a window (+ prior window for deltas).

    Explicit date_from/date_to (YYYY-MM-DD) win over `days`. Every key is always
    present; each section fails soft to a safe default and this never raises."""
    minutes, rate = _cfg()
    s, e, ps, pe, n = _window(days, date_from, date_to)

    # keep the rollup warm before we read it (fail-soft)
    try:
        from . import usage_rollup
        usage_rollup.ensure(max(n * 2, 30))
    except Exception:
        pass

    out = {
        "range": {"from": s.isoformat(), "to": e.isoformat(), "days": n,
                  "prev_from": ps.isoformat(), "prev_to": pe.isoformat()},
        "totals": {
            "questions": 0, "answered": 0, "hours_saved": 0.0, "labour_value": 0.0,
            "ai_cost": 0.0, "roi": None, "resolved_rate": 0.0, "deflection_rate": 0.0,
            "tickets_avoided": 0, "active_users": 0, "registered_users": 0,
            "avg_answer_s": 0.0, "refused_rate": 0.0,
            "lang": {"en": 0, "my": 0, "mixed": 0},
            "prev": {"questions": 0, "hours_saved": 0.0, "resolved_rate": 0.0,
                     "avg_answer_s": 0.0, "refused_rate": 0.0},
        },
        "trend": [], "hourly": [0] * 24, "domains": [], "departments": [],
        "funnel": {"registered": 0, "asked": 0, "weekly": 0, "power": 0,
                   "dormant": 0, "d7": 0, "d30": 0},
        "gaps": [], "people": [],
        "platform": {
            "tokens": {"total": 0, "tin": 0, "tout": 0, "per_answer": 0},
            "by_model": [], "cache": {"serves": 0, "hit_rate": 0.0, "avg_s": 0.0},
            "new_users": {"count": 0, "last": None},
            "conversations": {"count": 0, "avg_turns": 0.0, "one_turn_pct": 0, "max_turns": 0},
            "kb": {"docs": 0, "pages": 0, "qa_active": 0, "last_doc": None},
        },
        "adoption": {"dau": 0, "wau": 0, "mau": 0, "dau_trend": [0] * 14,
                     "stickiness_pct": 0, "after_hours": 0, "d7": 0, "d30": 0},
    }

    try:
        with get_conn() as conn:
            has_dept = _has_department(conn)
            for key, fn in (
                ("totals", lambda: _totals(conn, s, e, ps, pe, minutes, rate)),
                ("trend", lambda: _trend(conn, s, e, minutes)),
                ("hourly", lambda: _hourly(conn, s, e)),
                ("domains", lambda: _domains(conn, s, e, ps, pe, minutes)),
                ("departments", lambda: _departments(conn, s, e, ps, pe, minutes)),
                ("gaps", lambda: _gaps(conn, s, e, n)),
                ("people", lambda: _people(conn, s, e, minutes, rate, has_dept)),
                ("platform", lambda: _platform(conn, s, e)),
            ):
                try:
                    out[key] = fn()
                except Exception as ex:  # fail-soft: keep the default
                    print(f"[productivity] {key} failed: {ex!r}")
            try:
                out["funnel"] = _funnel(conn, s, e, out["totals"]["registered_users"])
            except Exception as ex:
                print(f"[productivity] funnel failed: {ex!r}")
            try:
                out["adoption"] = _adoption(conn, s, e, out["funnel"]["d7"], out["funnel"]["d30"])
            except Exception as ex:
                print(f"[productivity] adoption failed: {ex!r}")
    except Exception as ex:
        print(f"[productivity] overview failed: {ex!r}")

    return out
