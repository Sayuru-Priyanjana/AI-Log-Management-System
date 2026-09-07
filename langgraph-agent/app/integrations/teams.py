"""Sends a message to a Microsoft Teams webhook.

Teams has two incompatible webhook flavours, and sending the wrong shape to
either produces silence rather than an error:

  * the legacy Office 365 connector (`*.webhook.office.com`) takes a
    `MessageCard`. Microsoft has retired these for new use.
  * the Power Automate "Workflows" webhook (`*.logic.azure.com`), which is what
    the *Workflows → Post to a channel when a webhook request is received*
    template creates today, takes an **Adaptive Card** wrapped in an
    `attachments` envelope. Handed a MessageCard it accepts the POST, returns
    202, and posts nothing.

So the caller describes the message and this module renders it for whichever
webhook is configured, choosing by hostname. That is also why the formatting
lives here rather than in the browser: the URL is the only thing that decides
the dialect, and the browser never sees it.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# What a channel can expect to receive, described once and used by the test
# message. Kept next to the sender so it cannot drift from what actually posts.
_WHAT_ARRIVES = (
    "- **Alerts** when a detection fires — the service, the monitor, and what tripped it.\n"
    "- **Investigation results** — the window analysed, the measured signals, the "
    "agent's confidence, and its suggested next steps.\n\n"
    "Each is controlled by its own switch in System settings, so this channel only "
    "gets what you asked for."
)

# Adaptive Card colours are named, not hex.
_ADAPTIVE_COLOUR = {
    "critical": "attention", "high": "attention", "medium": "warning",
    "low": "accent", "none": "good", "info": "accent",
}
_MESSAGECARD_COLOUR = {
    "critical": "D13438", "high": "E81123", "medium": "F7A501",
    "low": "0078D4", "none": "107C10", "info": "5B5FC7",
}


def plain(text: str, limit: int = 1400) -> str:
    """Flattens text into something a card will actually render.

    Truncation is marked rather than silent: a reader who cannot tell an answer
    was cut short will act on half of it.
    """
    if not text:
        return ""
    out = str(text).replace("`", "").replace("<", "&lt;").replace(">", "&gt;").strip()
    if len(out) > limit:
        out = out[:limit].rstrip() + "…\n\n_(truncated — open LogIntel for the full text)_"
    return out


def is_workflow_webhook(url: str) -> bool:
    """Whether this URL is a Power Automate Workflows webhook.

    Matched on the host rather than the path: the Workflows template always
    issues a `*.logic.azure.com` URL, and legacy connectors are always
    `*.webhook.office.com`. Anything unrecognised is treated as a workflow,
    because that is what Teams creates now and what a new deployment will have.
    """
    host = (url or "").split("://")[-1].split("/")[0].lower()
    return not host.endswith("webhook.office.com")


def render(card: dict, webhook_url: str) -> dict:
    """The message, in the dialect this webhook understands."""
    return (_adaptive(card) if is_workflow_webhook(webhook_url)
            else _message_card(card))


def _facts(card: dict) -> list[dict]:
    return [f for f in (card.get("facts") or [])
            if f.get("value") not in (None, "", "—")]


def _adaptive(card: dict) -> dict:
    """An Adaptive Card in the envelope a Workflows webhook expects."""
    severity = card.get("severity", "info")
    body: list[dict] = [
        {
            "type": "TextBlock", "text": card.get("title", "LogIntel"),
            "weight": "Bolder", "size": "Medium", "wrap": True,
            "color": _ADAPTIVE_COLOUR.get(severity, "default"),
        },
    ]

    facts = _facts(card)
    if facts:
        body.append({
            "type": "FactSet",
            "facts": [{"title": f["name"], "value": str(f["value"])} for f in facts],
        })

    for section in card.get("sections") or []:
        if section.get("heading"):
            body.append({
                "type": "TextBlock", "text": section["heading"], "weight": "Bolder",
                "wrap": True, "spacing": "Medium",
            })
        if section.get("text"):
            body.append({"type": "TextBlock", "text": section["text"], "wrap": True})

    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "msteams": {"width": "Full"},
                "body": body,
            },
        }],
    }


def _message_card(card: dict) -> dict:
    """The legacy connector shape."""
    sections: list[dict] = []
    facts = _facts(card)
    if facts:
        sections.append({"facts": facts, "markdown": True})
    for section in card.get("sections") or []:
        entry: dict = {"markdown": True}
        if section.get("heading"):
            entry["activityTitle"] = f"**{section['heading']}**"
        if section.get("text"):
            entry["text"] = section["text"]
        sections.append(entry)

    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": plain(card.get("summary") or card.get("title") or "LogIntel", 120),
        "themeColor": _MESSAGECARD_COLOUR.get(card.get("severity", "info"), "5B5FC7"),
        "title": card.get("title", "LogIntel"),
        "sections": sections,
    }


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
    flavour = "Workflows" if is_workflow_webhook(url) else "connector"
    detail = (f"Teams accepted the message ({response.status_code}, {flavour} webhook)"
              if ok else f"Teams returned {response.status_code}: {response.text[:200]}")
    return {"ok": ok, "detail": detail}


async def ping_teams(webhook_url: str, channel_name: str = "") -> dict:
    """Posts one real test message.

    Not a reachability probe against someone else's endpoint — this *is* the
    action, exercised once, in the same dialect a real notification will use.
    The card says what will arrive here later rather than only that the pipe
    works: a test that proves reachability alone leaves the reader to discover
    the shape of a real notification at the moment an incident is in progress.
    """
    card = {
        "title": "✅ LogIntel is connected to this channel",
        "summary": "LogIntel connection test",
        "severity": "none",
        "facts": [
            {"name": "Status", "value": "Webhook accepted the message"},
            {"name": "Channel", "value": channel_name or "this channel"},
            {"name": "Webhook type",
             "value": "Workflows" if is_workflow_webhook(webhook_url) else "Office 365 connector"},
            {"name": "Sent by", "value": "A person, from System settings → Integrations"},
        ],
        "sections": [
            {"heading": "What will be posted here", "text": _WHAT_ARRIVES},
            {"text": "_No detection triggered this message — it was a connection test._"},
        ],
    }
    return await _post(webhook_url, render(card, webhook_url))


async def notify_teams(webhook_url: str, payload: dict) -> dict:
    """Posts a message the caller described.

    Accepts either the neutral card this module renders, or a payload that is
    already a Teams card — the second form is what older clients send, and
    posting it unchanged keeps them working.
    """
    already_rendered = "@type" in payload or "attachments" in payload
    body = payload if already_rendered else render(payload, webhook_url)
    return await _post(webhook_url, body)
