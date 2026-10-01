"""OIDC / SSO (Keycloak, Azure AD, Google) — authorization-code flow.
Discovery via /.well-known, id_token verified against the provider JWKS."""
import secrets
import threading
import time
import urllib.parse

import httpx
import jwt

from ..config import PUBLIC_URL, OIDC_BEARER_ENABLED, OIDC_BEARER_CLIENT_IDS, OIDC_BEARER_AUTO_CREATE
from ..db import get_conn
from . import store

# CSRF state lives in Postgres (oidc_state table), not an in-process dict, so it
# survives across uvicorn workers — login can land on a different worker than the
# callback. Consumed one-shot; expired rows (>10 min) pruned lazily on each use.
_STATE_TTL_MIN = 10

# A normal browser-ish User-Agent on every server-to-server call to the IdP.
# A WAF / reverse proxy in front of Keycloak commonly 403s bot/library UAs
# (e.g. "Python-urllib") on the discovery / JWKS / userinfo paths.
_UA = {"User-Agent": "Mozilla/5.0 (Aria OIDC client)", "Accept": "application/json"}


def _state_put(state: str, pid: str | None = None, nonce: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO oidc_state (state, pid, nonce) VALUES (%s, %s, %s) "
                     "ON CONFLICT (state) DO NOTHING", (state, pid, nonce))


def _state_consume(state: str):
    """Atomically take a fresh, unexpired state (one-shot). Returns (ok, pid, nonce)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM oidc_state WHERE created_at < now() - make_interval(mins => %s)",
                     (_STATE_TTL_MIN,))
        row = conn.execute(
            "DELETE FROM oidc_state WHERE state = %s "
            "AND created_at >= now() - make_interval(mins => %s) RETURNING pid, nonce",
            (state, _STATE_TTL_MIN),
        ).fetchone()
    return (bool(row), (row.get("pid") if row else None), (row.get("nonce") if row else None))


class OidcError(Exception):
    pass


def consume_state(state: str):
    """Public one-shot state check for the callback route. Returns (ok, pid, nonce)."""
    return _state_consume(state)


_WK = "/.well-known/openid-configuration"


def _issuer_base(issuer: str) -> str:
    """Normalise a pasted issuer to the bare issuer URL. Tolerates an admin
    pasting the FULL discovery URL (…/.well-known/openid-configuration) — we
    strip it so we never build a doubled …/.well-known/…/.well-known/… path."""
    iss = (issuer or "").strip().rstrip("/")
    if iss.endswith(_WK):
        iss = iss[: -len(_WK)].rstrip("/")
    return iss


def _discover(issuer: str) -> dict:
    url = _issuer_base(issuer) + _WK
    r = httpx.get(url, headers=_UA, timeout=10)
    r.raise_for_status()
    return r.json()


# ---- signing-key verification (shared by the SSO callback + bearer tokens) ----
# Per-process caches (one dict per uvicorn worker — no Redis). Discovery docs
# and JWKS are cached for _KEY_TTL; an unknown `kid` forces ONE refetch so key
# rotation at the IdP heals without a restart. Forced refetches are throttled
# per jwks_uri so junk tokens with random kids can't hammer the IdP.
_KEY_TTL = 600
_FORCE_MIN_GAP = 30
_disc_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, list]] = {}
_jwks_forced: dict[str, float] = {}
_cache_lock = threading.Lock()


def _discover_cached(issuer: str) -> dict:
    key = _issuer_base(issuer)
    now = time.monotonic()
    with _cache_lock:
        hit = _disc_cache.get(key)
    if hit and now - hit[0] < _KEY_TTL:
        return hit[1]
    disc = _discover(issuer)
    with _cache_lock:
        _disc_cache[key] = (now, disc)
    return disc


def _fetch_jwks(jwks_uri: str) -> list:
    # Fetch JWKS ourselves via httpx (NOT PyJWKClient, which uses urllib with
    # a "Python-urllib" User-Agent that a WAF / reverse proxy in front of the
    # IdP often blocks with 403). A normal User-Agent gets through.
    jr = httpx.get(jwks_uri, headers=_UA, timeout=10)
    jr.raise_for_status()
    return jr.json().get("keys", [])


def _jwks(jwks_uri: str, force: bool = False) -> list:
    now = time.monotonic()
    with _cache_lock:
        hit = _jwks_cache.get(jwks_uri)
        if force:
            if now - _jwks_forced.get(jwks_uri, -1e9) < _FORCE_MIN_GAP:
                return hit[1] if hit else []
            _jwks_forced[jwks_uri] = now
    if hit and not force and now - hit[0] < _KEY_TTL:
        return hit[1]
    keys = _fetch_jwks(jwks_uri)
    with _cache_lock:
        _jwks_cache[jwks_uri] = (now, keys)
    return keys


def _decode_signed(token: str, issuer: str, jwks_uri: str, *, strict_kid: bool) -> dict:
    """Verify signature + expiry + issuer and return the claims. Raises
    OidcError / jwt.PyJWTError on failure.

    strict_kid=False keeps the SSO callback's legacy behaviour (fall back to the
    first key when the kid doesn't match). Bearer tokens use strict_kid=True:
    unknown kid → refetch once → reject."""
    kid = jwt.get_unverified_header(token).get("kid")
    keys = _jwks(jwks_uri)
    jwk = next((k for k in keys if k.get("kid") == kid), None)
    if jwk is None and strict_kid:
        keys = _jwks(jwks_uri, force=True)
        jwk = next((k for k in keys if k.get("kid") == kid), None)
    if jwk is None and not strict_kid:
        jwk = keys[0] if keys else None
    if jwk is None:
        raise OidcError("no matching signing key in provider JWKS")
    signing_key = jwt.PyJWK(jwk).key
    # Keycloak's `aud` is frequently "account" (or a list that omits the
    # client), while the client id lives in `azp`. So we DON'T let PyJWT
    # enforce audience — callers check the client id in `aud`/`azp` themselves.
    return jwt.decode(
        token, signing_key, algorithms=["RS256", "ES256"],
        issuer=_issuer_base(issuer), leeway=30,
        options={"verify_at_hash": False, "verify_aud": False},
    )


def _client_ids(raw) -> list[str]:
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def bearer_settings() -> dict:
    """Effective bearer-token settings. The Settings → Authentication value wins
    once saved; until then (None) the OIDC_BEARER_* env vars apply."""
    c = store.get_config()
    en, ids, ac = c.get("bearer_enabled"), c.get("bearer_client_ids"), c.get("bearer_auto_create")
    return {
        "enabled": OIDC_BEARER_ENABLED if en is None else bool(en),
        "client_ids": _client_ids(OIDC_BEARER_CLIENT_IDS if ids is None else ids),
        "auto_create": OIDC_BEARER_AUTO_CREATE if ac is None else bool(ac),
        "source": "env" if en is None else "settings",
    }


def verify_bearer(token: str, skip_client_check: bool = False) -> dict | None:
    """Verify an IdP-issued ACCESS token (e.g. the Keycloak token OpenWebUI
    forwards). Returns the claims plus `_issuer` / `_client_id`, or None.
    skip_client_check=True when the caller already proved which app it is with
    a valid app API key — signature, issuer, expiry and typ are still enforced.
    Never raises; never logs or returns the token."""
    try:
        cfg = bearer_settings()
        if not cfg["enabled"] or not token or token.count(".") != 2:
            return None
        iss = (jwt.decode(token, options={"verify_signature": False}).get("iss") or "").rstrip("/")
        if not iss:
            return None
        for p in store.oidc_providers():
            if not p.get("enabled") or not p.get("issuer"):
                continue
            if _issuer_base(p["issuer"]) != iss:
                continue
            try:
                disc = _discover_cached(p["issuer"])
                claims = _decode_signed(token, p["issuer"], disc["jwks_uri"], strict_kid=True)
            except Exception as e:
                print(f"[oidc-bearer] rejected ({type(e).__name__})", flush=True)
                continue
            # an id_token also carries azp=client — only accept access tokens
            typ = claims.get("typ")
            if typ and str(typ).lower() != "bearer":
                print("[oidc-bearer] rejected (not an access token)", flush=True)
                continue
            allowed = cfg["client_ids"] or _client_ids([p.get("client_id")])
            aud = claims.get("aud")
            aud_list = aud if isinstance(aud, list) else [aud]
            azp = claims.get("azp")
            match = azp if azp in allowed else next((a for a in aud_list if a in allowed), None)
            if skip_client_check:
                match = match or azp or (aud_list[0] if aud_list else None)
            if not match:
                print("[oidc-bearer] rejected (client not allow-listed)", flush=True)
                continue
            claims["_issuer"] = iss
            claims["_client_id"] = match
            return claims
    except Exception as e:
        print(f"[oidc-bearer] rejected ({type(e).__name__})", flush=True)
    return None


def redirect_uri(public_url: str | None = None) -> str:
    # Prefer an explicitly-configured PUBLIC_URL (like Open WebUI's
    # OPENID_REDIRECT_URI) so the redirect_uri is deterministic and identical
    # between the auth request, the token exchange, and the value registered in
    # Keycloak — even behind a reverse proxy that rewrites scheme/host. Only
    # fall back to the request-derived base when PUBLIC_URL isn't set.
    # Trust the caller's resolved base first (it already prefers the UI config
    # value, then PUBLIC_URL, then proxy headers). Env PUBLIC_URL is the fallback.
    base = (public_url or PUBLIC_URL or "").rstrip("/")
    return f"{base}/api/auth/oidc/callback"


def auth_url(provider: dict, public_url: str | None = None) -> str:
    """`provider` is one SSO provider dict (id, issuer, client_id, ...)."""
    oc = provider
    if not oc.get("issuer") or not oc.get("client_id"):
        raise OidcError("OIDC not configured")
    disc = _discover(oc["issuer"])
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    _state_put(state, str(oc.get("id") or ""), nonce)
    params = {
        "client_id": oc["client_id"],
        "response_type": "code",
        "scope": oc.get("scopes", "openid email profile"),
        "redirect_uri": redirect_uri(public_url),
        "state": state,
        "nonce": nonce,
    }
    return disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def exchange(provider: dict, code: str, state: str, public_url: str | None = None,
             expected_nonce: str | None = None) -> dict:
    """Returns {email, name, sub}. Verifies state + id_token signature + nonce.
    `provider` MUST be the same provider the state was issued for (caller resolves
    it from the pid returned by consume_state). `expected_nonce` is the nonce
    bound to this auth attempt; the id_token's `nonce` claim must equal it."""
    oc = provider
    disc = _discover(oc["issuer"])

    tok = httpx.post(disc["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(public_url),
        "client_id": oc["client_id"],
        "client_secret": oc.get("client_secret", ""),
    }, headers={"User-Agent": _UA["User-Agent"]}, timeout=10)
    if tok.status_code != 200:
        raise OidcError(f"token exchange failed: {tok.text[:200]}")
    tok_json = tok.json()
    access_token = tok_json.get("access_token")
    id_token = tok_json.get("id_token")
    if not id_token:
        raise OidcError("no id_token in response")

    # verify signature against JWKS. Any failure (JWKS unreachable, bad
    # signature, expired, decode error) becomes an OidcError so the callback
    # redirects to /login with a message instead of a raw 500.
    try:
        claims = _decode_signed(id_token, oc["issuer"], disc["jwks_uri"], strict_kid=False)
    except OidcError:
        raise
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token verify failed: {e}")
    except Exception as e:  # JWKS fetch / network / anything else
        raise OidcError(f"id_token verify error: {e}")
    aud = claims.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud]
    cid = oc["client_id"]
    if cid not in aud_list and claims.get("azp") != cid:
        raise OidcError("id_token audience does not match client id")
    # nonce replay protection: the id_token nonce MUST match the one we bound to
    # this auth attempt. Only enforced when a nonce was issued (legacy in-flight
    # states created before the nonce column stay tolerant).
    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise OidcError("id_token nonce mismatch")
    # id_token claims first; fall back to the /userinfo endpoint if email is
    # missing (some Keycloak clients don't map email into the id_token but do
    # return it from userinfo). This mirrors Open WebUI's behaviour. userinfo
    # values fill gaps; id_token claims are kept where userinfo omits them.
    if not claims.get("email") and disc.get("userinfo_endpoint") and access_token:
        try:
            ur = httpx.get(disc["userinfo_endpoint"],
                           headers={**_UA, "Authorization": f"Bearer {access_token}"},
                           timeout=10)
            if ur.status_code == 200:
                info = ur.json()
                for k, v in info.items():
                    claims.setdefault(k, v)
        except Exception as e:
            print(f"[oidc] userinfo fallback failed: {e!r}")

    email = claims.get("email")
    if not email:
        raise OidcError("no email claim in id_token or userinfo")
    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    # email_verified may arrive as bool or the string "true" depending on the IdP
    ev = claims.get("email_verified")
    email_verified = ev is True or str(ev).lower() == "true"
    return {"email": email, "name": name, "sub": claims.get("sub", ""), "email_verified": email_verified}
