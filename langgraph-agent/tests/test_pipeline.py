from __future__ import annotations

import json

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.agents.react import ReActAgent
from app.llm.base import LLMClient, LLMResponse, LLMUnavailable, PromptTruncated
from app.models.answer import AnswerMode, CitationStatus
from app.models.domain import ServiceDescriptor, SystemDescriptor
from app.models.evidence import EventEvidence, LogEvidence, LogSnapshot, MetricEvidence
from app.models.plan import InvestigationRequest
from app.pipeline.run import InvestigationPipeline
from tests.conftest import T0, at, buckets, event, pattern, series

SYSTEM = SystemDescriptor(
    id="shopdemo", name="Shop Demo", environments=["staging"], namespaces=["shopdemo"],
    services=[ServiceDescriptor(name=n, log_count=500)
              for n in ("checkout-api", "payment-api", "payment-db")],
)


class FakeRegistry:
    async def require(self, system_id: str) -> SystemDescriptor:
        return SYSTEM

    async def all(self):
        return [SYSTEM]


class ScriptedLLM(LLMClient):
    """Replays a fixed list of replies, one per call."""

    def __init__(self, *responses: str, raise_on: dict[int, Exception] | None = None) -> None:
        self.responses = list(responses)
        self.raise_on = raise_on or {}
        self.calls = 0
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def generate(self, *, system: str, prompt: str, schema=None) -> LLMResponse:
        self.calls += 1
        self.prompts.append(prompt)
        self.systems.append(system)
        if self.calls in self.raise_on:
            raise self.raise_on[self.calls]
        text = self.responses.pop(0) if self.responses else "{}"
        return LLMResponse(text=text, prompt_tokens=900, output_tokens=80)

    async def available(self) -> bool:
        return True


class FakeLogTool:
    def __init__(self, evidence: LogEvidence, counts: list[int],
                 snapshot_errors: int = 4) -> None:
        self.evidence = evidence
        self.counts = counts
        self.snapshot_errors = snapshot_errors
        # Every window the cheap aggregation was asked for: one per elevated
        # stretch found by the sweep, plus one for the current-status probe.
        self.snapshots: list = []

    async def histogram(self, plan, window, interval="60s"):
        return buckets(self.counts)

    async def collect(self, plan, incident, baseline):
        return self.evidence

    async def snapshot(self, plan, window, **_kwargs):
        self.snapshots.append(window)
        return LogSnapshot(
            window=window,
            total_documents=500,
            by_level={"ERROR": self.snapshot_errors, "INFO": 496},
            errors_by_service={"payment-db": self.snapshot_errors},
            top_errors=[f"payment-db: connection refused (x{self.snapshot_errors})"],
        )


class FakeTool:
    def __init__(self, evidence) -> None:
        self.evidence = evidence

    async def collect(self, plan, incident, baseline):
        return self.evidence


def dependency_outage_evidence():
    logs = LogEvidence(
        patterns=[
            pattern("Upstream dependency payment-db failed: DependencyUnreachable",
                    service="payment-api", count=210, baseline_count=0, first=660),
            pattern("Upstream dependency payment-api failed: DependencyServerError",
                    service="checkout-api", count=205, baseline_count=0, first=700),
        ],
        totals_by_level={"ERROR": 415, "INFO": 30},
        baseline_totals_by_level={"ERROR": 2, "INFO": 900},
        total_documents=445, baseline_documents=902,
        dependency_edges={"checkout-api": ["payment-api"], "payment-api": ["payment-db"]},
    )
    events = EventEvidence(events=[
        event("Unhealthy", pod="payment-db-7d9f6b8c55-kx2qp", count=12, first=640),
    ])
    metrics = MetricEvidence(series=[
        series("dependency_failure_rate", [2.0, 2.1, 2.0],
               labels={"service": "payment-api", "dependency": "payment-db"},
               baseline=[0.0, 0.0, 0.0], unit="req/s"),
        series("dependency_request_rate", [2.0, 2.1, 2.0],
               labels={"service": "payment-api", "dependency": "payment-db"},
               baseline=[2.0, 2.0, 2.0], unit="req/s"),
    ])
    return logs, events, metrics


def build(llm, logs, events, metrics, counts) -> InvestigationPipeline:
    return InvestigationPipeline(
        log_tool=FakeLogTool(logs, counts),
        event_tool=FakeTool(events),
        metric_tool=FakeTool(metrics),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm, max_steps=4),
        registry=FakeRegistry(),
    )


PLAN = json.dumps({"intent": "incident_investigation", "service": None,
                   "duration": "1h", "goal": "why is checkout failing"})
COUNTS = [0, 0, 1, 0, 1, 40, 45, 38, 41, 44]


def ask(question="why is checkout failing?", *, start=None, end=None):
    """A request, optionally pinned to an explicit range.

    The fake histogram is anchored to a fixed date, and the sweep — unlike onset
    detection — only reports stretches that fall *inside the period asked
    about*. A test about what the sweep found therefore has to state that period
    rather than let the planner default to "the last hour", which lands nowhere
    near the fixture.
    """
    return InvestigationRequest(
        system_id="shopdemo", environment="staging", question=question,
        start_time=start.isoformat() if start else None,
        end_time=end.isoformat() if end else None,
    )


@pytest.mark.asyncio
async def test_a_full_run_reaches_a_verified_structured_answer():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        # A distinct tool, not the seeded get_signals: repeating that one is
        # caught by the repeat guard, never reaches the call log, and now counts
        # as concluding without investigating.
        json.dumps({"thought": "check which service sits deepest",
                    "action": "get_dependencies", "action_input": {"service_name": "all"},
                    "is_finished": False}),
        json.dumps({
            "thought": "payment-db is the deepest failing service",
            "action": None, "is_finished": True,
            "answer": {
                "headline": "payment-db became unavailable and its callers failed",
                "detail": "Calls to payment-db stopped succeeding; the services above it failed in turn.",
                "root_cause_service": "payment-db",
                "reasoning": [
                    {"claim": "calls to payment-db are failing",
                     "because": "the dependency failure rate matches the request rate",
                     "evidence_ids": ["sig:DEPENDENCY_UNAVAILABLE:payment-db"],
                     "kind": "observation"},
                ],
                "assumptions": [{"statement": "traffic stayed steady",
                                 "basis": "no traffic surge signal fired",
                                 "impact_if_wrong": "rates would not be comparable"}],
                "confidence": 0.8,
                "limitations": ["only the last hour was examined"],
                "next_steps": ["check payment-db pods"],
            },
        }),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    stages, result = [], None
    async for stage_event in pipeline.run(ask()):
        stages.append(stage_event.stage)
        if stage_event.stage == "result":
            result = stage_event.data

    # The topology is streamed first so the UI can draw the graph before a node
    # has run; the stage order after it is the order the graph executes in.
    assert stages[0] == "graph"
    assert stages[1:6] == ["plan", "windows", "evidence", "signals", "candidates"]
    assert "reasoning" in stages and "answer" in stages
    assert "llm" in stages, "the model's cost must be reported with the run"
    assert result is not None

    # The graph is the thing that ran, not a diagram drawn beside it: the nodes
    # it reports having visited are the nodes the answer came through.
    assert result["graph_path"][:5] == ["plan", "windows", "evidence", "signals",
                                        "candidates"]
    assert result["graph_path"][-2:] == ["verify", "finish"]
    assert "fallback" not in result["graph_path"], (
        "a run whose loop concluded must not route through the rule answer")
    assert [d["to"] for d in result["graph_decisions"]] == ["signals", "verify"]
    assert result["llm"]["requests"] >= 2, "one planning call plus the loop's"
    assert result["llm"]["model"]

    answer = result["answer"]
    assert answer["mode"] == AnswerMode.ROOT_CAUSE.value
    assert answer["root_cause_service"] == "payment-db"
    assert answer["reasoning"] and answer["assumptions"]
    assert answer["confidence_factors"], "confidence must explain itself"
    # the deterministic layers are no longer discarded
    assert result["signals"], "signals must reach the result"
    assert result["candidates"], "candidates must reach the result"


@pytest.mark.asyncio
async def test_the_loop_is_given_the_measured_signals_not_raw_averages():
    """The reason for reintegrating the engine: the loop must reason over figures
    that were already compared against a baseline and against resource limits."""
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "look at signals", "action": "get_signals",
                    "action_input": {}, "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed", "confidence": 0.7}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    observations = []
    async for stage_event in pipeline.run(ask()):
        if stage_event.stage == "reasoning" and stage_event.data.get("type") == "observation":
            observations.append(stage_event.data["text"])

    assert observations
    assert "DEPENDENCY_UNAVAILABLE" in observations[0]
    assert "baseline" in observations[0].lower()


@pytest.mark.asyncio
async def test_a_truncated_prompt_never_yields_a_confident_answer():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN, raise_on={2: PromptTruncated("exceeded num_ctx")})
    pipeline = build(llm, logs, events, metrics, COUNTS)

    result = await pipeline.run_collect(ask())

    assert result.answer is not None
    assert result.answer.confidence <= 0.35
    assert any("did not complete" in f.factor or "truncat" in f.factor.lower()
               for f in result.answer.confidence_factors)
    # the rules still carried an answer through
    assert result.candidates
    assert "degraded" in result.analysis.analyst


@pytest.mark.asyncio
async def test_an_unreachable_model_falls_back_to_the_rule_engine():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN, raise_on={2: LLMUnavailable("ollama is down")})
    pipeline = build(llm, logs, events, metrics, COUNTS)

    result = await pipeline.run_collect(ask())

    assert result.answer.headline, "the deterministic ranking should still answer"
    assert result.answer.root_cause_service == "payment-db"
    assert any("did not complete" in f.factor for f in result.answer.confidence_factors)


@pytest.mark.asyncio
async def test_repeated_tool_calls_do_not_burn_the_step_budget_silently():
    logs, events, metrics = dependency_outage_evidence()
    same_call = json.dumps({"thought": "again", "action": "get_signals",
                            "action_input": {}, "is_finished": False})
    llm = ScriptedLLM(PLAN, same_call, same_call, same_call, same_call)
    pipeline = build(llm, logs, events, metrics, COUNTS)

    texts = []
    async for stage_event in pipeline.run(ask()):
        if stage_event.stage == "reasoning" and stage_event.data.get("type") == "observation":
            texts.append(stage_event.data["text"])

    assert any("already called" in t for t in texts), (
        "a repeated call must be told it is repeating, not silently re-run"
    )


@pytest.mark.asyncio
async def test_a_data_extraction_question_returns_records_not_an_incident_report():
    """The generality requirement: 'show me the errors' is a retrieval request."""
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        json.dumps({"intent": "data_extraction", "service": "payment-api",
                    "duration": "1h", "goal": "list the payment-api errors"}),
        json.dumps({"thought": "retrieve the matching records",
                    "action": "search_logs",
                    "action_input": {"query": "dependency", "service_name": "payment-api"},
                    "is_finished": False}),
        json.dumps({"thought": "found them", "action": None, "is_finished": True,
                    "answer": {"headline": "1 distinct error pattern matched, 210 occurrences",
                               "confidence": 0.9}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    result = await pipeline.run_collect(
        ask("show me the dependency errors from payment-api")
    )

    assert result.answer.mode is AnswerMode.DATA_EXTRACTION
    assert result.answer.table is not None, "an extraction answer must carry the records"
    assert result.answer.table.total_matched == 210
    assert "occurrences" in result.answer.table.columns


@pytest.mark.asyncio
async def test_an_aggregation_question_returns_a_breakdown():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        json.dumps({"intent": "aggregation", "service": None, "duration": "1h",
                    "goal": "how many errors by service"}),
        json.dumps({"thought": "count them", "action": "count_logs",
                    "action_input": {"group_by": "service"}, "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "415 errors across 2 services",
                               "confidence": 0.9}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    result = await pipeline.run_collect(ask("how many errors per service?"))

    assert result.answer.mode is AnswerMode.AGGREGATION
    assert result.answer.table is not None
    assert result.answer.table.columns[0] == "service"


@pytest.mark.asyncio
async def test_invented_citations_survive_into_the_answer_as_unresolved():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        # Root-cause answers must now be preceded by at least one real tool call;
        # a conclusion drawn from nothing is refused once.
        json.dumps({"thought": "check the call graph first",
                    "action": "get_dependencies", "action_input": {"service_name": "all"},
                    "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed",
                               "root_cause_service": "payment-db",
                               "confidence": 0.95,
                               "reasoning": [{"claim": "db down",
                                              "evidence_ids": ["sig:MADE_UP:nothing"]}]}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    result = await pipeline.run_collect(ask())

    bad = [c for c in result.answer.citations if c.status is CitationStatus.UNRESOLVED]
    assert bad and bad[0].id == "sig:MADE_UP:nothing"
    assert result.answer.confidence <= 0.6


@pytest.mark.asyncio
async def test_the_signals_are_in_front_of_the_model_before_it_asks_for_them():
    """The loop used to be free to never call get_signals, and it took that
    freedom: one live run opened with a log search, got nothing, and answered
    "no log patterns matched" while six signals — three of them traffic surges
    measured from metrics — sat uncollected. Whether the deterministic layer's
    output is consulted is not a decision worth delegating to a 7B model."""
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        # The model finishes immediately, without ever calling a tool.
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed", "confidence": 0.7}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    observations = []
    async for stage_event in pipeline.run(ask()):
        if stage_event.stage == "reasoning" and stage_event.data.get("type") == "observation":
            observations.append(stage_event.data)

    assert observations, "signals must be supplied even when no tool is called"
    assert observations[0]["automatic"] is True
    assert observations[0]["step"] == 0
    assert "DEPENDENCY_UNAVAILABLE" in observations[0]["text"]
    assert observations[0]["evidence_ids"], "the IDs must be citable"

    # and the model saw them in its prompt, not only the UI
    react_prompt = llm.prompts[1]
    assert "provided automatically" in react_prompt
    assert "DEPENDENCY_UNAVAILABLE" in react_prompt


@pytest.mark.asyncio
async def test_re_requesting_the_seeded_signals_does_not_cost_a_step():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "let me check signals", "action": "get_signals",
                    "action_input": {"service_name": "all"}, "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed", "confidence": 0.7}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    texts = []
    async for stage_event in pipeline.run(ask()):
        if stage_event.stage == "reasoning" and stage_event.data.get("type") == "observation":
            texts.append(stage_event.data["text"])

    assert "already called" in texts[1], "the repeat guard should catch the re-ask"


@pytest.mark.asyncio
async def test_a_run_reports_the_whole_range_and_the_present_moment():
    """The two additions, end to end through the graph.

    A question whose range holds a second, separate failure must come back
    describing both — the deep analysis of one, and a measurement of the other —
    and must say what the system is doing now. Neither is asked of the model:
    both are attached from what the pipeline measured, so an answer that talks
    about one failure still carries the list of three.
    """
    logs, events, metrics = dependency_outage_evidence()
    # Quiet, a failure, quiet, and a second failure still running at the end.
    counts = ([1, 0, 1, 0, 1] * 3
              + [40, 45, 38, 41, 44]
              + [1, 0, 1, 0, 1] * 3
              + [55, 60, 58, 62])
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "what else happened in this range?",
                    "action": "get_episodes", "action_input": {},
                    "is_finished": False}),
        json.dumps({
            "thought": "payment-db is the deepest failing service",
            "action": None, "is_finished": True,
            "answer": {
                "headline": "payment-db became unavailable",
                "detail": "Calls to payment-db stopped succeeding.",
                "root_cause_service": "payment-db",
                "reasoning": [{"claim": "calls to payment-db are failing",
                               "evidence_ids": ["sig:DEPENDENCY_UNAVAILABLE:payment-db"],
                               "kind": "observation"}],
                "confidence": 0.8,
            },
        }),
    )
    log_tool = FakeLogTool(logs, counts, snapshot_errors=900)
    pipeline = InvestigationPipeline(
        log_tool=log_tool,
        event_tool=FakeTool(events),
        metric_tool=FakeTool(metrics),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm, max_steps=4),
        registry=FakeRegistry(),
    )

    result = None
    windows_stage = None
    async for stage_event in pipeline.run(
            ask("what went wrong?", start=T0, end=at(len(counts) * 60))):
        if stage_event.stage == "windows":
            windows_stage = stage_event.data
        if stage_event.stage == "result":
            result = stage_event.data

    assert result is not None

    # -- the whole range ---------------------------------------------------
    episodes = result["windows"]["episodes"]
    assert len(episodes) == 2, "both failures in the range are reported, not just one"
    assert sum(1 for e in episodes if e["primary"]) == 1
    assert episodes[-1]["ongoing"] is True
    assert result["windows"]["scanned"], "the period swept is recorded alongside the incident"
    assert result["answer"]["window_issues"], "the issue list reaches the answer"

    # Each stretch was described by its own cheap aggregation rather than a
    # second full investigation.
    assert all(e["breakdown_status"] == "ok" for e in episodes)
    assert all(e["services"] == ["payment-db"] for e in episodes)

    # -- the present moment ------------------------------------------------
    recent = result["recent_status"]
    assert recent is not None and recent["status"] == "critical"
    assert windows_stage["recent_status"]["status"] == "critical", (
        "the reader sees it while the run is still going, not only at the end")
    assert result["answer"]["recent_status"]["status"] == "critical"
    assert any("Right now" in l for l in result["answer"]["limitations"])

    # The probe measures against the clock, so it asked for a window ending now
    # rather than at the end of the period the question covered.
    from app.models.domain import utcnow
    latest = max(w.end for w in log_tool.snapshots)
    assert abs((latest - utcnow()).total_seconds()) < 30


@pytest.mark.asyncio
async def test_the_loop_is_told_about_the_other_failures_before_it_chooses():
    """Front-loaded rather than left for the model to ask about.

    Not front-loading costs a whole round trip — which re-sends the same
    transcript anyway — and risks the loop never asking, which is the failure
    being fixed. The listing goes in the opening observation; the detail stays
    behind get_episodes.
    """
    logs, events, metrics = dependency_outage_evidence()
    counts = ([1, 0, 1, 0, 1] * 3 + [40, 45, 38, 41, 44]
              + [1, 0, 1, 0, 1] * 3 + [55, 60, 58, 62])
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db went down", "confidence": 0.5}}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db went down", "confidence": 0.5}}),
    )
    pipeline = InvestigationPipeline(
        log_tool=FakeLogTool(logs, counts),
        event_tool=FakeTool(events),
        metric_tool=FakeTool(metrics),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm, max_steps=4),
        registry=FakeRegistry(),
    )

    async for _ in pipeline.run(ask("what went wrong?", start=T0,
                                    end=at(len(counts) * 60))):
        pass

    reasoning_prompt = llm.prompts[-1]
    assert "SEPARATE elevated stretches" in reasoning_prompt
    assert "Whole period asked about" in reasoning_prompt
    assert "Stretch analysed in depth" in reasoning_prompt, (
        "the two windows are stated apart, so an answer about part of the range "
        "does not read as an answer about all of it")
    assert "Current status, measured separately" in reasoning_prompt
