from __future__ import annotations

from app.models.analysis import CauseCategory
from app.models.evidence import EvidenceBundle, LogEvidence
from app.models.signals import Magnitude, Severity, Signal, SignalType
from app.pipeline.hypotheses import HypothesisEngine, is_ambiguous
from tests.conftest import at, pattern


def signal(signal_type: SignalType, *, service: str, onset: int,
           severity: Severity = Severity.HIGH, pod: str | None = None) -> Signal:
    return Signal(
        id=f"sig:{signal_type.value}:{service}",
        type=signal_type, severity=severity, service=service, pod=pod,
        first_seen=at(onset), description=f"{signal_type.value} on {service}",
        magnitude=Magnitude(baseline=1.0, incident=10.0, unit="x", ratio=10.0),
    )


def test_root_cause_beats_the_symptom_it_produced(plan, windows):
    """The whole point of the dependency chain.

    payment-db goes down at t+600; checkout-api starts erroring at t+660. The
    right answer is the database, not the service that reported the failure.
    """
    signals = [
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600,
               severity=Severity.CRITICAL),
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=660),
        signal(SignalType.HTTP_5XX_BURST, service="checkout-api", onset=680),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is CauseCategory.DEPENDENCY_FAILURE
    assert candidates[0].service == "payment-db"


def test_a_cause_that_starts_after_the_symptom_is_demoted(plan, windows):
    # Errors at t+600, deployment change at t+900. The change cannot have caused
    # errors that predate it.
    signals = [
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600),
        signal(SignalType.DEPLOYMENT_CHANGE, service="payment-api", onset=900,
               severity=Severity.MEDIUM),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is not CauseCategory.CHANGE_INDUCED


def test_a_change_shortly_before_the_symptom_is_a_suspect(plan, windows):
    signals = [
        signal(SignalType.DEPLOYMENT_CHANGE, service="payment-api", onset=600,
               severity=Severity.MEDIUM),
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=720),
        signal(SignalType.HTTP_5XX_BURST, service="payment-api", onset=730),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert any(c.category is CauseCategory.CHANGE_INDUCED for c in candidates)


def test_memory_chain_orders_pressure_before_the_kill(plan, windows):
    signals = [
        signal(SignalType.MEMORY_PRESSURE, service="payment-api", onset=600),
        signal(SignalType.OOM_KILL, service="payment-api", onset=700, severity=Severity.CRITICAL),
        signal(SignalType.POD_RESTART, service="payment-api", onset=720),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is CauseCategory.RESOURCE_EXHAUSTION
    assert candidates[0].onset == at(600), "the chain should start at memory pressure"


def test_application_fault_is_demoted_when_infrastructure_explains_it(plan, windows):
    signals = [
        signal(SignalType.OOM_KILL, service="payment-api", onset=600, severity=Severity.CRITICAL),
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=660),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    fault = next(c for c in candidates if c.category is CauseCategory.APPLICATION_FAULT)
    assert fault.contradicting_signals, "the OOM should be recorded as arguing against"
    assert candidates[0].category is not CauseCategory.APPLICATION_FAULT


def test_pure_application_errors_stay_an_application_fault(plan, windows):
    signals = [
        signal(SignalType.HTTP_5XX_BURST, service="payment-api", onset=600),
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=605),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is CauseCategory.APPLICATION_FAULT


def test_crashloop_with_a_fatal_startup_log_reads_as_a_startup_failure(plan, windows):
    evidence = EvidenceBundle(logs=LogEvidence(patterns=[
        pattern("payment-api failed to initialise: configuration is invalid",
                service="payment-api", count=8, level="FATAL"),
    ]))
    signals = [signal(SignalType.CRASHLOOP, service="payment-api", onset=600,
                      severity=Severity.CRITICAL)]
    candidates = HypothesisEngine().generate(plan, windows, signals, evidence)
    assert candidates[0].category is CauseCategory.STARTUP_FAILURE


def test_readiness_failure_without_restarts_is_distinguished_from_a_crashloop(plan, windows):
    signals = [signal(SignalType.READINESS_FAILURE, service="payment-api", onset=600)]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is CauseCategory.READINESS_FAILURE


def test_traffic_surge_is_not_blamed_on_a_broken_service(plan, windows):
    signals = [
        signal(SignalType.TRAFFIC_SURGE, service="checkout-api", onset=600,
               severity=Severity.MEDIUM),
        signal(SignalType.LATENCY_DEGRADATION, service="checkout-api", onset=660,
               severity=Severity.MEDIUM),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    assert candidates[0].category is CauseCategory.LOAD_INCREASE


def test_no_signals_yields_an_explicit_no_incident(plan, windows):
    candidates = HypothesisEngine().generate(plan, windows, [], EvidenceBundle())
    assert len(candidates) == 1
    assert candidates[0].category is CauseCategory.NO_INCIDENT


def test_ambiguity_is_detected_when_the_top_two_are_close():
    from app.models.analysis import Candidate

    close = [Candidate(id="cand:1", category=CauseCategory.UNKNOWN, hypothesis="a", score=0.50),
             Candidate(id="cand:2", category=CauseCategory.UNKNOWN, hypothesis="b", score=0.45)]
    clear = [Candidate(id="cand:1", category=CauseCategory.UNKNOWN, hypothesis="a", score=0.80),
             Candidate(id="cand:2", category=CauseCategory.UNKNOWN, hypothesis="b", score=0.30)]
    assert is_ambiguous(close) is True
    assert is_ambiguous(clear) is False
