from __future__ import annotations

import json

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.llm.base import LLMClient, LLMResponse, LLMUnavailable
from app.models.domain import ServiceDescriptor, SystemDescriptor
from app.models.plan import Intent, InvestigationRequest
from app.tools.fingerprint import fingerprint


class FakeLLM(LLMClient):
    def __init__(self, payload: dict | str | None = None, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    async def generate(self, *, system: str, prompt: str, schema=None) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise LLMUnavailable("ollama is down")
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload or {})
        return LLMResponse(text=text, prompt_tokens=500, output_tokens=40)

    async def available(self) -> bool:
        return not self.fail


@pytest.fixture
def system() -> SystemDescriptor:
    return SystemDescriptor(
        id="shopdemo", name="Shop Demo",
        environments=["staging"],
        namespaces=["shopdemo"],
        services=[ServiceDescriptor(name=n, log_count=100)
                  for n in ("checkout-api", "payment-api", "payment-db", "loadgen")],
    )


def ask(question: str, **kwargs) -> InvestigationRequest:
    return InvestigationRequest(system_id="shopdemo", environment="staging",
                                question=question, **kwargs)


@pytest.mark.asyncio
async def test_a_hallucinated_service_is_rejected_not_queried(system):
    """The failure this exists to prevent: an invented name becomes a term filter
    that matches nothing, and the investigation reports all-clear."""
    llm = FakeLLM({"intent": "incident_investigation", "service": "billing-service",
                   "duration": "30m", "goal": "check billing"})
    plan = await OrchestratorAgent(llm).plan(ask("why is billing broken?"), system)

    assert plan.service is None
    assert any("billing-service" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_a_near_miss_service_name_is_resolved(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": "payment",
                   "duration": "30m", "goal": "payments"})
    plan = await OrchestratorAgent(llm).plan(ask("payment problems?"), system)

    # 'payment' is a substring of payment-api and payment-db, so it is ambiguous
    # and must not be guessed at.
    assert plan.service is None


@pytest.mark.asyncio
async def test_an_exact_service_name_is_kept(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": "payment-api",
                   "duration": "1h", "goal": "payment-api errors"})
    plan = await OrchestratorAgent(llm).plan(ask("why is payment-api failing?"), system)

    assert plan.service == "payment-api"
    assert plan.requested_window.minutes == pytest.approx(60, abs=1)


@pytest.mark.asyncio
async def test_an_unsupported_duration_falls_back_to_the_intent_default(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": None,
                   "duration": "since last Tuesday", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(ask("what broke?"), system)

    assert plan.requested_window.minutes == pytest.approx(60, abs=1)
    assert any("since last Tuesday" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_a_duration_stated_in_the_question_wins_over_a_bad_model_answer(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": None,
                   "duration": "nonsense", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(ask("errors in the last 15 minutes?"), system)

    assert plan.requested_window.minutes == pytest.approx(15, abs=1)


@pytest.mark.asyncio
async def test_the_planner_still_works_when_the_model_is_down(system):
    llm = FakeLLM(fail=True)
    plan = await OrchestratorAgent(llm).plan(ask("why is checkout failing?"), system)

    assert plan.planner == "heuristic"
    assert plan.intent is Intent.INCIDENT_INVESTIGATION
    assert plan.tools == ["logs", "events", "metrics"]


@pytest.mark.asyncio
async def test_non_json_from_the_model_is_survivable(system):
    llm = FakeLLM("I think you should look at the payment service.")
    plan = await OrchestratorAgent(llm).plan(ask("slow checkout?"), system)

    assert plan.planner == "heuristic"
    assert plan.intent is Intent.PERFORMANCE_REVIEW


@pytest.mark.asyncio
async def test_an_explicit_service_hint_overrides_the_models_guess(system):
    """The bug this guards against: the UI lets a user pick a service directly,
    and that choice must actually reach the plan, not just be a decoration."""
    llm = FakeLLM({"intent": "incident_investigation", "service": "checkout-api",
                   "duration": "1h", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(
        ask("what happened?", service_hint="payment-db"), system
    )
    assert plan.service == "payment-db"


@pytest.mark.asyncio
async def test_an_unmatched_service_hint_is_reported_not_silently_dropped(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": None,
                   "duration": "1h", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(
        ask("what happened?", service_hint="billing-service"), system
    )
    assert plan.service is None
    assert any("billing-service" in note for note in plan.notes)


@pytest.mark.asyncio
async def test_an_explicit_duration_override_beats_the_model(system):
    llm = FakeLLM({"intent": "incident_investigation", "service": None,
                   "duration": "24h", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(ask("what happened?", duration="15m"), system)

    assert plan.requested_window.minutes == pytest.approx(15, abs=1)


# --------------------------------------------------------------------------
def test_fingerprint_collapses_variable_parts():
    a = fingerprint("Payment failed after 1203ms for trace tr-9f2ab1c3d4e5")
    b = fingerprint("Payment failed after 87ms for trace tr-11223344aabb")
    assert a == b


def test_fingerprint_keeps_genuinely_different_messages_apart():
    a = fingerprint("Database connection timeout")
    b = fingerprint("Payment processed successfully")
    assert a != b


def test_fingerprint_masks_pod_names_and_addresses():
    template = fingerprint(
        "Readiness probe failed: Get http://10.42.0.54:8000/health from payment-api-69d7b68776-mqxxd"
    )
    assert "10.42.0.54" not in template
    assert "mqxxd" not in template


@pytest.mark.asyncio
async def test_asking_to_be_shown_items_beats_the_planner_calling_it_an_incident(system):
    """"what are the metric spikes for the last 15 minutes, I need a list of
    them" came back as a root-cause narrative. A request to *see* the items is
    unambiguous in a way intent inference is not, so it overrides the model."""
    llm = FakeLLM({"intent": "incident_investigation", "service": None,
                   "duration": "15m", "goal": "spikes"})
    plan = await OrchestratorAgent(llm).plan(
        InvestigationRequest(
            system_id="shopdemo", environment="staging",
            question="what are the metric spikes for last 15 minits. i need list of them"),
        system)

    assert plan.intent is Intent.DATA_EXTRACTION
    assert any("asks to be shown specific items" in note for note in plan.notes), \
        "the override must be recorded, not applied silently"


@pytest.mark.asyncio
async def test_asking_for_a_count_of_items_is_an_aggregation_not_an_extraction(system):
    llm = FakeLLM({"intent": "incident_investigation", "duration": "1h", "goal": "x"})
    plan = await OrchestratorAgent(llm).plan(
        InvestigationRequest(system_id="shopdemo", environment="staging",
                             question="show me how many errors payment-api logged"),
        system)
    assert plan.intent is Intent.AGGREGATION


@pytest.mark.asyncio
async def test_asking_for_the_root_cause_still_wins_over_the_word_show(system):
    """"show me the root cause" wants the analysis, not a table of rows."""
    llm = FakeLLM(None)
    plan = await OrchestratorAgent(llm).plan(
        InvestigationRequest(
            system_id="shopdemo", environment="staging",
            question="show me the root cause across logs, events and metrics"),
        system)
    assert plan.intent is Intent.INCIDENT_INVESTIGATION


@pytest.mark.asyncio
async def test_retrieval_wording_is_classified_without_the_model(system):
    """The heuristics used to have no keywords for extraction or aggregation at
    all, so every planner failure fell through to an incident investigation."""
    llm = FakeLLM(fail=True)
    plan = await OrchestratorAgent(llm).plan(
        InvestigationRequest(system_id="shopdemo", environment="staging",
                             question="list the warnings from checkout-api"),
        system)
    assert plan.intent is Intent.DATA_EXTRACTION
