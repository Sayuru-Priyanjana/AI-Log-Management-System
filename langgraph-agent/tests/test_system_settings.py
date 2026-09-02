from __future__ import annotations

import json

import pytest

from app.integrations.teams import ping_teams
from app.store.system_settings import DEFAULTS, SystemSettingsStore, validate


class FakeOpenSearch:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken
        self.docs: dict[str, dict] = {}

    async def get_document(self, index, doc_id):
        if self.broken:
            raise RuntimeError("connection refused")
        return self.docs.get(doc_id)

    async def index_document(self, index, document, doc_id=None):
        if self.broken:
            raise RuntimeError("connection refused")
        self.docs[doc_id] = document
        return {"result": "updated"}


# --------------------------------------------------------------------------
# Isolation between systems — the whole point of this store existing
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_two_systems_keep_independent_settings():
    """The mistake this replaces: a single process-wide Teams webhook, which
    can only ever be right for one cluster and silently wrong for every other
    one sharing the same agent."""
    client = FakeOpenSearch()
    store = SystemSettingsStore(client)

    await store.save("shopdemo", {"teams_webhook_url": "https://teams/shopdemo"})
    await store.save("payments-prod", {"teams_webhook_url": "https://teams/payments"})

    assert (await store.get("shopdemo"))["teams_webhook_url"] == "https://teams/shopdemo"
    assert (await store.get("payments-prod"))["teams_webhook_url"] == "https://teams/payments"


@pytest.mark.asyncio
async def test_an_unconfigured_system_reads_as_the_defaults():
    store = SystemSettingsStore(FakeOpenSearch())
    assert await store.get("never-touched") == DEFAULTS
    assert (await store.get("never-touched")) is not DEFAULTS, "must not hand back the mutable shared default"


@pytest.mark.asyncio
async def test_saving_one_field_does_not_erase_the_others():
    client = FakeOpenSearch()
    store = SystemSettingsStore(client)
    await store.save("shopdemo", {"teams_channel_name": "on-call", "auto_scan_enabled": True})

    await store.save("shopdemo", {"teams_webhook_url": "https://teams/x"})

    values = await store.get("shopdemo")
    assert values["teams_channel_name"] == "on-call"
    assert values["auto_scan_enabled"] is True
    assert values["teams_webhook_url"] == "https://teams/x"


@pytest.mark.asyncio
async def test_an_unreachable_store_reads_as_defaults_rather_than_raising():
    store = SystemSettingsStore(FakeOpenSearch(broken=True))
    assert await store.get("shopdemo") == DEFAULTS


@pytest.mark.asyncio
async def test_a_failed_save_is_reported():
    store = SystemSettingsStore(FakeOpenSearch(broken=True))
    assert await store.save("shopdemo", {"teams_channel_name": "x"}) is False


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def test_a_webhook_url_without_a_scheme_is_refused():
    with pytest.raises(ValueError, match="http://"):
        validate({"teams_webhook_url": "teams.microsoft.com/hook"})


def test_an_empty_webhook_url_clears_it_rather_than_being_refused():
    assert validate({"teams_webhook_url": ""})["teams_webhook_url"] == ""


@pytest.mark.parametrize("bad", ["3am", "25:00", "9:5", "03-00", ""])
def test_a_malformed_scan_time_is_refused(bad):
    with pytest.raises(ValueError, match="HH:MM"):
        validate({"scan_time": bad})


def test_a_well_formed_scan_time_round_trips():
    assert validate({"scan_time": "03:00"})["scan_time"] == "03:00"
    assert validate({"scan_time": "23:59"})["scan_time"] == "23:59"


def test_boolean_flags_accept_the_usual_truthy_strings():
    assert validate({"auto_scan_enabled": "true"})["auto_scan_enabled"] is True
    assert validate({"notify_on_alert_enabled": "0"})["notify_on_alert_enabled"] is False
    assert validate({"notify_on_scan_result_enabled": "yes"})["notify_on_scan_result_enabled"] is True


def test_an_unknown_field_is_refused():
    with pytest.raises(ValueError, match="unknown field"):
        validate({"opensearch_url": "http://nice-try:9200"})


# --------------------------------------------------------------------------
# Teams ping — reused from the earlier global-settings version, now called
# with an explicit webhook rather than reading it off process settings.
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_ping_with_no_webhook_says_so_without_a_request():
    result = await ping_teams("")
    assert result["ok"] is False
    assert "No webhook" in result["detail"]


@pytest.mark.asyncio
async def test_a_successful_ping_names_the_configured_channel(monkeypatch):
    import httpx

    captured = {}

    class FakeResponse:
        status_code = 200
        text = "1"

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await ping_teams("https://outlook.office.com/webhook/shopdemo", "on-call")

    assert result["ok"] is True
    assert captured["url"] == "https://outlook.office.com/webhook/shopdemo"
    # Asserted against the whole payload rather than one field. The message was a
    # flat {"text": ...} when this was written and is now a MessageCard carrying
    # the channel in a fact, so pinning the field made the test fail on a change
    # that did not break anything. What matters is that whoever reads the message
    # can tell which channel it was aimed at.
    assert "on-call" in json.dumps(captured["json"])


@pytest.mark.asyncio
async def test_a_connection_failure_is_reported_not_raised(monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None):
            raise httpx.ConnectError("name resolution failed")

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    result = await ping_teams("https://unreachable.invalid/webhook")
    assert result["ok"] is False
    assert "Could not reach" in result["detail"]


@pytest.mark.asyncio
async def test_a_document_from_an_earlier_schema_does_not_leak_stale_fields():
    """Regression: a system configured before scan_interval_minutes was renamed
    to scan_time still has the old key sitting in its stored document. `get`
    used to hand it straight back, and since the UI round-trips whatever `get`
    returns on the next `save`, that stale key made every future save to that
    system fail validation with "unknown field(s)"."""
    client = FakeOpenSearch()
    client.docs["shopdemo"] = {
        "teams_channel_name": "on-call",
        "teams_webhook_url": "https://teams/shopdemo",
        "auto_scan_enabled": True,
        "scan_interval_minutes": 15,      # old field name
        "auto_notify_enabled": True,      # old field name
    }
    store = SystemSettingsStore(client)

    values = await store.get("shopdemo")

    assert "scan_interval_minutes" not in values
    assert "auto_notify_enabled" not in values
    assert values["teams_channel_name"] == "on-call", "real settings must survive the migration"
    assert values["scan_time"] == "03:00", "missing new fields fall back to their default"

    # And the round-trip that broke before this fix:
    validate(values)
    assert await store.save("shopdemo", validate(values)) is True
