"""Auth primitives: widget token round-trip + password hashing."""
from app.auth import security


def test_widget_token_round_trip():
    tok = security.make_widget_token(1, 5, "vid-1", "Bob")
    p = security.decode_token(tok)
    assert p is not None
    assert p["typ"] == "widget"
    assert p["kid"] == 5
    assert p["vid"] == "vid-1"
    assert str(p["sub"]) == "1"


def test_decode_rejects_garbage():
    assert security.decode_token("not.a.jwt") is None


def test_password_hash_round_trip():
    h = security.hash_password("secret123")
    assert h != "secret123"
    assert security.verify_password("secret123", h)
    assert not security.verify_password("wrong", h)
