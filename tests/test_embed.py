"""Embed widget: origin gate, rate limit, daily caps."""
import pytest
from fastapi import HTTPException

from app import embed


def test_origin_allow_list():
    key = {"allowed_origins": ["https://x.com"]}
    assert embed.origin_allowed(key, "https://x.com")
    assert not embed.origin_allowed(key, "https://evil.com")
    # empty list = allow any (dev convenience)
    assert embed.origin_allowed({"allowed_origins": []}, "https://anything.com")


def test_key_create_rate_and_caps(db):
    k = embed.create_key("pytest-embed", [], created_by=None,
                         rate_per_min=2, daily_msg_cap=2)
    kid = k["id"]
    try:
        assert k["public_key"].startswith("pk_live_")
        assert k["secret"].startswith("sk_live_")

        # rate limit: first 2 ok, 3rd → 429
        embed.check_rate(kid, "v1", 2)
        embed.check_rate(kid, "v1", 2)
        with pytest.raises(HTTPException) as e:
            embed.check_rate(kid, "v1", 2)
        assert e.value.status_code == 429

        # daily message cap: bump to the cap, then check_caps blocks
        full = embed.get_key(kid)
        embed.bump_message(kid)
        embed.bump_message(kid)
        with pytest.raises(HTTPException) as e2:
            embed.check_caps(full)
        assert e2.value.status_code == 429
    finally:
        embed.delete_key(kid)
        with db() as c:
            c.execute("DELETE FROM embed_rate WHERE key_id=%s", (kid,))
            c.execute("DELETE FROM embed_usage WHERE key_id=%s", (kid,))
            c.execute("DELETE FROM users WHERE id=%s", (k["anon_user_id"],))
