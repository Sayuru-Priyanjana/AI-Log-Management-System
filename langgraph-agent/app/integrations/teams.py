"""Sends a message to a Microsoft Teams incoming webhook.

One function per message kind, kept free of any notion of settings storage, so
each can be called with whichever cluster's webhook is relevant — the caller
decides that, this just sends.

MessageCard is unforgiving: it silently drops a payload containing raw HTML and
renders code fences badly. Anything user- or model-written that reaches a card
is flattened first (see `plain`).
"""
from __future__ import annotations

import httpx

# What a channel can expect to receive, described once and used by the test
# message. Kept next to the sender so it cannot drift from what actually posts.
_WHAT_ARRIVES = (
    "- **Alerts** when a detection fires — the service, the monitor, and what tripped it.\n"
    "- **Investigation results** — the window analysed, the measured signals, the "
    "agent's confidence, and its suggested next steps.\n\n"
    "Each is controlled by its own switch in System settings, so this channel only "
    "gets what you asked for."
)


def plain(text: str, limit: int = 1400) -> str:
    """Flattens text into something a MessageCard will actually render.

    Truncation is marked rather than silent: a reader who cannot tell an answer
    was cut short will act on half of it.
    """
    if not text:
        return ""
    out = str(text).replace("`", "").replace("<", "&lt;").replace(">", "&gt;").strip()
    if len(out) > limit:
        out = out[:limit].rstrip() + "…\n\n_(truncated — open LogIntel for the full text)_"
    return out


async def _post(webhook_url: str, payload: dict) -> dict:
    url = (webhook_url or "").strip()
    if not url:
        return {"ok": False, "detail": "No webhook URL configured for this system."}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Could not reach the webhook: {exc}"}

    ok = 200 <= response.status_code < 300
    detail = (f"Teams accepted the message ({response.status_code})" if ok else
              f"Teams returned {response.status_code}: {response.text[:200]}")
    return {"ok": ok, "detail": detail}


async def ping_teams(webhook_url: str, channel_name: str = "") -> dict:
    """Posts one real test message.

    Not a reachability probe against someone else's endpoint — this *is* the
    action, exercised once. The card says what will arrive here later rather
    than only that the pipe works: a test that proves reachability alone leaves
    the reader to discover the shape of a real notification at the moment an
    incident is already in progress.
    """
    channel = channel_name or "this channel"
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "LogIntel connection test",
        "themeColor": "107C10",
        "title": "✅ LogIntel is connected to this channel",
        "sections": [
            {
                "facts": [
                    {"name": "Status", "value": "🟢 Webhook accepted the message"},
                    {"name": "Channel", "value": channel},
                    {"name": "Sent by", "value": "A person, from System settings → Integrations"},
                ],
                "markdown": True,
            },
            {
                "activityTitle": "**What will be posted here**",
                "text": _WHAT_ARRIVES,
                "markdown": True,
            },
            {
                "text": "_No detection triggered this message — it was a connection test._",
                "markdown": True,
            },
        ],
    }
    return await _post(webhook_url, payload)


async def notify_teams(webhook_url: str, payload: dict) -> dict:
    """Posts a MessageCard the caller has already composed."""
    return await _post(webhook_url, payload)
