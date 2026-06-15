"""Single-row app config (ROI knobs for the management scorecard). Admin-editable;
falls back to env defaults. Stored in app_config.data JSONB."""
import os

from .db import get_conn

_DEFAULTS = {
    # value model for the management slide
    "minutes_saved_per_answer": float(os.getenv("VALUE_MINUTES_SAVED", "6")),
    "llm_price_per_mtok": float(os.getenv("LLM_PRICE_PER_MTOK", "0.30")),
    "tokens_per_answer": int(os.getenv("TOKENS_PER_ANSWER", "3500")),
}


def get_config() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT data FROM app_config WHERE id=1").fetchone()
        data = (row or {}).get("data") or {}
    except Exception:
        data = {}
    return {**_DEFAULTS, **data}


import json as _json


def _raw() -> dict:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT data FROM app_config WHERE id=1").fetchone()
        return dict((row or {}).get("data") or {})
    except Exception:
        return {}


def _write(data: dict) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE app_config SET data = %s::jsonb WHERE id=1", (_json.dumps(data),))


# ---- Microsoft Teams integration config (under app_config.data.teams) ----
_TEAMS_KEYS = {"enabled", "app_id", "app_password", "public_url", "skip_auth"}


def get_teams() -> dict:
    t = _raw().get("teams") or {}
    return {
        "enabled": bool(t.get("enabled")), "app_id": t.get("app_id", ""),
        "app_password": t.get("app_password", ""), "public_url": t.get("public_url", ""),
        "skip_auth": bool(t.get("skip_auth")),
    }


def save_teams(patch: dict) -> dict:
    data = _raw()
    cur = data.get("teams") or {}
    for k, v in (patch or {}).items():
        if k in _TEAMS_KEYS and v is not None:
            cur[k] = bool(v) if k in ("enabled", "skip_auth") else str(v)
    data["teams"] = cur
    _write(data)
    return get_teams()


def save_config(patch: dict) -> dict:
    allowed = {"minutes_saved_per_answer", "llm_price_per_mtok", "tokens_per_answer"}
    clean = {}
    for k, v in (patch or {}).items():
        if k in allowed and v is not None:
            clean[k] = float(v) if k != "tokens_per_answer" else int(v)
    cur = get_config()
    merged = {**{k: cur[k] for k in allowed}, **clean}
    with get_conn() as conn:
        conn.execute("UPDATE app_config SET data = %s::jsonb WHERE id=1",
                     (__import__("json").dumps(merged),))
    return get_config()
