from __future__ import annotations

import json

import pytest

from app.agents.analyst import AnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.llm.base import LLMClient, LLMResponse, PromptTruncated
from app.models.analysis import CauseCategory
from app.models.domain import ServiceDescriptor, SystemDescriptor
from app.models.evidence import EventEvidence, LogEvidence, MetricEvidence
from app.models.plan import InvestigationRequest
from app.pipeline.run import InvestigationPipeline
from tests.conftest import buckets, event, pattern, series

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
    """Answers the planner call and the selection call in order."""

    def __init__(self, *responses: str, truncate_on: int | None = None) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.truncate_on = truncate_on
        self.prompts: list[str] = []

    async def generate(self, *, system: str, prompt: str, schema=None) -> LLMResponse:
        self.calls += 1
        self.prompts.append(prompt)
        if self.truncate_on == self.calls:
            raise PromptTruncated("prompt exceeded num_ctx")
        text = self.responses.pop(0) if self.responses else "ok"
        return LLMResponse(text=text, prompt_tokens=900, output_tokens=60)

    async def available(self) -> bool:
        return True


class FakeLogTool:
    def __init__(self, evidence: LogEvidence, histogram_counts: list[int]) -> None:
        self.evidence = evidence
        self.counts = histogram_counts

    async def histogram(self, plan, window, interval="60s"):
        return buckets(self.counts)

    async def collect(self, plan, incident, baseline):
        return self.evidence


class FakeEventTool:
    def __init__(self, evidence: EventEvidence) -> None:
        self.evidence = evidence

    async def collect(self, plan, incident, baseline):
        return self.evidence


class FakeMetricTool:
    def __init__(self, evidence: MetricEvidence) -> None:
        self.evidence = evidence

    async def collect(self, plan, incident, baseline):
        return self.evidence


def dependency_outage_evidence():
    """payment-db is gone; payment-api and checkout-api report the fallout."""
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
        series("http_error_rate", [1.9, 2.0, 2.0], labels={"service": "checkout-api"},
               baseline=[0.01, 0.0, 0.01], unit="req/s"),
        series("http_request_rate", [2.0, 2.0, 2.0], labels={"service": "checkout-api"},
               baseline=[2.0, 2.0, 2.0], unit="req/s"),
    ])
    return logs, events, metrics


def build(llm: ScriptedLLM, logs, events, metrics, counts) -> InvestigationPipeline:
    return InvestigationPipeline(
        log_tool=FakeLogTool(logs, counts),
        event_tool=FakeEventTool(events),
        metric_tool=FakeMetricTool(metrics),
        orchestrator=OrchestratorAgent(llm),
        analyst=AnalystAgent(llm),
        registry=FakeRegistry(),
    )


PLAN_REPLY = json.dumps({"intent": "incident_investigation", "service": "checkout-api",
                         "duration": "1h", "goal": "why is checkout failing"})


@pytest.mark.asyncio
async def test_full_run_names_the_root_cause_not_the_symptom():
    logs, events, metrics = dependency_outage_evidence()
    selection = json.dumps({
        "candidate_id": "cand:1", "confidence": 0.85,
        "reasoning": "payment-db stopped answering and its callers failed in order.",
        "evidence_ids": ["sig:DEPENDENCY_UNAVAILABLE:payment-db"],
        "next_steps": ["check payment-db pods"],
    })
    llm = ScriptedLLM(PLAN_REPLY, selection, "payment-db went away and checkout failed.")
    pipeline = build(llm, logs, events, metrics, [0, 0, 1, 0, 1, 40, 45, 38, 41, 44])

    stages = []
    result = None
    async for stage_event in pipeline.run(InvestigationRequest(
        system_id="shopdemo", environment="staging", question="why is checkout failing?"
    )):
        stages.append(stage_event.stage)
        if stage_event.stage == "result":
            result = stage_event.data

    assert stages == ["plan", "windows", "evidence", "signals", "candidates",
                      "analysis", "verified", "result"]
    assert result is not None
    assert result["analysis"]["category"] == CauseCategory.DEPENDENCY_FAILURE.value
    assert result["analysis"]["incident_detected"] is True
    # The failing component, not the service that reported the error.
    chosen = next(c for c in result["candidates"]
                  if c["id"] == result["analysis"]["chosen_candidate_id"])
    assert chosen["service"] == "payment-db"
    assert result["analysis"]["timeline"], "a timeline should always be produced"
    assert result["analysis"]["narrative"]


@pytest.mark.asyncio
async def test_a_truncated_prompt_never_yields_an_answer():
    """The failure mode this whole design guards against.

    A truncated prompt means the model answered from a fraction of the evidence.
    That answer must be discarded, not surfaced with high confidence.
    """
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN_REPLY, truncate_on=2)
    pipeline = build(llm, logs, events, metrics, [0, 0, 1, 0, 1, 40, 45, 38, 41, 44])

    result = await pipeline.run_collect(InvestigationRequest(
        system_id="shopdemo", environment="staging", question="why is checkout failing?"
    ))

    assert result.analysis.analyst == "deterministic"
    assert result.analysis.category is CauseCategory.DEPENDENCY_FAILURE
    codes = {issue.code for issue in result.analysis.verification}
    # Distinct from a plain outage: the fix is num_ctx, not restarting Ollama.
    assert "prompt_truncated" in codes
    assert "llm_unavailable" in codes, "the run must also record that it fell back"
    truncation = next(i for i in result.analysis.verification if i.code == "prompt_truncated")
    assert truncation.severity == "error"


@pytest.mark.asyncio
async def test_a_quiet_system_reports_no_incident():
    llm = ScriptedLLM(
        json.dumps({"intent": "health_check", "service": None, "duration": "30m",
                    "goal": "is everything ok"}),
        json.dumps({"candidate_id": "cand:1", "confidence": 0.7,
                    "reasoning": "nothing crossed a threshold", "evidence_ids": []}),
        "Everything looks normal.",
    )
    logs = LogEvidence(
        patterns=[pattern("Payment processed successfully", service="payment-api",
                          count=900, baseline_count=880, level="INFO")],
        totals_by_level={"INFO": 900},
        baseline_totals_by_level={"INFO": 880},
        total_documents=900, baseline_documents=880,
    )
    pipeline = build(llm, logs, EventEvidence(), MetricEvidence(series=[]),
                     [0, 0, 0, 0, 0, 0, 0, 0])

    result = await pipeline.run_collect(InvestigationRequest(
        system_id="shopdemo", environment="staging", question="is everything healthy?"
    ))

    assert result.analysis.incident_detected is False
    assert result.analysis.category is CauseCategory.NO_INCIDENT


@pytest.mark.asyncio
async def test_the_prompt_carries_candidate_ids_and_stays_within_budget():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN_REPLY,
                      json.dumps({"candidate_id": "cand:1", "confidence": 0.8,
                                  "reasoning": "db down", "evidence_ids": []}),
                      "narrative")
    pipeline = build(llm, logs, events, metrics, [0, 0, 1, 0, 1, 40, 45, 38, 41, 44])

    await pipeline.run_collect(InvestigationRequest(
        system_id="shopdemo", environment="staging", question="why is checkout failing?"
    ))

    selection_prompt = llm.prompts[1]
    assert "cand:1" in selection_prompt
    assert "CANDIDATE EXPLANATIONS" in selection_prompt
    assert "SIGNALS DETECTED" in selection_prompt
    # Roughly 4 chars per token; the budget exists so the prompt never approaches
    # the context window in the first place.
    assert len(selection_prompt) < 24_000, "evidence budgets should bound the prompt"
