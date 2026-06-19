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


# ---- S3 / MinIO object storage config (under app_config.data.s3) ----
# DB-saved values OVERRIDE env defaults so a super-admin can configure storage
# entirely from the UI. The secret is stored here (same convention as teams) but
# NEVER returned by get_s3(reveal=False) — only a has_secret flag.
_S3_STR_KEYS = {"backend", "bucket", "region", "endpoint_url",
                "access_key_id", "prefix", "import_prefix"}
_S3_BOOL_KEYS = {"force_path_style", "presign"}


def _s3_env_defaults() -> dict:
    from . import config as _c
    return {
        "backend": _c.STORAGE,
        "bucket": _c.S3_BUCKET,
        "region": _c.S3_REGION,
        "endpoint_url": _c.S3_ENDPOINT_URL,
        "access_key_id": _c.S3_ACCESS_KEY_ID,
        "secret_access_key": _c.S3_SECRET_ACCESS_KEY,
        "prefix": _c.S3_PREFIX,
        "import_prefix": _c.S3_IMPORT_PREFIX,
        "force_path_style": _c.S3_FORCE_PATH_STYLE,
        "presign": _c.S3_PRESIGN,
    }


def get_s3(reveal: bool = False) -> dict:
    """Effective S3 config = DB over env. reveal=True includes the real secret
    (internal use only); reveal=False masks it and adds has_secret (API/UI use)."""
    saved = _raw().get("s3") or {}
    eff = {**_s3_env_defaults(), **{k: v for k, v in saved.items() if v is not None}}
    eff["backend"] = "s3" if eff.get("backend") == "s3" else "local"
    eff["force_path_style"] = bool(eff.get("force_path_style"))
    eff["presign"] = bool(eff.get("presign"))
    has_secret = bool(eff.get("secret_access_key"))
    if not reveal:
        eff.pop("secret_access_key", None)
        eff["has_secret"] = has_secret
    return eff


def save_s3(patch: dict) -> dict:
    data = _raw()
    cur = data.get("s3") or {}
    for k, v in (patch or {}).items():
        if k in _S3_STR_KEYS and v is not None:
            cur[k] = str(v)
        elif k in _S3_BOOL_KEYS and v is not None:
            cur[k] = bool(v)
        elif k == "secret_access_key" and v:        # only overwrite when a new value is sent
            cur[k] = str(v)
    data["s3"] = cur
    _write(data)
    return get_s3(reveal=False)


# ---- dream cycle config (under app_config.data.dream) — DB overrides env ----
# Lets an admin flip the dream-cycle behaviour live (no redeploy). dream.py reads
# the effective value at run time via these helpers.
_DREAM_BOOL_KEYS = {"enabled", "auto_resolve", "gap_fill", "autolink"}
_DREAM_INT_KEYS = {"interval_h", "stale_days", "promote_age_h"}
_DREAM_FLOAT_KEYS = {"promote_min_conf"}   # auto-activate pending facts at/above this confidence


def _dream_env_defaults() -> dict:
    from . import config as _c
    return {
        "enabled": _c.DREAM_ENABLED,
        "interval_h": _c.DREAM_INTERVAL_H,
        "stale_days": _c.DREAM_STALE_DAYS,
        "promote_age_h": _c.DREAM_PROMOTE_AGE_H,
        "promote_min_conf": _c.AUTO_APPROVE_MIN_CONF,
        "auto_resolve": _c.DREAM_AUTO_RESOLVE,
        "gap_fill": _c.DREAM_GAP_FILL,
        "autolink": _c.DREAM_AUTOLINK,
    }


def get_dream() -> dict:
    """Effective dream-cycle config = DB over env."""
    saved = _raw().get("dream") or {}
    eff = {**_dream_env_defaults(), **{k: v for k, v in saved.items() if v is not None}}
    for k in _DREAM_BOOL_KEYS:
        eff[k] = bool(eff.get(k))
    for k in _DREAM_INT_KEYS:
        try:
            eff[k] = int(eff.get(k))
        except Exception:
            pass
    for k in _DREAM_FLOAT_KEYS:
        try:
            eff[k] = float(eff.get(k))
        except Exception:
            pass
    return eff


def save_dream(patch: dict) -> dict:
    data = _raw()
    cur = data.get("dream") or {}
    for k, v in (patch or {}).items():
        if k in _DREAM_BOOL_KEYS and v is not None:
            cur[k] = bool(v)
        elif k in _DREAM_INT_KEYS and v is not None:
            try:
                cur[k] = int(v)
            except Exception:
                pass
        elif k in _DREAM_FLOAT_KEYS and v is not None:
            try:
                cur[k] = float(v)
            except Exception:
                pass
    data["dream"] = cur
    _write(data)
    return get_dream()


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
