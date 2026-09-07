"""
Tests for generalised log retrieval.

`LogTool.fetch` is the foundation of live, agent-driven querying: it is the first
method that lets a caller choose the level filter, the sort order and the size
of a log query rather than receiving whatever the incident collector decided to
keep. Everything asserted here is a property the reasoning loop will depend on —
that the filters it asks for actually reach the query, and that nothing it can
pass turns into a malformed one.
"""
from __future__ import annotations

import pytest

from app.models.domain import TimeWindow
from app.tools.logs import LogTool
from tests.conftest import T0, at


class RecordingClient:
    """Captures the query body instead of running it.

    The point of these tests is what LogTool *asks OpenSearch for*; the parsing
    of a response is exercised separately.
    """

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.bodies: list[dict] = []
        self._hits = hits or []

    async def search(self, index: str, body: dict) -> dict:
        self.bodies.append(body)
        return {"hits": {"hits": self._hits, "total": {"value": len(self._hits)}}}

    @property
    def last(self) -> dict:
        return self.bodies[-1]


def hit(doc_id: str, timestamp: str, *, level: str = "INFO",
        service: str = "checkout-api", message: str = "hello") -> dict:
    return {
        "_id": doc_id,
        "_source": {
            "@timestamp": timestamp,
            "log": {"level": level, "message": message},
            "service": {"name": service},
        },
    }


def filters_of(body: dict) -> list[dict]:
    return body["query"]["bool"]["filter"]


def window() -> TimeWindow:
    return TimeWindow(start=T0, end=at(1800), label="incident")


# ---------------------------------------------------------------- ordering
@pytest.mark.asyncio
async def test_newest_first_is_what_last_n_logs_needs(plan):
    """The question this whole change exists to answer.

    `samples` could only sort ascending, so "give me the last 20 logs" had no
    code path at all — it returned the *oldest* 20 error lines of the window.
    """
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), order="newest", limit=20)

    assert client.last["sort"] == [{"@timestamp": {"order": "desc"}}]
    assert client.last["size"] == 20


@pytest.mark.asyncio
async def test_oldest_first_is_still_available_for_incident_evidence(plan):
    """Root-cause work wants the *first* failures, not the latest repetitions."""
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), order="oldest")
    assert client.last["sort"] == [{"@timestamp": {"order": "asc"}}]


@pytest.mark.asyncio
async def test_results_are_returned_in_chronological_order_either_way(plan):
    """Sorting descending is how you *find* the last N lines; it is not how you
    read them. A transcript handed to the model out of order invites exactly the
    effect-before-cause reasoning the pipeline works to prevent."""
    client = RecordingClient([
        hit("c", "2026-08-09T12:30:00Z"),
        hit("b", "2026-08-09T12:20:00Z"),
        hit("a", "2026-08-09T12:10:00Z"),
    ])
    found = await LogTool(client).fetch(plan, window(), order="newest")
    assert [s.id for s in found] == ["log:a", "log:b", "log:c"]


# ----------------------------------------------------------------- filters
@pytest.mark.asyncio
async def test_no_level_filter_means_every_level(plan):
    """The default has to be "all levels".

    `samples` hardcoded ERROR/FATAL/CRITICAL, which is why a request for recent
    activity came back as a list of failures. A cause is very often an INFO or
    WARN line immediately before the first error.
    """
    client = RecordingClient()
    await LogTool(client).fetch(plan, window())
    assert not any("log.level" in str(f) for f in filters_of(client.last))


@pytest.mark.asyncio
async def test_levels_are_upper_cased_before_they_reach_the_query(plan):
    """`log.level` is a keyword field, so a term filter is case-sensitive and
    level="error" would silently match nothing."""
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), levels=["error", "Warn"])
    assert {"terms": {"log.level": ["ERROR", "WARN"]}} in filters_of(client.last)


@pytest.mark.asyncio
async def test_a_service_filter_is_an_exact_term(plan):
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), service="payment-db")
    assert {"term": {"service.name": "payment-db"}} in filters_of(client.last)


@pytest.mark.asyncio
async def test_contains_searches_message_text(plan):
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), contains="connection refused")
    assert {"match_phrase": {"log.message": "connection refused"}} in filters_of(client.last)


@pytest.mark.asyncio
async def test_system_and_window_scoping_always_applies(plan):
    """Whatever the caller asks for, it stays inside its own system, environment
    and time window. These are not the agent's to widen."""
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), service="payment-db", limit=200)

    rendered = str(filters_of(client.last))
    assert "'system.id': 'shopdemo'" in rendered
    assert "'environment': 'staging'" in rendered
    assert "@timestamp" in rendered


# ------------------------------------------------------------------ limits
@pytest.mark.asyncio
async def test_the_limit_is_capped_however_large_it_is_asked_for(plan):
    """`limit` is chosen by the model. Unbounded, one call could put thousands
    of raw lines into the next prompt and truncate the investigation."""
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), limit=100_000)
    assert client.last["size"] == LogTool.MAX_FETCH


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["twenty", None, -5, 0])
async def test_a_nonsensical_limit_falls_back_rather_than_raising(plan, bad):
    """Small models pass `limit="twenty"`. Refusing the call costs a whole step
    and teaches the model nothing; a sane default answers the question."""
    client = RecordingClient()
    await LogTool(client).fetch(plan, window(), limit=bad)
    assert 1 <= client.last["size"] <= LogTool.MAX_FETCH


# ------------------------------------------------------- backwards compatible
@pytest.mark.asyncio
async def test_samples_still_means_first_errors_of_the_window(plan):
    """`samples` is now one configuration of `fetch`. The incident collector
    depends on its old behaviour exactly: error levels only, oldest first."""
    client = RecordingClient()
    await LogTool(client).samples(plan, window(), size=25)

    assert client.last["sort"] == [{"@timestamp": {"order": "asc"}}]
    assert client.last["size"] == 25
    assert {"terms": {"log.level": ["ERROR", "FATAL", "CRITICAL"]}} in filters_of(client.last)


@pytest.mark.asyncio
async def test_every_returned_line_carries_an_id_the_model_cannot_invent(plan):
    """The verifier can only tell a real citation from a fabricated one because
    Python mints every ID. Live querying must not change that."""
    client = RecordingClient([hit("abc123def456", "2026-08-09T12:10:00Z")])
    found = await LogTool(client).fetch(plan, window())
    assert found[0].id.startswith("log:")
    assert "abc123def4" in found[0].id
