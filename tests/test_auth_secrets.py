"""SSO client secrets / LDAP bind passwords never leave the server (2.29.1)."""
from app.auth import router, store

STORED = {
    "oidc_providers": [{"id": "kc", "issuer": "https://iam/x", "client_id": "a", "client_secret": "S3CR3T"}],
    "ldap_directories": [{"id": "ad", "host": "ldap", "bind_password": "B1ND"}],
    "oidc": {"issuer": "", "client_secret": "LEGACY"},
    "ldap": {"host": "", "bind_password": ""},
}


def test_mask_hides_values_and_flags_them():
    m = router._mask_secrets(STORED)
    assert m["oidc_providers"][0]["client_secret"] == "" and m["oidc_providers"][0]["has_secret"] is True
    assert m["ldap_directories"][0]["bind_password"] == "" and m["ldap_directories"][0]["has_secret"] is True
    assert m["oidc"]["client_secret"] == "" and m["oidc"]["has_secret"] is True
    assert m["ldap"]["has_secret"] is False
    assert "S3CR3T" not in str(m) and "B1ND" not in str(m) and "LEGACY" not in str(m)
    assert STORED["oidc_providers"][0]["client_secret"] == "S3CR3T"      # original untouched


def test_blank_keeps_stored_and_new_value_replaces(monkeypatch):
    monkeypatch.setattr(store, "oidc_providers", lambda: STORED["oidc_providers"])
    monkeypatch.setattr(store, "ldap_directories", lambda: STORED["ldap_directories"])
    body = router._mask_secrets(STORED)                                   # what the UI sends back
    body["ldap_directories"][0]["bind_password"] = "NEWPW"
    out = router._keep_secrets(body, STORED)
    assert out["oidc_providers"][0]["client_secret"] == "S3CR3T"          # blank → kept
    assert out["ldap_directories"][0]["bind_password"] == "NEWPW"         # typed → replaced
    assert out["oidc"]["client_secret"] == "LEGACY"
    assert "has_secret" not in out["oidc_providers"][0]


def test_legacy_provider_migrated_by_ui_keeps_secret(monkeypatch):
    stored = {"oidc": {"issuer": "https://iam/x", "client_id": "a", "client_secret": "OLD"}}
    monkeypatch.setattr(store, "oidc_providers", lambda: [{"id": "default", "client_secret": "OLD"}])
    monkeypatch.setattr(store, "ldap_directories", lambda: [])
    body = {"oidc_providers": [{"id": "default", "client_secret": "", "has_secret": True}]}
    assert router._keep_secrets(body, stored)["oidc_providers"][0]["client_secret"] == "OLD"


def test_new_provider_without_secret_stays_blank(monkeypatch):
    monkeypatch.setattr(store, "oidc_providers", lambda: [])
    monkeypatch.setattr(store, "ldap_directories", lambda: [])
    body = {"oidc_providers": [{"id": "new1", "client_secret": ""}]}
    assert router._keep_secrets(body, {})["oidc_providers"][0]["client_secret"] == ""
