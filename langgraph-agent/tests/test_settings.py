from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.store.runtime_config import RuntimeConfig, validate
from app.util import timefmt


@pytest.fixture(autouse=True)
def restore_zone():
    before = timefmt.label()
    yield
    timefmt.set_zone(before)


class FakeOpenSearch:
    """Stands in for the client, and can be told to fail like a real one."""

    def __init__(self, stored: dict | None = None, broken: bool = False) -> None:
        self.stored = stored
        self.broken = broken
        self.writes: list[dict] = []

    async def get_document(self, index, doc_id):
        if self.broken:
            raise RuntimeError("connection refused")
        return self.stored

    async def index_document(self, index, document, doc_id=None):
        if self.broken:
            raise RuntimeError("connection refused")
        self.writes.append(document)
        return {"result": "updated"}


# --------------------------------------------------------------------------
# Time zone
# --------------------------------------------------------------------------
def test_the_default_zone_is_asia_colombo_offset():
    zone, label = timefmt.parse_zone(timefmt.DEFAULT_ZONE)
    moment = datetime(2026, 8, 12, 5, 12, tzinfo=timezone.utc)
    assert label == "+05:30"
    assert moment.astimezone(zone).strftime("%H:%M") == "10:42"


def test_a_naive_timestamp_is_read_as_utc_not_as_the_host_clock():
    """Everything here is stored in UTC. Guessing the host's zone would make the
    output depend on where the container happens to run."""
    timefmt.set_zone("+05:30")
    assert timefmt.clock(datetime(2026, 8, 12, 5, 12)) == "10:42:00"


def test_a_named_zone_is_resolved_rather_than_flattened_to_an_offset():
    zone, label = timefmt.parse_zone("Asia/Colombo")
    assert label == "Asia/Colombo"
    winter = datetime(2026, 1, 15, 5, 12, tzinfo=timezone.utc).astimezone(zone)
    summer = datetime(2026, 7, 15, 5, 12, tzinfo=timezone.utc).astimezone(zone)
    assert winter.strftime("%H:%M") == summer.strftime("%H:%M") == "10:42"


def test_a_zone_with_daylight_saving_shifts_with_the_season():
    zone, _ = timefmt.parse_zone("Europe/London")
    winter = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc).astimezone(zone)
    summer = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc).astimezone(zone)
    assert winter.hour == 12 and summer.hour == 13, (
        "a fixed offset would report the same hour in both, which is wrong for half the year"
    )


@pytest.mark.parametrize("bad", ["+25:00", "Mars/Olympus", "half past five", "+05:99"])
def test_a_nonsense_zone_is_rejected_at_the_point_of_entry(bad):
    with pytest.raises(ValueError):
        timefmt.parse_zone(bad)


def test_negative_offsets_round_trip():
    zone, label = timefmt.parse_zone("-08:00")
    assert label == "-08:00"
    moment = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)
    assert moment.astimezone(zone).strftime("%H:%M") == "21:00"


def test_the_agents_own_prose_follows_the_configured_zone():
    """The point of doing this server-side: the agent writes times into its own
    sentences, and a page formatting them differently hands the reader two
    clocks to reconcile."""
    from app.models.domain import TimeWindow

    window = TimeWindow(start=datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc),
                        end=datetime(2026, 8, 12, 5, 30, tzinfo=timezone.utc))
    timefmt.set_zone("+05:30")
    assert "10:30:00" in str(window)
    timefmt.set_zone("UTC")
    assert "05:30:00" in str(window)


# --------------------------------------------------------------------------
# Editable settings
# --------------------------------------------------------------------------
def test_only_listed_settings_are_editable():
    """Thresholds stay in code. A text box that quietly changed what counts as an
    incident would make every stored investigation incomparable with the next."""
    with pytest.raises(ValueError, match="not an editable setting"):
        validate("error_rate_spike_multiplier", 1.5)


def test_a_url_without_a_scheme_is_refused_before_it_takes_a_source_offline():
    with pytest.raises(ValueError, match="http://"):
        validate("opensearch_url", "opensearch:9200")


def test_a_context_window_that_would_truncate_every_prompt_is_refused():
    with pytest.raises(ValueError, match="truncates"):
        validate("ollama_num_ctx", 1024)
    assert validate("ollama_num_ctx", "16384") == 16384


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="must be one of"):
        validate("llm_provider", "gemeni")


@pytest.mark.asyncio
async def test_saved_overrides_are_applied_over_the_environment(monkeypatch):
    monkeypatch.setattr(settings, "prometheus_url", "http://from-env:30090")
    config = RuntimeConfig(FakeOpenSearch(
        {"values": {"prometheus_url": "http://from-settings:9090"}}))

    await config.load()

    assert settings.prometheus_url == "http://from-settings:9090"
    described = {f["name"]: f for f in config.describe()}
    assert described["prometheus_url"]["source"] == "saved", (
        "the origin has to be visible, or a value written months ago silently "
        "shadows the environment variable someone is staring at"
    )


@pytest.mark.asyncio
async def test_clearing_an_override_falls_back_to_the_environment():
    config = RuntimeConfig(FakeOpenSearch())
    original = settings.prometheus_url

    config.apply({"prometheus_url": "http://temporary:9090"})
    assert settings.prometheus_url == "http://temporary:9090"

    config.apply({"prometheus_url": None})
    assert settings.prometheus_url == original
    assert "prometheus_url" not in config.overrides


@pytest.mark.asyncio
async def test_an_api_key_is_never_returned_only_reported_as_present(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-secret-value")
    config = RuntimeConfig(FakeOpenSearch())

    described = {f["name"]: f for f in config.describe()}
    key = described["llm_api_key"]

    assert key["value"] is None
    assert key["is_set"] is True
    assert "sk-secret-value" not in repr(described)


@pytest.mark.asyncio
async def test_a_failed_save_is_reported_rather_than_swallowed():
    """Pointing at a broken OpenSearch is exactly when this fails, and exactly
    when the user needs to know the change will not survive a restart."""
    config = RuntimeConfig(FakeOpenSearch(broken=True))
    config.apply({"prometheus_url": "http://elsewhere:9090"})

    assert await config.save() is False
    assert config.persisted is False
    assert settings.prometheus_url == "http://elsewhere:9090", "still in force"


@pytest.mark.asyncio
async def test_an_unreachable_store_does_not_stop_the_agent_starting():
    """The settings page is where you go to fix an unreachable OpenSearch. An
    agent that will not boot without one cannot show it to you."""
    config = RuntimeConfig(FakeOpenSearch(broken=True))
    await config.load()          # must not raise
    assert config.overrides == {}


@pytest.mark.asyncio
async def test_changing_the_zone_needs_no_client_rebuild():
    config = RuntimeConfig(FakeOpenSearch())
    changed = config.apply({"display_timezone": "UTC"})
    assert changed == ["display_timezone"]
    assert config.needs_rebuild(changed) is False
    assert timefmt.label() == "UTC"

    changed = config.apply({"opensearch_url": "http://moved:9200"})
    assert config.needs_rebuild(changed) is True
    config.apply({"opensearch_url": None})


def test_every_editable_field_names_a_real_setting():
    from app.store.runtime_config import FIELDS

    for field in FIELDS:
        assert hasattr(settings, field.name), f"{field.name} is not a setting"
