"""Fact approval policy — decides per source whether a new fact goes ACTIVE
(injected into answers) or PENDING (admin reviews in Facts).

Policy is per-source: mode = 'auto' | 'review', with an optional confidence floor
(auto-approve only when confidence >= min; below floor → pending). Stored in
app_config.data.governance, admin-editable in Settings → Knowledge governance.
Defaults = the "recommended mix": hand-taught auto, doc facts auto≥0.90,
chat + corrections reviewed.
"""
from . import appcfg

SOURCES = ["human", "doc", "chat", "feedback", "qa", "gap"]

_DEFAULTS = {
    "human":    {"mode": "auto",   "min": 0.0},   # you typed it → trusted
    "doc":      {"mode": "auto",   "min": 0.90},  # straight from your SOPs, high volume
    "chat":     {"mode": "review", "min": 0.95},  # user-asserted, riskier
    "feedback": {"mode": "review", "min": 0.0},   # 👎 corrections, riskier
    "qa":       {"mode": "auto",   "min": 0.90},  # doc-mined Q&A, grounded w/ page cites
    "gap":      {"mode": "review", "min": 0.0},   # daemon-synthesized → always reviewed
}


def get_policy() -> dict:
    saved = (appcfg._raw().get("governance") or {})
    out = {}
    for s in SOURCES:
        d = {**_DEFAULTS[s], **(saved.get(s) or {})}
        out[s] = {"mode": "auto" if d.get("mode") == "auto" else "review",
                  "min": max(0.0, min(1.0, float(d.get("min", 0.0))))}
    return out


def save_policy(patch: dict) -> dict:
    data = appcfg._raw()
    cur = data.get("governance") or {}
    for s in SOURCES:
        if s in (patch or {}):
            p = patch[s] or {}
            entry = cur.get(s) or {}
            if "mode" in p:
                entry["mode"] = "auto" if p["mode"] == "auto" else "review"
            if "min" in p and p["min"] is not None:
                entry["min"] = max(0.0, min(1.0, float(p["min"])))
            cur[s] = entry
    data["governance"] = cur
    appcfg._write(data)
    return get_policy()


def decide_status(source: str, confidence: float | None = None,
                  is_admin: bool = False) -> str:
    """Return 'active' or 'pending' for a new fact from `source`.
    auto + (no confidence OR confidence >= floor) → active; else pending."""
    pol = get_policy().get(source) or _DEFAULTS.get(source) or {"mode": "review", "min": 0.0}
    if pol["mode"] != "auto":
        return "pending"
    if confidence is not None and confidence < pol["min"]:
        return "pending"
    return "active"
