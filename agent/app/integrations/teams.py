"""Sends a message to a Microsoft Teams incoming webhook.

One function, kept free of any notion of settings storage, so it can be called
with whichever cluster's webhook is relevant — the caller decides that, this
just sends.
"""
from __future__ import annotations

import httpx


async def ping_teams(webhook_url: str, channel_name: str = "") -> dict:
    """Posts one real test message. Not a reachability probe against someone
    else's endpoint — this *is* the action, exercised once. A webhook that
    accepts the ping will accept the same shape of message a real detection
    sends later."""
    url = (webhook_url or "").strip()
    if not url:
        return {"ok": False, "detail": "No webhook URL configured for this system."}

    channel = channel_name or "this channel"
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": "LogIntel test notification",
        "themeColor": "1F6FEB",
        "title": "LogIntel connection test",
        "text": f"This confirms LogIntel can reach **{channel}**. "
                f"No detection triggered this — it was requested from the system's settings.",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "detail": f"Could not reach the webhook: {exc}"}

    ok = 200 <= response.status_code < 300
    detail = (f"Teams accepted the message ({response.status_code})" if ok else
              f"Teams returned {response.status_code}: {response.text[:200]}")
    return {"ok": ok, "detail": detail}


async def notify_teams(webhook_url: str, payload: dict) -> dict:
    """Posts a custom MessageCard to the Teams webhook."""
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
