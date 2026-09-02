"""
Tests for the evaluation harness's scoring.

These exist because the harness spent a long stretch reporting numbers it had
made up: `score_one` fetched a real investigation and then filled the scorecard
with constants — `detected_signals = []`, `actual_cause = "Unknown"`,
`confidence = 1.0`. Every scenario read as 0% recall and a failed cause whatever
the pipeline did, so a genuine regression and a healthy run produced identical
reports.

A harness that does not read its own subject is worse than none: it produces
numbers, and numbers get believed.
"""
from __future__ import annotations

import pytest

from app.models.analysis import (
    Analysis,
    CauseCategory,
    InvestigationResult,
    InvestigationWindows,
)
from app.models.answer import StructuredAnswer
from app.models.domain import TimeWindow
from app.models.plan import Intent, InvestigationPlan
from app.models.signals import Severity, Signal, SignalType
from eval.run_eval import ScenarioScore, score_one
from tests.conftest import T0, at


class FakePipeline:
    def __init__(self, result: InvestigationResult) -> None:
        self._result = result

    async def run_collect(self, request) -> InvestigationResult:
        return self._result


def signal(signal_type: SignalType, service: str = "payment-api") -> Signal:
    return Signal(
        id=f"sig:{signal_type.value}:{service}",
        type=signal_type,
        severity=Severity.HIGH,
        service=service,
        description=f"{signal_type.value} on {service}",
    )


def result(*, signals, category, service, confidence=0.72,
           analyst="react", agrees=True) -> InvestigationResult:
    return InvestigationResult(
        id="inv-test0001",
        question="why is checkout failing?",
        plan=InvestigationPlan(
            intent=Intent.INCIDENT_INVESTIGATION,
            system_id="shopdemo", system_name="Shop Demo", environment="staging",
            requested_window=TimeWindow(start=T0, end=at(1800)),
        ),
        windows=InvestigationWindows(
            requested=TimeWindow(start=T0, end=at(1800)),
            incident=TimeWindow(start=T0, end=at(1800), label="incident"),
        ),
        signals=signals,
        analysis=Analysis(category=category, analyst=analyst,
                          agrees_with_engine=agrees, confidence=confidence),
        answer=StructuredAnswer(headline="payment-db stopped answering",
                                root_cause_service=service, confidence=confidence),
    )


SPEC = {
    "expected_cause": "dependency_failure",
    "expected_signals": ["DEPENDENCY_UNAVAILABLE", "ERROR_RATE_SPIKE"],
    "expected_service": "payment-db",
}


async def run(res: InvestigationResult) -> ScenarioScore:
    return await score_one(FakePipeline(res), "db-outage", SPEC,
                           "shopdemo", "staging", "15m")


# ------------------------------------------------------------------ the fix
@pytest.mark.asyncio
async def test_detected_signals_come_from_the_run():
    """The regression itself. `detected_signals` was a hardcoded empty list, so
    recall was structurally 0% and the harness's own headline metric — the one
    its docstring calls "the leading indicator" — was always zero."""
    score = await run(result(
        signals=[signal(SignalType.DEPENDENCY_UNAVAILABLE, "payment-db"),
                 signal(SignalType.ERROR_RATE_SPIKE)],
        category=CauseCategory.DEPENDENCY_FAILURE, service="payment-db"))

    assert score.detected_signals == ["DEPENDENCY_UNAVAILABLE", "ERROR_RATE_SPIKE"]
    assert score.recall == 1.0
    assert score.missing_signals == []


@pytest.mark.asyncio
async def test_a_partial_detection_scores_partial_recall():
    score = await run(result(
        signals=[signal(SignalType.ERROR_RATE_SPIKE)],
        category=CauseCategory.DEPENDENCY_FAILURE, service="payment-db"))

    assert score.recall == 0.5
    assert score.missing_signals == ["DEPENDENCY_UNAVAILABLE"]


@pytest.mark.asyncio
async def test_the_cause_is_read_from_the_analysis():
    """`actual_cause` was the literal string "Unknown", so `cause_correct` could
    never be true and every scenario was reported as FAIL."""
    score = await run(result(
        signals=[signal(SignalType.DEPENDENCY_UNAVAILABLE, "payment-db")],
        category=CauseCategory.DEPENDENCY_FAILURE, service="payment-db"))

    assert score.actual_cause == "dependency_failure"
    assert score.cause_correct is True
    assert score.service_correct is True


@pytest.mark.asyncio
async def test_a_wrong_cause_is_reported_as_wrong():
    score = await run(result(
        signals=[signal(SignalType.ERROR_RATE_SPIKE)],
        category=CauseCategory.APPLICATION_FAULT, service="checkout-api"))

    assert score.cause_correct is False
    assert score.service_correct is False
    assert score.actual_service == "checkout-api"


@pytest.mark.asyncio
async def test_confidence_is_the_verified_one_not_a_constant():
    """It was pinned to 1.0 — the single most misleading value available, since
    the verifier's entire job is to refuse confidence the run cannot support."""
    score = await run(result(
        signals=[signal(SignalType.ERROR_RATE_SPIKE)],
        category=CauseCategory.APPLICATION_FAULT, service="payment-api",
        confidence=0.35))
    assert score.confidence == 0.35


@pytest.mark.asyncio
async def test_a_degraded_run_is_not_reported_as_a_model_success():
    """When the loop fails the pipeline still answers, from the rule engine, and
    labels itself "react (degraded)". Scoring that as a normal run credits the
    model for a conclusion it had no part in."""
    score = await run(result(
        signals=[signal(SignalType.DEPENDENCY_UNAVAILABLE, "payment-db")],
        category=CauseCategory.DEPENDENCY_FAILURE, service="payment-db",
        analyst="react (degraded)", agrees=False))

    assert "degraded" in score.analyst
    assert score.agreed_with_engine is False


# --------------------------------------------------------------- subsumption
@pytest.mark.asyncio
async def test_a_stronger_signal_counts_as_the_weaker_one_it_implies():
    """CRASHLOOP means the pod restarted, so a scenario expecting POD_RESTART is
    satisfied by it. Without this the engine is penalised for being *more*
    precise than the ground truth asked for."""
    spec = {"expected_cause": "startup_failure",
            "expected_signals": ["POD_RESTART"], "expected_service": "payment-api"}
    score = await score_one(
        FakePipeline(result(signals=[signal(SignalType.CRASHLOOP)],
                            category=CauseCategory.STARTUP_FAILURE,
                            service="payment-api")),
        "crashloop", spec, "shopdemo", "staging", "15m")

    assert score.recall == 1.0


# -------------------------------------------------------------------- errors
@pytest.mark.asyncio
async def test_a_failed_investigation_is_recorded_rather_than_crashing_the_run():
    """One scenario blowing up must not lose the other five."""
    class Exploding:
        async def run_collect(self, request):
            raise RuntimeError("opensearch unreachable")

    score = await score_one(Exploding(), "db-outage", SPEC,
                            "shopdemo", "staging", "15m")
    assert score.error is not None
    assert "opensearch unreachable" in score.error
    assert score.duration_s > 0
