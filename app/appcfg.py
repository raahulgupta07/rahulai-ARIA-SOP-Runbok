"""Single-row app config (ROI knobs for the management scorecard). Admin-editable;
falls back to env defaults. Stored in app_config.data JSONB."""
import os

from .db import get_conn

_DEFAULTS = {
    # value model for the management slide
    "minutes_saved_per_answer": float(os.getenv("VALUE_MINUTES_SAVED", "6")),
    "llm_price_per_mtok": float(os.getenv("LLM_PRICE_PER_MTOK", "0.30")),
    "tokens_per_answer": int(os.getenv("TOKENS_PER_ANSWER", "3500")),
    # avg loaded labour cost per hour — converts hours-saved into $ value on
    # the Insights productivity page
    "hourly_rate_usd": float(os.getenv("VALUE_HOURLY_RATE", "4.0")),
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


# ---- multi-tenant RBAC switch (DB over env; super-admin flips it live, no restart) ----
def get_rbac_enabled() -> bool:
    """Effective multi-tenant RBAC switch: DB value over the RBAC_ENABLED env default."""
    v = _raw().get("rbac_enabled")
    if v is not None:
        return bool(v)
    return os.getenv("RBAC_ENABLED", "0") == "1"


def set_rbac_enabled(on: bool) -> bool:
    data = _raw()
    data["rbac_enabled"] = bool(on)
    _write(data)
    return get_rbac_enabled()


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


# ---- one-logo white-label brand (under app_config.data.brand) ----
# Dedicated save_brand merges into app_config.data.brand WITHOUT going through
# the allowlisted save_config (which would strip these keys). GET /api/brand is
# public and must never raise — get_brand() returns the merged defaults+saved.
_BRAND_TEXT_KEYS = {"name", "short_name", "tagline", "footer", "assistant_label",
                    "accent", "accent_dk", "logo_url"}

_BRAND_DEFAULTS = {
    "name": "City Agent Aria",
    "short_name": "Aria",
    "tagline": "Your runbook intelligence — answered with the source page.",
    "footer": "© 2026 City Agent Aria · Runbooks & IT Assistance",
    "assistant_label": "ARIA 1.0",
    "accent": "#c2683f",
    "accent_dk": "#a8542f",
    "logo_url": "/brand-logo.png",
    "mark_url": "/favicon.png",
    "favicon_url": "/favicon.png",
    "icon192_url": "/icon-192.png",
    "icon512_url": "/icon-512.png",
    "custom": False,
}


def get_brand() -> dict:
    """Public brand payload = defaults merged with any saved override. Never raises."""
    out = dict(_BRAND_DEFAULTS)
    try:
        saved = _raw().get("brand") or {}
    except Exception:
        saved = {}
    for k, v in saved.items():
        if v is not None:
            out[k] = v
    out["custom"] = bool(saved.get("custom"))
    return out


def save_brand(patch: dict) -> dict:
    """Merge text/accent fields into app_config.data.brand (preserve other keys).
    Bypasses the save_config allowlist. Returns the full public brand payload."""
    data = _raw()
    cur = data.get("brand") or {}
    for k, v in (patch or {}).items():
        if k in _BRAND_TEXT_KEYS and v is not None:
            cur[k] = str(v)
        elif k == "custom" and v is not None:
            cur[k] = bool(v)
    data["brand"] = cur
    _write(data)
    return get_brand()


# ---- runtime feature toggles (under app_config.data.features) ----
# Per-deployment switches so ONE codebase serves different projects with NO
# redeploy. DB value overrides the env default. Keys are boolean + small.
import os as _os

_FEATURE_DEFAULTS = {
    "citations": _os.getenv("CITATIONS_ENABLED", "1") == "1",
}


def get_features() -> dict:
    """Effective feature toggles = env defaults merged with any saved DB override."""
    out = dict(_FEATURE_DEFAULTS)
    try:
        saved = _raw().get("features") or {}
    except Exception:
        saved = {}
    for k, v in saved.items():
        if isinstance(v, bool):
            out[k] = v
    return out


def save_features(patch: dict) -> dict:
    """Merge boolean toggles into app_config.data.features (preserve other keys)."""
    data = _raw()
    cur = data.get("features") or {}
    for k, v in (patch or {}).items():
        if k in _FEATURE_DEFAULTS and v is not None:
            cur[k] = bool(v)
    data["features"] = cur
    _write(data)
    return get_features()


def citations_enabled() -> bool:
    """True when answers should carry inline [N] citations + source pages."""
    try:
        return bool(get_features().get("citations", True))
    except Exception:
        return True


# ---- wiki / knowledge schema (under app_config.data.wiki_schema) ----
# Karpathy "LLM wiki" Layer 3: a per-project config that defines the conventions
# the LLM maintains the knowledge base to. Read by the compiler, the contradiction
# pass (Phase 1), the temporal layer (Phase 2) and the browsable wiki (Phase 3).
# DB value overrides these defaults. Keep it a small, human-auditable JSON blob.
_WIKI_SCHEMA_DEFAULTS = {
    "wiki_title": "Knowledge Base",
    # what counts as a linkable entity/concept (drives entity hub pages + wikilinks)
    "entity_types": ["system", "screen", "menu", "field", "code", "role",
                     "transaction", "report"],
    # how an entity is rendered as a link in compiled markdown
    "link_style": "[[entity]]",
    # contradiction detection (Phase 1): which claim attributes to compare across
    # docs, and whether a conflict auto-resolves to the newer doc or waits for review
    "contradiction": {
        "compare_attributes": ["value", "threshold", "setting", "code", "path"],
        "auto_resolve_newer": False,   # False = route every conflict to review
        "min_severity": "value_mismatch",
    },
    # a claim/page is "stale" past this many days with no newer corroboration
    "freshness_days": 180,
    # orphan health-check (Phase 4): flag pages with fewer than N inbound links
    "orphan_min_inbound": 1,
}


def _merge_schema(base: dict, saved: dict) -> dict:
    """One-level-deep merge so a saved {contradiction:{auto_resolve_newer:true}}
    doesn't wipe the other contradiction defaults."""
    out = {}
    for k, v in base.items():
        sv = saved.get(k)
        if isinstance(v, dict) and isinstance(sv, dict):
            out[k] = {**v, **sv}
        elif sv is not None:
            out[k] = sv
        else:
            out[k] = v
    return out


def get_wiki_schema() -> dict:
    """Effective wiki schema = defaults merged with any saved DB override."""
    try:
        saved = _raw().get("wiki_schema") or {}
    except Exception:
        saved = {}
    return _merge_schema(_WIKI_SCHEMA_DEFAULTS, saved)


def save_wiki_schema(patch: dict) -> dict:
    """Merge a patch into app_config.data.wiki_schema (preserve unknown keys).
    Validates types defensively; bad values are dropped, never raised."""
    data = _raw()
    cur = data.get("wiki_schema") or {}
    for k, v in (patch or {}).items():
        if k not in _WIKI_SCHEMA_DEFAULTS:
            continue
        default = _WIKI_SCHEMA_DEFAULTS[k]
        if isinstance(default, dict) and isinstance(v, dict):
            cur[k] = {**(cur.get(k) or {}), **v}
        elif isinstance(default, list) and isinstance(v, list):
            cur[k] = [str(x) for x in v if str(x).strip()]
        elif isinstance(default, bool):
            cur[k] = bool(v)
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                cur[k] = int(v)
            except (TypeError, ValueError):
                continue
        elif isinstance(default, str):
            cur[k] = str(v)
    data["wiki_schema"] = cur
    _write(data)
    return get_wiki_schema()


# ---- agent persona (under app_config.data.persona) ----
# Identity/voice layer, generated from the corpus, applied on top of the grounding
# rules. Changes HOW the agent talks, never WHAT it knows (still docs-only + cited).
# Live, per-project. Versioned with a small rollback history.
_PERSONA_DEFAULTS = {
    "enabled": False,
    "name": "Aria",
    "role": "company runbook & IT assistant",
    "covers": [],                 # topic chips, auto-filled from the corpus
    "tone": "warm",               # formal | warm | casual
    "length": "full",             # brief | full | exhaustive
    "audience": "mixed",          # end-user | it | mixed
    "rules": [],                  # behaviour bullet list
    "greeting": "",
    "version": 1,
    "generated_from": "",         # e.g. "2 documents"
}
_PERSONA_STR = {"name", "role", "tone", "length", "audience", "greeting", "generated_from"}
_PERSONA_LIST = {"covers", "rules"}


def get_persona() -> dict:
    """Effective persona = defaults merged with the saved override."""
    out = dict(_PERSONA_DEFAULTS)
    try:
        saved = _raw().get("persona") or {}
    except Exception:
        saved = {}
    for k, v in saved.items():
        if k in _PERSONA_DEFAULTS and v is not None:
            out[k] = v
    return out


def save_persona(patch: dict) -> dict:
    """Merge a persona patch, bump version, push the prior version to history
    (cap 10) for rollback. Validates types defensively; never raises into a route."""
    data = _raw()
    cur = data.get("persona") or {}
    prior = dict(get_persona())
    for k, v in (patch or {}).items():
        if k not in _PERSONA_DEFAULTS or v is None:
            continue
        if k in _PERSONA_STR:
            cur[k] = str(v)
        elif k in _PERSONA_LIST and isinstance(v, list):
            cur[k] = [str(x) for x in v if str(x).strip()]
        elif k == "enabled":
            cur[k] = bool(v)
    cur["version"] = int(prior.get("version", 1)) + 1
    hist = data.get("persona_history") or []
    hist.insert(0, {k: prior.get(k) for k in _PERSONA_DEFAULTS})
    data["persona"] = cur
    data["persona_history"] = hist[:10]
    _write(data)
    return get_persona()


def persona_history() -> list:
    try:
        return (_raw().get("persona_history") or [])[:10]
    except Exception:
        return []


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
