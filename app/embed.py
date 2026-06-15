"""Embeddable-widget access layer.

Two ways a third-party site talks to the agent without a member login:

  pk_live_…  PUBLIC key   — shipped in the <script> snippet. The browser calls
             POST /api/embed/session {public_key}; we check the request Origin
             against the key's allow-list and mint a short-lived widget JWT for
             an *anonymous* visitor.

  sk_live_…  SECRET key   — server-side only. The customer's backend calls
             POST /api/embed/token {visitor_id,name} with Authorization: Bearer
             sk_live_…; we mint a widget JWT bound to that identity (SSO passthrough).

Both tokens are consumed by the same chat endpoints via the `current_principal`
guard (see auth/deps.py). Every widget visitor is backed by a real `users` row
(role='widget') so conversations + analytics keep working, fully isolated per key.
"""
import hashlib
import secrets
import time

from fastapi import HTTPException

from .db import get_conn
from .auth.security import hash_password, verify_password, make_widget_token

# ---- key generation -------------------------------------------------------

def _gen(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def create_key(name: str, allowed_origins: list[str], created_by: int | None,
               rate_per_min: int = 30, greeting: str | None = None,
               accent: str = "#c2683f", position: str = "right",
               subtitle: str | None = None, logo_url: str | None = None,
               launcher: str = "robot", daily_msg_cap: int = 0,
               daily_cost_cap: float = 0) -> dict:
    """Mint a pk/sk pair. The raw secret is returned ONCE and never stored."""
    public_key = _gen("pk_live")
    raw_secret = _gen("sk_live")
    secret_hash = hash_password(raw_secret)
    origins = [_norm_origin(o) for o in (allowed_origins or []) if o.strip()]
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO embed_keys "
            "(name, public_key, secret_hash, secret_last4, allowed_origins, "
            " rate_per_min, greeting, accent, position, subtitle, logo_url, "
            " launcher, daily_msg_cap, daily_cost_cap, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (name, public_key, secret_hash, raw_secret[-4:], origins,
             rate_per_min, greeting, accent, position, subtitle, logo_url,
             launcher, daily_msg_cap or 0, daily_cost_cap or 0, created_by),
        ).fetchone()
        # provision the shared anonymous identity for this key
        anon = conn.execute(
            "INSERT INTO users (email, name, role, auth_source) "
            "VALUES (%s,%s,'widget','embed') RETURNING id",
            (f"widget+key{row['id']}@embed.local", f"{name} (anonymous)"),
        ).fetchone()
        conn.execute("UPDATE embed_keys SET anon_user_id=%s WHERE id=%s",
                     (anon["id"], row["id"]))
    out = dict(row)
    out["anon_user_id"] = anon["id"]
    out["secret"] = raw_secret          # surfaced ONCE to the admin UI
    return out


def list_keys() -> list[dict]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, public_key, secret_last4, allowed_origins, "
            "rate_per_min, greeting, accent, position, subtitle, logo_url, "
            "launcher, daily_msg_cap, daily_cost_cap, active, created_at, last_used_at "
            "FROM embed_keys ORDER BY created_at DESC"
        ).fetchall()


def update_key(key_id: int, **fields) -> dict | None:
    allowed = {"name", "allowed_origins", "rate_per_min", "greeting",
               "accent", "active", "position", "subtitle", "logo_url",
               "launcher", "daily_msg_cap", "daily_cost_cap"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "allowed_origins":
            v = [_norm_origin(o) for o in v if o.strip()]
        sets.append(f"{k}=%s")
        vals.append(v)
    if not sets:
        return get_key(key_id)
    vals.append(key_id)
    with get_conn() as conn:
        return conn.execute(
            f"UPDATE embed_keys SET {', '.join(sets)} WHERE id=%s RETURNING *",
            tuple(vals),
        ).fetchone()


def delete_key(key_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM embed_keys WHERE id=%s", (key_id,))


def get_key(key_id: int) -> dict | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM embed_keys WHERE id=%s",
                            (key_id,)).fetchone()


def rotate_secret(key_id: int) -> str:
    """Issue a fresh sk_, invalidate the old one. Returns the raw secret once."""
    raw_secret = _gen("sk_live")
    with get_conn() as conn:
        conn.execute(
            "UPDATE embed_keys SET secret_hash=%s, secret_last4=%s WHERE id=%s",
            (hash_password(raw_secret), raw_secret[-4:], key_id),
        )
    return raw_secret


# ---- origin allow-list ----------------------------------------------------

def _norm_origin(o: str) -> str:
    o = (o or "").strip().lower().rstrip("/")
    return o


def origin_allowed(key: dict, origin: str | None) -> bool:
    allow = key.get("allowed_origins") or []
    if not allow:                       # empty list = wildcard (dev convenience)
        return True
    return _norm_origin(origin or "") in allow


# ---- visitor identity -----------------------------------------------------

def _visitor_user(key: dict, visitor_id: str, name: str | None) -> int:
    """Find or create the users row backing this (key, visitor)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM embed_visitors WHERE key_id=%s AND visitor_id=%s",
            (key["id"], visitor_id),
        ).fetchone()
        if row:
            conn.execute("UPDATE embed_visitors SET last_seen=now() WHERE "
                         "key_id=%s AND visitor_id=%s", (key["id"], visitor_id))
            return row["user_id"]
        vhash = hashlib.sha1(visitor_id.encode()).hexdigest()[:12]
        u = conn.execute(
            "INSERT INTO users (email, name, role, auth_source) "
            "VALUES (%s,%s,'widget','embed') RETURNING id",
            (f"widget+key{key['id']}+{vhash}@embed.local", name or "Visitor"),
        ).fetchone()
        conn.execute(
            "INSERT INTO embed_visitors (key_id, visitor_id, name, user_id) "
            "VALUES (%s,%s,%s,%s)", (key["id"], visitor_id, name, u["id"]),
        )
        return u["id"]


def _touch(key_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE embed_keys SET last_used_at=now() WHERE id=%s",
                     (key_id,))


# ---- session minting (the two public entry points) ------------------------

def start_session_public(public_key: str, origin: str | None,
                         visitor_id: str | None) -> dict:
    """pk_ path: anonymous visitor, gated by Origin allow-list."""
    with get_conn() as conn:
        key = conn.execute(
            "SELECT * FROM embed_keys WHERE public_key=%s AND active",
            (public_key,),
        ).fetchone()
    if not key:
        raise HTTPException(status_code=401, detail="invalid embed key")
    if not origin_allowed(key, origin):
        log_event(key["id"], "origin_block")
        raise HTTPException(status_code=403, detail="origin not allowed")
    vid = visitor_id or secrets.token_urlsafe(12)
    # each anonymous visitor gets its own backing identity → conversations isolate
    uid = _visitor_user(key, vid, None)
    token = make_widget_token(uid, key["id"], vid)
    _touch(key["id"])
    return {"token": token, "visitor_id": vid, "ttl_min": _ttl(),
            **theme_for(key)}


def sandbox_token(key_id: int) -> dict:
    """Admin-only: mint a real visitor token for a key WITHOUT the Origin check,
    so the dashboard sandbox can drive the live widget + raw API."""
    key = get_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="key not found")
    vid = "sandbox-" + secrets.token_urlsafe(6)
    uid = _visitor_user(key, vid, "Sandbox")
    token = make_widget_token(uid, key["id"], vid)
    _touch(key["id"])
    return {"token": token, "visitor_id": vid, "ttl_min": _ttl(), **theme_for(key)}


def theme_for(key: dict) -> dict:
    """Per-key appearance, falling back to the brand default for empty fields."""
    b = get_brand()
    return {
        "greeting": key.get("greeting") or b.get("greeting"),
        "accent": key.get("accent") or b.get("accent") or "#c2683f",
        "position": key.get("position") or b.get("position") or "right",
        "subtitle": key.get("subtitle") or b.get("subtitle"),
        "logo_url": key.get("logo_url") or b.get("logo_url"),
        "launcher": key.get("launcher") or b.get("launcher") or "robot",
        "title": key.get("name") or b.get("title") or "City Agent Aria",
    }


def mint_token_secret(secret_key: str, visitor_id: str,
                      name: str | None) -> dict:
    """sk_ path: server-to-server, identity bound to the caller's visitor_id."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM embed_keys WHERE active").fetchall()
    key = next((k for k in rows if verify_password(secret_key, k["secret_hash"])),
               None)
    if not key:
        raise HTTPException(status_code=401, detail="invalid secret key")
    if not visitor_id:
        raise HTTPException(status_code=422, detail="visitor_id required")
    uid = _visitor_user(key, visitor_id, name)
    token = make_widget_token(uid, key["id"], visitor_id, name)
    _touch(key["id"])
    return {"token": token, "ttl_min": _ttl()}


def _ttl() -> int:
    from .config import EMBED_TOKEN_TTL_MIN
    return EMBED_TOKEN_TTL_MIN


# ---- per-visitor rate limit (DB minute-bucket, cross-process) -------------
# Was an in-process dict — that under-counts across multiple workers/replicas
# (each process kept its own bucket, so the real limit was N×). A shared
# per-minute counter row in Postgres makes the cap hold no matter how many
# app instances are running behind the load balancer.

def check_rate(key_id: int, visitor_id: str, limit_per_min: int) -> None:
    minute = int(time.time()) // 60
    with get_conn() as conn:
        row = conn.execute(
            "INSERT INTO embed_rate (key_id, visitor_id, minute, n) VALUES (%s,%s,%s,1) "
            "ON CONFLICT (key_id, visitor_id, minute) "
            "DO UPDATE SET n = embed_rate.n + 1 RETURNING n",
            (key_id, visitor_id, minute),
        ).fetchone()
        # opportunistic prune keeps the table tiny (only the live window matters)
        conn.execute("DELETE FROM embed_rate WHERE minute < %s", (minute - 5,))
    if row["n"] > max(1, limit_per_min):
        log_event(key_id, "rate_block")
        raise HTTPException(status_code=429, detail="rate limit exceeded")


# ---- per-key daily ceilings (runaway-cost / abuse guard) ------------------
# An allowed-origin abuser can't rack up unbounded LLM spend: each key may set a
# daily message cap and/or a daily dollar cap. check_caps is a cheap read run on
# every widget request; bump_message + record_usage feed the daily roll-up.

def check_caps(key: dict) -> None:
    cap_msg = int(key.get("daily_msg_cap") or 0)
    cap_cost = float(key.get("daily_cost_cap") or 0)
    if cap_msg <= 0 and cap_cost <= 0:
        return
    with get_conn() as conn:
        row = conn.execute(
            "SELECT messages, cost_usd FROM embed_usage "
            "WHERE key_id=%s AND day=current_date", (key["id"],),
        ).fetchone()
    msgs = int((row or {}).get("messages") or 0)
    cost = float((row or {}).get("cost_usd") or 0)
    if cap_msg > 0 and msgs >= cap_msg:
        log_event(key["id"], "cap_block")
        raise HTTPException(status_code=429, detail="daily message cap reached")
    if cap_cost > 0 and cost >= cap_cost:
        log_event(key["id"], "cap_block")
        raise HTTPException(status_code=429, detail="daily budget reached")


def bump_message(key_id: int) -> None:
    """One question served on this key — counts toward the daily message cap."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO embed_usage (key_id, day, messages) VALUES (%s,current_date,1) "
                "ON CONFLICT (key_id, day) DO UPDATE SET messages = embed_usage.messages + 1",
                (key_id,))
    except Exception:
        pass


def record_usage(key_id: int, tokens: int, cost: float) -> None:
    """Add this answer's real token + dollar spend to the key's daily roll-up."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO embed_usage (key_id, day, tokens, cost_usd) "
                "VALUES (%s,current_date,%s,%s) ON CONFLICT (key_id, day) "
                "DO UPDATE SET tokens = embed_usage.tokens + %s, "
                "cost_usd = embed_usage.cost_usd + %s",
                (key_id, int(tokens or 0), cost or 0, int(tokens or 0), cost or 0))
    except Exception:
        pass


# ---- brand defaults (single shared appearance) ----------------------------

_BRAND_DEFAULTS = {
    "accent": "#c2683f", "position": "right",
    "greeting": "Hi! Ask me anything about our runbooks & SOPs.",
    "subtitle": "online · replies instantly", "logo_url": "",
    "launcher": "robot", "title": "City Agent Aria",
}


def get_brand() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT data FROM app_config WHERE id=1").fetchone()
        b = (row or {}).get("data", {}).get("embed_brand", {}) if row else {}
    except Exception:
        b = {}
    return {**_BRAND_DEFAULTS, **(b or {})}


def set_brand(patch: dict) -> dict:
    keep = {k: v for k, v in patch.items() if k in _BRAND_DEFAULTS and v is not None}
    with get_conn() as conn:
        conn.execute(
            "UPDATE app_config SET data = jsonb_set("
            "coalesce(data,'{}'::jsonb), '{embed_brand}', %s::jsonb, true) WHERE id=1",
            (__import__("json").dumps({**get_brand(), **keep}),),
        )
    return get_brand()


# ---- retention: keep the users table + embed logs from growing forever -----

def prune_stale(visitor_ttl_days: int = 90, event_ttl_days: int = 60) -> dict:
    """Drop idle ANONYMOUS visitors that never started a conversation (zero chat
    history lost), plus trim the append-only embed_events / embed_usage / stale
    embed_rate rows. Conservative by design: a visitor with ANY conversation is
    never touched."""
    out = {"visitors": 0, "events": 0, "usage": 0}
    with get_conn() as conn:
        try:
            conn.execute("DELETE FROM embed_events WHERE at < now() - (%s||' days')::interval",
                         (event_ttl_days,))
            conn.execute("DELETE FROM embed_usage WHERE day < current_date - %s",
                         (event_ttl_days,))
            conn.execute("DELETE FROM embed_rate WHERE minute < "
                         "(extract(epoch FROM now())/60)::bigint - 10")
        except Exception as e:
            print(f"[embed.prune] log trim failed: {e!r}")
        try:
            vids = conn.execute(
                "SELECT ev.id, ev.user_id FROM embed_visitors ev "
                "WHERE ev.last_seen < now() - (%s||' days')::interval "
                "AND NOT EXISTS (SELECT 1 FROM conversations c WHERE c.user_id = ev.user_id)",
                (visitor_ttl_days,)).fetchall()
            for v in vids:
                conn.execute("DELETE FROM embed_visitors WHERE id=%s", (v["id"],))
                conn.execute("DELETE FROM users WHERE id=%s AND role='widget'",
                             (v["user_id"],))
                out["visitors"] += 1
        except Exception as e:
            print(f"[embed.prune] visitor prune failed: {e!r}")
    return out


def start_prune_daemon() -> None:
    from .config import (EMBED_PRUNE_ENABLED, EMBED_VISITOR_TTL_DAYS,
                         EMBED_EVENT_TTL_DAYS, EMBED_PRUNE_INTERVAL_H)
    if not EMBED_PRUNE_ENABLED:
        return
    import threading
    import time as _t

    def _loop():
        _t.sleep(60)   # let boot settle
        while True:
            try:
                r = prune_stale(EMBED_VISITOR_TTL_DAYS, EMBED_EVENT_TTL_DAYS)
                if r["visitors"]:
                    print(f"[embed.prune] removed {r['visitors']} idle visitors")
            except Exception as e:
                print(f"[embed.prune] cycle failed: {e!r}")
            _t.sleep(max(1, EMBED_PRUNE_INTERVAL_H) * 3600)

    threading.Thread(target=_loop, daemon=True).start()
    print("[embed.prune] daemon started")


def log_event(key_id: int | None, kind: str) -> None:
    try:
        with get_conn() as conn:
            conn.execute("INSERT INTO embed_events (key_id, kind) VALUES (%s,%s)",
                         (key_id, kind))
    except Exception:
        pass


# ---- usage analytics for the Monitoring tab -------------------------------

def stats(days: int = 30) -> dict:
    """Per-widget + total: visitors, messages, sessions, blocked. All derived
    from existing tables (embed_visitors → conversations → messages)."""
    out = {"totals": {}, "widgets": [], "series": [], "blocked": {}, "days": days}
    with get_conn() as conn:
        per = conn.execute(
            """
            SELECT k.id, k.name, k.active,
                   count(distinct ev.id)                               AS visitors,
                   count(distinct c.id)                                AS sessions,
                   count(m.id) FILTER (WHERE m.role='user')            AS messages,
                   max(m.created_at)                                   AS last_seen
            FROM embed_keys k
            LEFT JOIN embed_visitors ev ON ev.key_id = k.id
            LEFT JOIN conversations c   ON c.user_id = ev.user_id
            LEFT JOIN messages m        ON m.conversation_id = c.id
                                       AND m.created_at > now() - (%s||' days')::interval
            GROUP BY k.id, k.name, k.active
            ORDER BY messages DESC NULLS LAST
            """, (days,),
        ).fetchall()
        widgets = [dict(r) for r in per]
        # today's spend + the configured ceilings, so Monitoring can show
        # "142 / 500 msgs · $0.21 / $5.00" per widget
        caps = {r["id"]: dict(r) for r in conn.execute(
            "SELECT id, daily_msg_cap, daily_cost_cap FROM embed_keys").fetchall()}
        today = {r["key_id"]: dict(r) for r in conn.execute(
            "SELECT key_id, messages, cost_usd FROM embed_usage "
            "WHERE day=current_date").fetchall()}
        for w in widgets:
            c = caps.get(w["id"], {})
            t = today.get(w["id"], {})
            w["daily_msg_cap"] = c.get("daily_msg_cap") or 0
            w["daily_cost_cap"] = float(c.get("daily_cost_cap") or 0)
            w["today_messages"] = int(t.get("messages") or 0)
            w["today_cost"] = float(t.get("cost_usd") or 0)
        out["widgets"] = widgets
        out["totals"] = {
            "widgets": len(per),
            "live": sum(1 for r in per if r["active"]),
            "visitors": sum(r["visitors"] or 0 for r in per),
            "messages": sum(r["messages"] or 0 for r in per),
            "sessions": sum(r["sessions"] or 0 for r in per),
        }
        blk = conn.execute(
            "SELECT kind, count(*) n FROM embed_events "
            "WHERE at > now() - (%s||' days')::interval GROUP BY kind", (days,),
        ).fetchall()
        out["blocked"] = {r["kind"]: r["n"] for r in blk}
        out["totals"]["blocked"] = sum(out["blocked"].values())
        series = conn.execute(
            """
            SELECT to_char(d::date,'Mon DD') AS label,
                   coalesce(count(m.id) FILTER (WHERE m.role='user'),0) AS n
            FROM generate_series(now() - (%s||' days')::interval, now(), '1 day') d
            LEFT JOIN conversations c ON c.user_id IN (SELECT user_id FROM embed_visitors)
            LEFT JOIN messages m ON m.conversation_id = c.id
                                AND m.created_at::date = d::date
            GROUP BY d ORDER BY d
            """, (days,),
        ).fetchall()
        out["series"] = [dict(r) for r in series]
    return out
