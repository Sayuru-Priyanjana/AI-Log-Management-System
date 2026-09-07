"""
The live query tools — the ones that reach the index during the loop.

Every other tool reads the evidence bundle collected before the loop started,
which is why the loop could only ever rearrange facts it had already been given.
These three let it go and look, and the properties asserted here are what keep
that safe: the model supplies parameters, never query fragments; every line comes
back with an ID Python minted; and the number of queries one run may issue is
bounded.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.agents.tool_bindings import MAX_LIVE_QUERIES, ToolBindings
from app.models.domain import TimeWindow
from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, LogSample, MetricEvidence
from tests.conftest import at


class FakeLogTool:
    """Records how it was called and returns whatever it was seeded with."""

    def __init__(self, samples=None) -> None:
        self.calls: list[dict] = []
        self._samples = samples or []

    async def fetch(self, plan, window, *, levels=None, service=None,
                    contains=None, order="newest", limit=20):
        self.calls.append({"window": window, "levels": levels, "service": service,
                           "contains": contains, "order": order, "limit": limit})
        return list(self._samples)

    @property
    def last(self) -> dict:
        return self.calls[-1]


def sample(seconds: int, message: str, *, level="INFO", service="payment-db") -> LogSample:
    return LogSample(id=f"log:{seconds}", timestamp=at(seconds), level=level,
                     service=service, message=message)


def make(log_tool=None, *, plan, windows, search=None) -> ToolBindings:
    return ToolBindings(
        plan, windows,
        EvidenceBundle(logs=LogEvidence(), events=EventEvidence(),
                       metrics=MetricEvidence()),
        [], [], log_tool=log_tool, search_window=search,
    )


# --------------------------------------------------------------- fetch_logs
@pytest.mark.asyncio
async def test_fetch_logs_answers_last_n_logs(plan, windows):
    """The question that had no code path before any of this existed."""
    tool = FakeLogTool([sample(60, "GET /health 200"), sample(120, "GET /cart 200")])
    result = await make(tool, plan=plan, windows=windows).execute(
        "fetch_logs", {"order": "newest", "limit": 20})

    assert tool.last["order"] == "newest"
    assert tool.last["limit"] == 20
    assert tool.last["levels"] is None, "no level filter means every level"
    assert result.table["rows"], "a retrieval answer needs rows, not prose"
    assert result.evidence_ids == ["log:60", "log:120"]


@pytest.mark.asyncio
async def test_every_returned_line_is_cited_by_a_visible_id(plan, windows):
    """An ID the loop is expected to cite but never shown guarantees an
    unverifiable answer — the same regression search_logs once had."""
    tool = FakeLogTool([sample(60, "boom", level="ERROR")])
    result = await make(tool, plan=plan, windows=windows).execute("fetch_logs", {})

    for evidence_id in result.evidence_ids:
        assert evidence_id in result.text


@pytest.mark.asyncio
async def test_all_means_no_service_filter(plan, windows):
    tool = FakeLogTool()
    await make(tool, plan=plan, windows=windows).execute(
        "fetch_logs", {"service_name": "all"})
    assert tool.last["service"] is None


@pytest.mark.asyncio
async def test_a_comma_separated_level_is_understood(plan, windows):
    """The model writes level="ERROR, WARN" because it is a reasonable thing to
    mean. _levels already handles it; the live tools must not lose that."""
    tool = FakeLogTool()
    await make(tool, plan=plan, windows=windows).execute(
        "fetch_logs", {"level": "ERROR, WARN"})
    assert tool.last["levels"] == ["ERROR", "WARN"]


# -------------------------------------------------------------- logs_around
@pytest.mark.asyncio
async def test_logs_around_looks_before_the_moment(plan, windows):
    """The whole point: the cause is usually logged just before the effect.

    A search window is supplied because the pipeline always supplies one — the
    onset here is the incident start, and looking before it is exactly what this
    tool is for.
    """
    search = TimeWindow(start=at(0), end=at(1800), label="search")
    tool = FakeLogTool([sample(540, "connection pool exhausted", level="WARN")])
    onset = at(600)
    result = await make(tool, plan=plan, windows=windows, search=search).execute(
        "logs_around", {"timestamp": onset.isoformat(), "before_seconds": 180,
                        "after_seconds": 60})

    used = tool.last["window"]
    assert used.start == onset - timedelta(seconds=180)
    assert used.end == onset + timedelta(seconds=60)
    assert tool.last["order"] == "oldest", "causal reading order"
    assert "connection pool exhausted" in result.text


@pytest.mark.asyncio
async def test_without_a_search_window_the_lookback_stops_at_the_incident(plan, windows):
    """Documents the degraded case rather than pretending it cannot happen.

    With no search range the only defensible bound is the incident window, so a
    lookback from its own start finds nothing — `logs_around` is then no more
    capable than the evidence already collected. The pipeline always passes a
    search window; a caller that does not should know what it is giving up.
    """
    tool = FakeLogTool()
    await make(tool, plan=plan, windows=windows).execute(
        "logs_around", {"timestamp": windows.incident.start.isoformat(),
                        "before_seconds": 600})

    assert tool.last["window"].start == windows.incident.start


@pytest.mark.asyncio
async def test_logs_around_defaults_to_every_level(plan, windows):
    """Filtering to ERROR here would hide the trigger, which is usually an INFO
    or WARN line — exactly the evidence the incident collector already drops."""
    tool = FakeLogTool()
    await make(tool, plan=plan, windows=windows).execute(
        "logs_around", {"timestamp": at(600).isoformat()})
    assert tool.last["levels"] is None


@pytest.mark.asyncio
async def test_a_clock_time_copied_from_an_observation_is_accepted(plan, windows):
    """Observations render times with clock() as "14:32", so that is what the
    model echoes back. Rejecting it costs a step to learn a format that was
    never shown to it."""
    tool = FakeLogTool()
    bindings = make(tool, plan=plan, windows=windows)
    moment = windows.incident.end - timedelta(minutes=5)
    result = await bindings.execute(
        "logs_around", {"timestamp": moment.strftime("%H:%M")})

    assert "Could not read" not in result.text
    assert tool.calls, "a parseable time should have reached the index"


@pytest.mark.asyncio
async def test_an_unreadable_timestamp_explains_the_accepted_forms(plan, windows):
    tool = FakeLogTool()
    result = await make(tool, plan=plan, windows=windows).execute(
        "logs_around", {"timestamp": "shortly before the spike"})

    assert "Could not read" in result.text
    assert "14:32" in result.text, "should show an example form"
    assert not tool.calls, "an unparseable time must not spend a query"


@pytest.mark.asyncio
async def test_the_window_never_escapes_the_search_range(plan, windows):
    """The agent picks the span; it does not get to widen the investigation
    beyond the data the pipeline decided was in scope."""
    search = TimeWindow(start=at(0), end=at(1800), label="search")
    tool = FakeLogTool()
    await make(tool, plan=plan, windows=windows, search=search).execute(
        "logs_around", {"timestamp": at(60).isoformat(), "before_seconds": 3600})

    assert tool.last["window"].start >= search.start


# --------------------------------------------------------- first_occurrence
@pytest.mark.asyncio
async def test_first_occurrence_searches_the_whole_search_range(plan, windows):
    """The incident window is clamped to the period asked about, so an error that
    began earlier has its origin hidden. This is the only tool that can see it."""
    search = TimeWindow(start=at(0), end=at(1800), label="search")
    tool = FakeLogTool([sample(120, "connection refused", level="ERROR")])
    await make(tool, plan=plan, windows=windows, search=search).execute(
        "first_occurrence", {"contains": "connection refused"})

    assert tool.last["window"].start == search.start
    assert tool.last["order"] == "oldest"
    assert tool.last["contains"] == "connection refused"


@pytest.mark.asyncio
async def test_an_occurrence_before_the_window_is_called_out_as_predating_it(plan, windows):
    """This is the finding that settles causal ordering, so it must be stated,
    not left for the model to work out from two timestamps."""
    search = TimeWindow(start=at(0), end=at(1800), label="search")
    early = sample(60, "connection refused", level="ERROR")   # windows.incident starts at 600
    tool = FakeLogTool([early])
    result = await make(tool, plan=plan, windows=windows, search=search).execute(
        "first_occurrence", {"contains": "connection refused"})

    assert "PREDATES" in result.text
    assert "cannot have been triggered by anything inside it" in result.text


@pytest.mark.asyncio
async def test_an_occurrence_inside_the_window_says_so(plan, windows):
    search = TimeWindow(start=at(0), end=at(1800), label="search")
    tool = FakeLogTool([sample(900, "connection refused", level="ERROR")])
    result = await make(tool, plan=plan, windows=windows, search=search).execute(
        "first_occurrence", {"contains": "connection refused"})

    assert "falls inside the window analysed" in result.text
    assert "PREDATES" not in result.text


@pytest.mark.asyncio
async def test_a_phrase_that_never_occurs_says_the_range_was_exhausted(plan, windows):
    """"Not found" must distinguish itself from "not looked for" — otherwise the
    loop reads silence as absence, which is the failure the whole pipeline
    guards against."""
    tool = FakeLogTool([])
    result = await make(tool, plan=plan, windows=windows).execute(
        "first_occurrence", {"contains": "kernel panic"})

    assert "does not appear anywhere between" in result.text
    assert "widest range available" in result.text


@pytest.mark.asyncio
async def test_first_occurrence_requires_something_to_search_for(plan, windows):
    tool = FakeLogTool()
    result = await make(tool, plan=plan, windows=windows).execute(
        "first_occurrence", {"service_name": "payment-db"})
    assert "needs `contains`" in result.text
    assert not tool.calls


# ------------------------------------------------------------------ budgets
@pytest.mark.asyncio
async def test_live_queries_are_capped(plan, windows):
    """The model chooses how many queries to make. Unbounded, a confused loop
    would hammer the index."""
    tool = FakeLogTool()
    bindings = make(tool, plan=plan, windows=windows)
    for _ in range(MAX_LIVE_QUERIES + 3):
        result = await bindings.execute("fetch_logs", {})

    assert len(tool.calls) == MAX_LIVE_QUERIES
    assert "Query budget spent" in result.text
    assert "Answer from the evidence you have gathered" in result.text


@pytest.mark.asyncio
async def test_the_in_memory_tools_are_not_charged_against_the_budget(plan, windows):
    """Reading already-collected evidence costs nothing and must stay free."""
    bindings = make(FakeLogTool(), plan=plan, windows=windows)
    for _ in range(30):
        await bindings.execute("get_signals", {})
    assert bindings.live_queries == 0


@pytest.mark.asyncio
async def test_without_an_index_the_live_tools_say_so_plainly(plan, windows):
    """The stored-investigation replay path may have no log tool. The model must
    be told the capability is missing, not handed an obscure failure."""
    bindings = make(None, plan=plan, windows=windows)
    for name in ("fetch_logs", "logs_around", "first_occurrence"):
        result = await bindings.execute(name, {"timestamp": at(600).isoformat(),
                                               "contains": "x"})
        assert "not available in this context" in result.text


@pytest.mark.asyncio
async def test_a_failing_index_does_not_end_the_investigation(plan, windows):
    """A tool fault is reported to the loop, which can try something else. An
    exception here would discard everything gathered so far."""
    class Broken(FakeLogTool):
        async def fetch(self, *a, **k):
            raise RuntimeError("opensearch unreachable")

    result = await make(Broken(), plan=plan, windows=windows).execute("fetch_logs", {})
    assert "failed" in result.text.lower()
    assert "opensearch unreachable" in result.text


# ------------------------------------------------------------------ contract
def test_every_live_tool_is_registered_with_the_model():
    """A tool the model is never told about cannot be called."""
    names = {spec.name for spec in ToolBindings.SPECS}
    assert {"fetch_logs", "logs_around", "first_occurrence"} <= names


def test_the_tool_schema_stays_serialisable():
    """It is rendered into the system prompt on every step."""
    import json
    assert json.loads(ToolBindings.schema())
