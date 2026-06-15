"""OIDC / SSO (Keycloak, Azure AD, Google) — authorization-code flow.
Discovery via /.well-known, id_token verified against the provider JWKS."""
import secrets
import urllib.parse

import httpx
import jwt
from jwt import PyJWKClient

from ..config import PUBLIC_URL

# in-memory state store (CSRF) — fine single-process; move to Redis at scale
_STATES: dict[str, float] = {}


class OidcError(Exception):
    pass


def _discover(issuer: str) -> dict:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    r = httpx.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


def redirect_uri(public_url: str | None = None) -> str:
    base = (public_url or PUBLIC_URL or "").rstrip("/")
    return f"{base}/api/auth/oidc/callback"


def auth_url(cfg: dict, public_url: str | None = None) -> str:
    oc = cfg["oidc"]
    if not oc.get("issuer") or not oc.get("client_id"):
        raise OidcError("OIDC not configured")
    disc = _discover(oc["issuer"])
    state = secrets.token_urlsafe(24)
    _STATES[state] = 1
    params = {
        "client_id": oc["client_id"],
        "response_type": "code",
        "scope": oc.get("scopes", "openid email profile"),
        "redirect_uri": redirect_uri(public_url),
        "state": state,
    }
    return disc["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def exchange(cfg: dict, code: str, state: str, public_url: str | None = None) -> dict:
    """Returns {email, name, sub}. Verifies state + id_token signature."""
    if state not in _STATES:
        raise OidcError("invalid state")
    _STATES.pop(state, None)

    oc = cfg["oidc"]
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

    # verify signature against JWKS
    jwks = PyJWKClient(disc["jwks_uri"])
    signing_key = jwks.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(
        id_token, signing_key, algorithms=["RS256", "ES256"],
        audience=oc["client_id"], options={"verify_at_hash": False},
    )
    email = claims.get("email")
    if not email:
        raise OidcError("id_token has no email claim")
    name = claims.get("name") or claims.get("preferred_username") or email.split("@")[0]
    # email_verified may arrive as bool or the string "true" depending on the IdP
    ev = claims.get("email_verified")
    email_verified = ev is True or str(ev).lower() == "true"
    return {"email": email, "name": name, "sub": claims.get("sub", ""), "email_verified": email_verified}
