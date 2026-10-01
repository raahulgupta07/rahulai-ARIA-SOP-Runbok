"""channel_usage shaping — DB-free tests of label / share / trend logic."""
import datetime

from app.productivity import channel_label, shape_channels


def test_labels():
    assert channel_label("web") == "Aria web"
    assert channel_label(None) == "Aria web"
    assert channel_label("") == "Aria web"
    assert channel_label("widget") == "Embed widget"
    assert channel_label("app:CityGPT Global") == "CityGPT Global"
    assert channel_label("app:Global-CityGPT") == "Global-CityGPT"
    assert channel_label("app:") == "Connected app"


def test_shares_sorted_and_null_folds_into_web():
    rows = [
        {"channel": "web", "questions": 50, "people": 10},
        {"channel": None, "questions": 10, "people": 2},
        {"channel": "widget", "questions": 15, "people": 5},
        {"channel": "app:CityGPT Global", "questions": 25, "people": 7},
    ]
    out = shape_channels(rows, new_accounts=3)
    assert out["total"] == 100
    assert [c["channel"] for c in out["channels"]] == ["web", "app:CityGPT Global", "widget"]
    web = out["channels"][0]
    assert web["questions"] == 60 and web["share_pct"] == 60 and web["label"] == "Aria web"
    assert out["channels"][1]["label"] == "CityGPT Global"
    assert out["channels"][1]["share_pct"] == 25
    assert out["new_accounts_via_app"] == 3


def test_empty():
    out = shape_channels([])
    assert out == {"channels": [], "total": 0, "new_accounts_via_app": 0, "trend": []}


def test_trend_fills_days_and_buckets_apps():
    s = datetime.date(2026, 9, 1)
    e = datetime.date(2026, 9, 3)
    trend_rows = [
        {"day": s, "channel": "web", "n": 4},
        {"day": s, "channel": None, "n": 1},
        {"day": s, "channel": "app:A", "n": 2},
        {"day": s, "channel": "app:B", "n": 3},
        {"day": e, "channel": "widget", "n": 5},
    ]
    out = shape_channels([], trend_rows, s, e)
    assert [t["day"] for t in out["trend"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert out["trend"][0] == {"day": "2026-09-01", "web": 5, "widget": 0, "apps": 5}
    assert out["trend"][1] == {"day": "2026-09-02", "web": 0, "widget": 0, "apps": 0}
    assert out["trend"][2]["widget"] == 5
