"""IdP bearer-token auth (Keycloak access token forwarded by e.g. an OpenWebUI pipe).

Fully offline: a throwaway RSA key + fake JWKS/discovery, and the store /
audit-log calls monkeypatched — no network, no Keycloak, no database.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.auth import deps, oidc, security, store
from app import security_log

ISS = "https://kc.example.test/realms/city-group"
JWKS_URI = ISS + "/protocol/openid-connect/certs"
CLIENT = "citygpt-openwebui"


def _key(kid):
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return k, jwk


KEY_A, JWK_A = _key("kid-a")
KEY_B, JWK_B = _key("kid-b")      # rotated-in key
KEY_X, _ = _key("kid-x")          # never published

USERS = {
    "alice@city.test": {"id": 11, "email": "alice@city.test", "role": "user", "active": True},
    "pat@city.test": {"id": 12, "email": "pat@city.test", "role": "pending", "active": True},
    "gone@city.test": {"id": 13, "email": "gone@city.test", "role": "user", "active": False},
    "vis@city.test": {"id": 14, "email": "vis@city.test", "role": "widget", "active": True},
}
BY_ID = {u["id"]: u for u in USERS.values()}


@pytest.fixture
def env(monkeypatch):
    """Fresh caches + patched IdP and store for every test."""
    state = {"jwks": [JWK_A], "fetches": 0, "cfg": {"bearer_enabled": True,
             "bearer_client_ids": CLIENT}, "events": [], "methods": [], "touched": []}

    oidc._disc_cache.clear(); oidc._jwks_cache.clear(); oidc._jwks_forced.clear()
    deps._noted.clear()

    def fake_fetch(uri):
        assert uri == JWKS_URI
        state["fetches"] += 1
        return list(state["jwks"])

    monkeypatch.setattr(oidc, "_fetch_jwks", fake_fetch)
    monkeypatch.setattr(oidc, "_discover", lambda iss: {"issuer": ISS, "jwks_uri": JWKS_URI})
    monkeypatch.setattr(store, "get_config", lambda: dict(state["cfg"]))
    monkeypatch.setattr(store, "oidc_providers", lambda: [
        {"id": "kc", "issuer": ISS, "client_id": "CityAgent-ITSM-ARIA", "enabled": True}])
    monkeypatch.setattr(store, "get_by_email", lambda e: USERS.get(e.lower()))
    monkeypatch.setattr(store, "get_by_id", lambda i: BY_ID.get(i))
    monkeypatch.setattr(store, "record_auth_method", lambda uid, m: state["methods"].append((uid, m)))
    monkeypatch.setattr(store, "touch_login", lambda uid: state["touched"].append(uid))
    monkeypatch.setattr(security_log, "log_event",
                        lambda ev, email=None, actor_email=None, meta=None: state["events"].append((ev, email, meta)))
    return state


def _tok(key=KEY_A, kid="kid-a", **over):
    now = int(time.time())
    claims = {"iss": ISS, "sub": "kc-uuid", "aud": "account", "azp": CLIENT, "typ": "Bearer",
              "email": "alice@city.test", "email_verified": True, "iat": now, "exp": now + 300}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _status(fn, token):
    try:
        fn(f"Bearer {token}")
        return 200
    except HTTPException as e:
        return e.status_code


# 1
def test_valid_access_token_maps_to_user(env):
    u = deps.current_principal(f"Bearer {_tok()}")
    assert u["id"] == 11 and u["email"] == "alice@city.test"
    assert deps.current_user(f"Bearer {_tok()}")["id"] == 11
    assert env["methods"] == [(11, "oidc-bearer")]
    ev, email, meta = env["events"][0]
    assert ev == "bearer_ok" and email == "alice@city.test"
    assert meta == {"issuer": ISS, "client_id": CLIENT}


# 2 — the regression that matters most
def test_hs256_member_token_unchanged(env, monkeypatch):
    called = []
    monkeypatch.setattr(oidc, "verify_bearer", lambda t: called.append(t))
    tok = security.make_token(USERS["alice@city.test"])
    assert deps.current_principal(f"Bearer {tok}")["id"] == 11
    assert deps.current_user(f"Bearer {tok}")["id"] == 11
    assert called == []          # bearer path never consulted for our own tokens
    assert _status(deps.current_user, security.make_token(USERS["pat@city.test"])) == 403


# 3
def test_widget_token_unchanged(env, monkeypatch):
    import app.embed as embed
    monkeypatch.setattr(embed, "get_key", lambda kid: {"id": kid, "active": True, "rate_per_min": 60})
    monkeypatch.setattr(embed, "check_rate", lambda *a, **k: None)
    monkeypatch.setattr(embed, "check_caps", lambda *a, **k: None)
    tok = security.make_widget_token(14, 5, "vid-1", "Visitor")
    u = deps.current_principal(f"Bearer {tok}")
    assert u["_widget"] is True and u["_embed_key_id"] == 5


# 4
def test_wrong_issuer(env):
    assert _status(deps.current_principal, _tok(iss="https://evil.test/realms/city-group")) == 401


# 5
def test_client_not_allow_listed(env):
    assert _status(deps.current_principal, _tok(azp="some-other-client")) == 401


def test_allow_list_blank_falls_back_to_provider_client(env):
    env["cfg"]["bearer_client_ids"] = ""
    assert _status(deps.current_principal, _tok()) == 401
    assert _status(deps.current_principal, _tok(azp="CityAgent-ITSM-ARIA")) == 200


def test_client_in_aud_list_accepted(env):
    assert _status(deps.current_principal, _tok(azp="x", aud=["account", CLIENT])) == 200


# 6
def test_unknown_signing_key(env):
    assert _status(deps.current_principal, _tok(key=KEY_X, kid="kid-x")) == 401
    # a key the IdP doesn't publish, reusing a published kid — signature check fails
    assert _status(deps.current_principal, _tok(key=KEY_X, kid="kid-a")) == 401


# 7
def test_expired(env):
    past = int(time.time()) - 3600
    assert _status(deps.current_principal, _tok(iat=past - 300, exp=past)) == 401


# 8
def test_no_email_claim(env):
    assert _status(deps.current_principal, _tok(email=None)) == 401


def test_unverified_email_accepted_like_sso_login(env):
    # Office 365-brokered Keycloak users carry email_verified=false; Aria's own
    # SSO login accepts them, so the bearer path must too (2.26.1).
    assert _status(deps.current_principal, _tok(email_verified=False)) == 200


# 9
def test_unknown_email_is_403_and_not_created(env):
    assert _status(deps.current_principal, _tok(email="nobody@city.test")) == 403
    assert "nobody@city.test" not in USERS
    assert env["events"][0][0] == "bearer_no_account"


def test_inactive_and_widget_rows_are_403(env):
    assert _status(deps.current_principal, _tok(email="gone@city.test")) == 403
    assert _status(deps.current_principal, _tok(email="vis@city.test")) == 403


# 10
def test_pending_user_is_403(env):
    try:
        deps.current_principal(f"Bearer {_tok(email='pat@city.test')}")
        assert False, "expected 403"
    except HTTPException as e:
        assert e.status_code == 403 and e.detail == "account awaiting admin approval"


# 11
def test_disabled_flag_rejects_valid_token(env):
    env["cfg"]["bearer_enabled"] = False
    assert _status(deps.current_principal, _tok()) == 401
    assert env["fetches"] == 0


def test_env_applies_when_setting_unset(env, monkeypatch):
    env["cfg"] = {"bearer_enabled": None, "bearer_client_ids": None}
    monkeypatch.setattr(oidc, "OIDC_BEARER_ENABLED", True)
    monkeypatch.setattr(oidc, "OIDC_BEARER_CLIENT_IDS", f" {CLIENT} , other ")
    s = oidc.bearer_settings()
    assert s == {"enabled": True, "client_ids": [CLIENT, "other"], "auto_create": False, "source": "env"}
    assert _status(deps.current_principal, _tok()) == 200


# 12
def test_jwks_cached_then_refetched_once_on_rotation(env):
    for _ in range(4):
        assert _status(deps.current_principal, _tok()) == 200
    assert env["fetches"] == 1                       # cached inside the TTL
    env["jwks"] = [JWK_A, JWK_B]                     # IdP rotates in a new key
    assert _status(deps.current_principal, _tok(key=KEY_B, kid="kid-b")) == 200
    assert env["fetches"] == 2                       # exactly one forced refetch
    assert _status(deps.current_principal, _tok(key=KEY_B, kid="kid-b")) == 200
    assert env["fetches"] == 2


def test_forced_refetch_is_throttled(env):
    for _ in range(5):
        assert _status(deps.current_principal, _tok(key=KEY_X, kid="kid-x")) == 401
    assert env["fetches"] == 2                       # initial + one forced, not 6


# extras
def test_id_token_rejected(env):
    assert _status(deps.current_principal, _tok(typ="ID")) == 401


def test_bookkeeping_throttled(env):
    for _ in range(5):
        deps.current_principal(f"Bearer {_tok()}")
    assert env["methods"] == [(11, "oidc-bearer")]
    assert env["touched"] == [11]
    assert [e[0] for e in env["events"]] == ["bearer_ok"]


def test_token_never_logged(env, capsys):
    bad = _tok(azp="some-other-client")
    _status(deps.current_principal, bad)
    _status(deps.current_principal, _tok(key=KEY_X, kid="kid-x"))
    out = capsys.readouterr().out
    assert bad not in out and bad.split(".")[1] not in out
    assert all(bad not in repr(e) for e in env["events"])


def test_garbage_never_raises(env):
    for t in ["", "x", "a.b.c", "not-a-jwt", None]:
        assert oidc.verify_bearer(t) is None


# ---- app API keys (2.27.0): key identifies the app, token identifies the user ----
@pytest.fixture
def keys(env, monkeypatch):
    from app.auth import app_keys
    good = {"id": 7, "name": "CityGPT Global"}
    touched = []
    monkeypatch.setattr(app_keys, "verify", lambda k: good if k == "ak_live_GOOD" else None)
    monkeypatch.setattr(app_keys, "touch", lambda kid: touched.append(kid))
    env["touched_keys"] = touched
    return env


def _call(token, key=None):
    try:
        deps.current_principal(f"Bearer {token}", key)
        return 200
    except HTTPException as e:
        return e.status_code


def test_app_key_replaces_client_allow_list(keys):
    # client not on the allow-list, but a valid app key vouches for the app
    assert _call(_tok(azp="Dev-CityGPT"), "ak_live_GOOD") == 200
    assert keys["touched_keys"] == [7]
    assert keys["events"][-1][2]["app_key"] == "CityGPT Global"


def test_app_key_still_needs_valid_user_token(keys):
    assert _call(_tok(key=KEY_X, kid="kid-x"), "ak_live_GOOD") == 401      # bad signature
    assert _call(_tok(iss="https://evil.test/realms/x"), "ak_live_GOOD") == 401
    assert _call(_tok(typ="ID"), "ak_live_GOOD") == 401
    assert _call(_tok(email="nobody@city.test"), "ak_live_GOOD") == 403


def test_bad_or_revoked_app_key_fails_closed(keys):
    # even an allow-listed client is refused when it presents a wrong key
    assert _call(_tok(), "ak_live_WRONG") == 401


def test_no_key_keeps_allow_list_path(keys):
    assert _call(_tok()) == 200
    assert _call(_tok(azp="Dev-CityGPT")) == 401


def test_app_key_needs_master_switch(keys):
    keys["cfg"]["bearer_enabled"] = False
    assert _call(_tok(azp="Dev-CityGPT"), "ak_live_GOOD") == 401


def test_hs256_ignores_app_key_header(keys):
    tok = security.make_token(USERS["alice@city.test"])
    assert _call(tok, "ak_live_WRONG") == 200


def test_require_admin_direct_call_with_bearer(keys):
    # require_admin calls current_user() positionally: the app-key param is then
    # a FastAPI Header default, not a str — must not crash (was a 500 in dev)
    try:
        deps.require_admin(f"Bearer {_tok()}")
        assert False, "expected 403"
    except HTTPException as e:
        assert e.status_code == 403


# ---- create the account on first use (2.28.0) ----
@pytest.fixture
def jit(env, monkeypatch):
    made = []
    def fake_create(email, name, sub=None):
        role = env["cfg"].get("default_role", "user")
        u = {"id": 99, "email": email, "role": role if role in ("user", "pending") else "user", "active": True}
        made.append((email, name, sub))
        USERS[email] = u; BY_ID[99] = u
        return u
    monkeypatch.setattr(store, "create_for_bearer", fake_create)
    env["made"] = made
    yield env
    USERS.pop("newbie@city.test", None); BY_ID.pop(99, None)


def test_unknown_user_still_403_when_auto_create_off(jit):
    assert _status(deps.current_principal, _tok(email="newbie@city.test")) == 403
    assert jit["made"] == []


def test_first_use_creates_account_and_answers(jit):
    jit["cfg"]["bearer_auto_create"] = True
    u = deps.current_principal(f"Bearer {_tok(email='Newbie@City.test', name='New Bie')}")
    assert u["email"] == "newbie@city.test" and u["role"] == "user"
    assert jit["made"] == [("newbie@city.test", "New Bie", "kc-uuid")]
    assert [e[0] for e in jit["events"]] == ["bearer_user_created", "bearer_ok"]
    # second call finds the account — no second create
    deps.current_principal(f"Bearer {_tok(email='newbie@city.test')}")
    assert len(jit["made"]) == 1


def test_first_use_pending_role_waits_for_approval(jit):
    jit["cfg"].update(bearer_auto_create=True, default_role="pending")
    assert _status(deps.current_principal, _tok(email="newbie@city.test")) == 403


def test_auto_create_never_resurrects_inactive_or_widget(jit):
    jit["cfg"]["bearer_auto_create"] = True
    assert _status(deps.current_principal, _tok(email="gone@city.test")) == 403
    assert _status(deps.current_principal, _tok(email="vis@city.test")) == 403
    assert jit["made"] == []


def test_auto_create_failure_is_403_not_500(jit, monkeypatch):
    jit["cfg"]["bearer_auto_create"] = True
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "create_for_bearer", boom)
    assert _status(deps.current_principal, _tok(email="newbie@city.test")) == 403


def test_auto_create_needs_a_valid_token(jit):
    jit["cfg"]["bearer_auto_create"] = True
    assert _status(deps.current_principal, _tok(email="newbie@city.test", azp="other-app")) == 401
    assert _status(deps.current_principal, _tok(key=KEY_X, kid="kid-x", email="newbie@city.test")) == 401
    assert jit["made"] == []
