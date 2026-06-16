"""Microsoft Teams adapter (Azure Bot Service / Bot Framework).

Flow: Teams → Azure Bot → POST /api/teams/messages (this) → validate inbound JWT →
run Aria's answer pipeline (buffered) → reply via the Bot Connector API as an
Adaptive Card with citation buttons. Answers can be slow, so the HTTP handler
returns 200 immediately and the reply is posted from a background thread.

Config (Settings → Integrations → Microsoft Teams, stored in app_config.teams):
  enabled, app_id, app_password, public_url, skip_auth (dev tunnel only).
"""
import json
import time
import urllib.request
import urllib.parse

import jwt  # PyJWT
from jwt import PyJWKClient

from ..appcfg import get_config
from ..retrieve import search_pages, DEFAULT_K
from .. import agent as agent_mod
from ..db import get_conn

import os
from concurrent.futures import ThreadPoolExecutor

# Bounded worker pool for inbound Teams messages. Previously on_activity spawned
# an unbounded daemon Thread per message -> 1000 messages = 1000 threads + 1000
# concurrent LLM calls (spawn storm). A fixed pool caps in-flight work; overflow
# queues. Sized small since each job may call the LLM (see also LLM concurrency cap).
TEAMS_WORKERS = int(os.getenv("TEAMS_WORKERS", "8"))
_exec = ThreadPoolExecutor(max_workers=TEAMS_WORKERS, thread_name_prefix="teams")

_BOT_JWKS = "https://login.botframework.com/v1/.well-known/keys"
_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_jwk_client = None
_bot_token = {"value": "", "exp": 0.0}


def cfg() -> dict:
    return (get_config() or {}).get("teams", {}) or {}


def enabled() -> bool:
    c = cfg()
    return bool(c.get("enabled") and c.get("app_id") and c.get("app_password"))


# ---- inbound auth: verify the Bot Framework JWT ----
def verify_inbound(auth_header: str | None) -> bool:
    c = cfg()
    if c.get("skip_auth"):
        return True                      # dev tunnel only — NEVER in prod
    tok = (auth_header or "").replace("Bearer ", "").strip()
    if not tok:
        return False
    global _jwk_client
    try:
        if _jwk_client is None:
            _jwk_client = PyJWKClient(_BOT_JWKS)
        key = _jwk_client.get_signing_key_from_jwt(tok).key
        jwt.decode(tok, key, algorithms=["RS256"], audience=c.get("app_id"),
                   options={"verify_exp": True})
        return True
    except Exception as e:
        print(f"[teams] inbound auth failed: {e!r}")
        return False


# ---- outbound: client-credentials token to call the Connector API ----
def _bot_access_token() -> str:
    if _bot_token["value"] and _bot_token["exp"] > time.time() + 60:
        return _bot_token["value"]
    c = cfg()
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": c.get("app_id", ""),
        "client_secret": c.get("app_password", ""),
        "scope": "https://api.botframework.com/.default",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    _bot_token["value"] = d["access_token"]
    _bot_token["exp"] = time.time() + int(d.get("expires_in", 3000))
    return _bot_token["value"]


def _resolve_member_email(service_url: str, conv_id: str) -> str:
    """Best-effort: look up the Teams sender's email via the Bot Connector members API."""
    try:
        url = f"{service_url.rstrip('/')}/v3/conversations/{urllib.parse.quote(conv_id)}/members"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {_bot_access_token()}",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            members = json.load(r)
    except Exception as e:
        print(f"[teams] members lookup failed: {e!r}")
        return ""
    if not isinstance(members, list):
        return ""
    for m in members:
        if not isinstance(m, dict):
            continue
        email = (m.get("email") or m.get("userPrincipalName") or "").strip()
        if email:
            return email
    return ""


def _reply(service_url: str, conv_id: str, activity_id: str, activity: dict) -> None:
    url = f"{service_url.rstrip('/')}/v3/conversations/{urllib.parse.quote(conv_id)}/activities/{urllib.parse.quote(activity_id)}"
    body = json.dumps(activity).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_bot_access_token()}",
    })
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        print(f"[teams] reply failed: {e!r}")


# ---- build the Adaptive Card answer ----
def _page_meta(page_ids: list[int]) -> list[dict]:
    if not page_ids:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.id, p.page_no, d.name AS doc FROM pages p JOIN docs d ON d.id = p.doc_id "
            "WHERE p.id = ANY(%s)", (page_ids,),
        ).fetchall()
    by = {r["id"]: r for r in rows}
    return [{"id": i, "page_no": by[i]["page_no"], "doc": by[i]["doc"]} for i in page_ids if i in by]


def _card(text: str, page_ids: list[int]) -> dict:
    pub = cfg().get("public_url", "").rstrip("/")
    body = [
        {"type": "TextBlock", "text": "City Agent Aria", "weight": "Bolder", "size": "Small", "color": "Accent"},
        {"type": "TextBlock", "text": text, "wrap": True},
    ]
    actions = []
    for i, p in enumerate(_page_meta(page_ids)[:5], 1):
        if pub:
            actions.append({"type": "Action.OpenUrl",
                            "title": f"[{i}] {(_short(p['doc']))} p.{p['page_no']}",
                            "url": f"{pub}/api/pages/{p['id']}"})
    if actions:
        body.append({"type": "TextBlock", "text": "Sources", "weight": "Bolder", "size": "Small", "spacing": "Medium"})
    card = {
        "type": "AdaptiveCard", "version": "1.4",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "body": body, "actions": actions,
    }
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def _short(name: str) -> str:
    n = (name or "").rsplit("/", 1)[-1]
    return (n[:34] + "…") if len(n) > 35 else n


def _strip_mentions(activity: dict) -> str:
    text = activity.get("text", "") or ""
    for e in activity.get("entities", []) or []:
        if e.get("type") == "mention" and e.get("text"):
            text = text.replace(e["text"], "")
    return text.strip()


def _process(activity: dict) -> None:
    """Background: compute the answer and post it back to Teams."""
    q = _strip_mentions(activity)
    service_url = activity.get("serviceUrl", "")
    conv = (activity.get("conversation") or {}).get("id", "")
    aid = activity.get("id", "")
    if not (q and service_url and conv):
        return
    # best-effort per-user identity resolution (fail-soft — never blocks the reply)
    try:
        member_email = _resolve_member_email(service_url, conv)
        if member_email:
            mapped = None
            try:
                from ..auth import store as auth_store
                mapped = auth_store.get_by_email(member_email)
            except Exception as e:
                print(f"[teams] member email lookup failed: {e!r}")
            who = "known user" if mapped else "no Aria account"
            print(f"[teams] resolved member email={member_email} ({who})")
            try:
                from .. import notify
                notify.emit("teams", f"Teams question from {member_email}", level="info")
            except Exception:
                pass
    except Exception as e:
        print(f"[teams] identity resolution skipped: {e!r}")
    try:
        # Phase-4 Q&A bank: serve an approved cached answer with ZERO agent/LLM.
        # Repeat SOP questions over Teams are common — this is the cache that the
        # web /api/ask path already uses; wire it in here too (flag-gated).
        from .. import config as _cfg
        served = None
        if getattr(_cfg, "AUTO_QA_SERVE_ENABLED", True):
            try:
                from .. import qa as _qa
                served = _qa.serve_match(q, min_sim=getattr(_cfg, "AUTO_QA_SERVE_MIN_SIM", 0.72))
            except Exception as e:
                print(f"[teams] qa serve skipped: {e!r}")
        if served:
            page_ids = served.get("page_ids") or []
            out = {"text": served["answer"], "page_ids": page_ids}
            try:
                from .. import qa as _qa
                _qa.bump_served(served["id"])
            except Exception:
                pass
            print(f"[teams] served from Q&A bank id={served['id']} sim={served.get('sim'):.2f} (no LLM)")
        else:
            seed = search_pages(q, k=DEFAULT_K)
            if not seed:
                out = {"text": "I couldn't find that in the runbooks yet.", "page_ids": []}
            else:
                out = agent_mod.answer(q, seed, mode="quick")   # quick = fast for chat
    except Exception as e:
        out = {"text": f"Sorry, something went wrong: {e}", "page_ids": []}
    _reply(service_url, conv, aid, _card(out["text"], out.get("page_ids", [])))


def on_activity(activity: dict) -> dict:
    """Called by the route. Returns fast; replies happen in a thread."""
    if (activity or {}).get("type") != "message":
        return {}
    # Bounded pool instead of a fresh Thread per message (no spawn storm). Excess
    # messages queue and run as workers free up; _process is self-contained and
    # swallows its own errors, so a failed job never kills a pool worker.
    _exec.submit(_process, activity)
    return {}
