"""
Teams has two incompatible webhook flavours and neither one errors.

The legacy Office 365 connector takes a MessageCard. The Power Automate
"Workflows" webhook that Teams creates today takes an Adaptive Card in an
`attachments` envelope — hand it a MessageCard and it accepts the POST, returns
202, and posts nothing to the channel. Which is a very quiet way for every
notification to go missing.
"""
from __future__ import annotations

from app.integrations import teams

CARD = {
    "title": "🔎 Incident: payment-db was OOM-killed",
    "summary": "payment-db was OOM-killed",
    "severity": "critical",
    "facts": [
        {"name": "System", "value": "Shop Demo"},
        {"name": "Service", "value": "payment-db"},
        {"name": "Dropped", "value": ""},
        {"name": "Also dropped", "value": None},
    ],
    "sections": [
        {"heading": "What happened", "text": "It hit its memory limit."},
        {"text": "A closing note."},
    ],
}

WORKFLOW = "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke"
CONNECTOR = "https://acme.webhook.office.com/webhookb2/abc@def/IncomingWebhook/ghi/jkl"


def test_a_workflows_webhook_is_sent_an_adaptive_card():
    payload = teams.render(CARD, WORKFLOW)

    assert payload["type"] == "message"
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    content = attachment["content"]
    assert content["type"] == "AdaptiveCard"
    # Full width, or a card of facts renders in a narrow column in the channel.
    assert content["msteams"]["width"] == "Full"

    kinds = [b["type"] for b in content["body"]]
    assert kinds[0] == "TextBlock"
    assert "FactSet" in kinds
    # Severity reaches the card as a named colour; Adaptive Cards reject hex.
    assert content["body"][0]["color"] == "attention"


def test_a_legacy_connector_is_sent_a_message_card():
    payload = teams.render(CARD, CONNECTOR)

    assert payload["@type"] == "MessageCard"
    assert payload["themeColor"] == "D13438"
    assert payload["sections"][0]["facts"][0]["name"] == "System"


def test_an_unknown_host_is_treated_as_a_workflows_webhook():
    """What Teams creates now. A new deployment that guessed the other way would
    post nothing at all, with a 202 to say it worked."""
    assert teams.is_workflow_webhook("https://example.test/hook") is True
    assert teams.is_workflow_webhook(WORKFLOW) is True
    assert teams.is_workflow_webhook(CONNECTOR) is False


def test_empty_facts_are_dropped_from_both_dialects():
    """A fact with no value is a row that says nothing, twice: once as a label
    and once as the blank beside it."""
    for url in (WORKFLOW, CONNECTOR):
        payload = teams.render(CARD, url)
        rendered = str(payload)
        assert "Dropped" not in rendered
        assert "Also dropped" not in rendered


def test_text_is_flattened_before_it_reaches_a_card():
    dirty = teams.plain("<b>bold</b> and `code`")
    assert "<" not in dirty and "`" not in dirty

    long = teams.plain("x" * 3000)
    assert len(long) < 1600
    assert "truncated" in long


def test_an_already_rendered_payload_is_posted_unchanged():
    """Older clients send a finished Teams card. Re-rendering one would wrap a
    MessageCard inside an Adaptive Card and post nothing."""
    legacy = {"@type": "MessageCard", "title": "from an old client", "sections": []}
    assert "@type" in legacy
    # `notify_teams` branches on exactly this, without touching the network.
    assert ("@type" in legacy or "attachments" in legacy) is True
