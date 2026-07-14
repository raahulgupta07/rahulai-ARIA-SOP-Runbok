"""OIDC / SSO (Keycloak, Azure AD, Google) — authorization-code flow.
Discovery via /.well-known, id_token verified against the provider JWKS."""
import secrets
import urllib.parse

import httpx
import jwt

from ..config import PUBLIC_URL
from ..db import get_conn

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


def redirect_uri(public_url: str | None = None) -> str:
    # Prefer an explicitly-configured PUBLIC_URL (like Open WebUI's
    # OPENID_REDIRECT_URI) so the redirect_uri is deterministic and identical
    # between the auth request, the token exchange, and the value registered in
    # Keycloak — even behind a reverse proxy that rewrites scheme/host. Only
    # fall back to the request-derived base when PUBLIC_URL isn't set.
    base = (PUBLIC_URL or public_url or "").rstrip("/")
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
        # Fetch JWKS ourselves via httpx (NOT PyJWKClient, which uses urllib with
        # a "Python-urllib" User-Agent that a WAF / reverse proxy in front of the
        # IdP often blocks with 403). A normal User-Agent gets through.
        jr = httpx.get(disc["jwks_uri"], headers=_UA, timeout=10)
        jr.raise_for_status()
        keys = jr.json().get("keys", [])
        kid = jwt.get_unverified_header(id_token).get("kid")
        jwk = next((k for k in keys if k.get("kid") == kid), None) or (keys[0] if keys else None)
        if jwk is None:
            raise OidcError("no signing key in provider JWKS")
        signing_key = jwt.PyJWK(jwk).key
        # Keycloak's id_token `aud` is frequently "account" (or a list that omits
        # the client), while the client id lives in `azp`. So we DON'T let PyJWT
        # enforce audience — we verify the signature + issuer, then check the
        # client id appears in either `aud` or `azp` ourselves.
        claims = jwt.decode(
            id_token, signing_key, algorithms=["RS256", "ES256"],
            issuer=_issuer_base(oc["issuer"]),
            options={"verify_at_hash": False, "verify_aud": False},
        )
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
