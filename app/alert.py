"""External alerting — push warn/error notifications to a webhook (Slack/Discord/
generic JSON `{text}`). Default OFF (no ALERT_WEBHOOK_URL = no-op). Per-title
cooldown so one recurring incident doesn't flood the channel.

Wired from notify.emit(): any notification at level warn/error also fires here.
"""
import json
import os
import time
import urllib.request

WEBHOOK = os.getenv("ALERT_WEBHOOK_URL", "")
COOLDOWN = int(os.getenv("ALERT_COOLDOWN_SEC", "300"))

_last: dict[str, float] = {}


def maybe_push(kind: str, title: str, body: str, level: str) -> None:
    """Fire-and-forget webhook for a warn/error event, throttled per title."""
    if not WEBHOOK:
        return
    key = (title or "")[:80]
    now = time.time()
    if now - _last.get(key, 0.0) < COOLDOWN:
        return
    _last[key] = now
    text = f"[{(level or 'info').upper()}] {title}"
    if body:
        text += f"\n{body}"
    payload = json.dumps({"text": text[:1000], "kind": kind, "level": level}).encode()
    req = urllib.request.Request(
        WEBHOOK, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[alert] webhook failed: {e!r}")
