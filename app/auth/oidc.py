"""OIDC / SSO (Keycloak, Azure AD, Google) — authorization-code flow.
Discovery via /.well-known, id_token verified against the provider JWKS."""
import secrets
import urllib.parse

import httpx
import jwt
from jwt import PyJWKClient

from ..config import PUBLIC_URL
from ..db import get_conn

# CSRF state lives in Postgres (oidc_state table), not an in-process dict, so it
# survives across uvicorn workers — login can land on a different worker than the
# callback. Consumed one-shot; expired rows (>10 min) pruned lazily on each use.
_STATE_TTL_MIN = 10


def _state_put(state: str, pid: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO oidc_state (state, pid) VALUES (%s, %s) "
                     "ON CONFLICT (state) DO NOTHING", (state, pid))


def _state_consume(state: str):
    """Atomically take a fresh, unexpired state (one-shot). Returns (ok, pid)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM oidc_state WHERE created_at < now() - make_interval(mins => %s)",
                     (_STATE_TTL_MIN,))
        row = conn.execute(
            "DELETE FROM oidc_state WHERE state = %s "
            "AND created_at >= now() - make_interval(mins => %s) RETURNING pid",
            (state, _STATE_TTL_MIN),
        ).fetchone()
    return (bool(row), (row.get("pid") if row else None))


class OidcError(Exception):
    pass


def consume_state(state: str):
    """Public one-shot state check for the callback route. Returns (ok, pid)."""
    return _state_consume(state)


def _discover(issuer: str) -> dict:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def redirect_uri(public_url: str | None = None) -> str:
    base = (public_url or PUBLIC_URL or "").rstrip("/")
    return f"{base}/api/auth/oidc/callback"


def auth_url(provider: dict, public_url: str | None = None) -> str:
    """`provider` is one SSO provider dict (id, issuer, client_id, ...)."""
    oc = provider
    if not oc.get("issuer") or not oc.get("client_id"):
        raise OidcError("OIDC not configured")
    disc = _discover(oc["issuer"])
    state = secrets.token_urlsafe(24)
    _state_put(state, str(oc.get("id") or ""))
    params = {
        "client_id": oc["client_id"],
        "response_type": "code",
        "scope": oc.get("scopes", "openid email profile"),
        "redirect_uri": redirect_uri(public_url),
        "state": state,
    }
    return disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def exchange(provider: dict, code: str, state: str, public_url: str | None = None) -> dict:
    """Returns {email, name, sub}. Verifies state + id_token signature.
    `provider` MUST be the same provider the state was issued for (caller resolves
    it from the pid returned by consume_state)."""
    oc = provider
    disc = _discover(oc["issuer"])

    tok = httpx.post(disc["token_endpoint"], data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(public_url),
        "client_id": oc["client_id"],
        "client_secret": oc.get("client_secret", ""),
    }, timeout=10)
    if tok.status_code != 200:
        raise OidcError(f"token exchange failed: {tok.text[:200]}")
    id_token = tok.json().get("id_token")
    if not id_token:
        raise OidcError("no id_token in response")

    # verify signature against JWKS. Any failure (JWKS unreachable, bad
    # signature, expired, decode error) becomes an OidcError so the callback
    # redirects to /login with a message instead of a raw 500.
    try:
        jwks = PyJWKClient(disc["jwks_uri"])
        signing_key = jwks.get_signing_key_from_jwt(id_token).key
        # Keycloak's id_token `aud` is frequently "account" (or a list that omits
        # the client), while the client id lives in `azp`. So we DON'T let PyJWT
        # enforce audience — we verify the signature + issuer, then check the
        # client id appears in either `aud` or `azp` ourselves.
        claims = jwt.decode(
            id_token, signing_key, algorithms=["RS256", "ES256"],
            issuer=oc["issuer"].rstrip("/"),
            options={"verify_at_hash": False, "verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise OidcError(f"id_token verify failed: {e}")
    except Exception as e:  # JWKS fetch / network / anything else
        raise OidcError(f"id_token verify error: {e}")
    aud = claims.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud]
    cid = oc["client_id"]
    if cid not in aud_list and claims.get("azp") != cid:
        raise OidcError("id_token audience does not match client id")
    email = claims.get("email")
    if not email:
        raise OidcError("id_token has no email claim")
    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    # email_verified may arrive as bool or the string "true" depending on the IdP
    ev = claims.get("email_verified")
    email_verified = ev is True or str(ev).lower() == "true"
    return {"email": email, "name": name, "sub": claims.get("sub", ""), "email_verified": email_verified}
