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


def test_latency_across_a_chain_blames_the_deepest_service_not_the_first_to_trip(plan, windows):
    """When a dependency slows down, every service above it looks slow too — and
    a caller can cross its p95 threshold *before* the dependency causing the
    delay does. Onset order alone therefore points at the symptom. The observed
    call graph settles it: the deepest affected service is the candidate root.
    """
    evidence = EvidenceBundle(logs=LogEvidence(dependency_edges={
        "checkout-api": ["payment-api"],
        "payment-api": ["payment-db"],
    }))
    signals = [
        # checkout-api trips first, but it is at the top of the chain
        signal(SignalType.LATENCY_DEGRADATION, service="checkout-api", onset=600,
               severity=Severity.MEDIUM),
        signal(SignalType.LATENCY_DEGRADATION, service="payment-api", onset=640,
               severity=Severity.MEDIUM),
        signal(SignalType.LATENCY_DEGRADATION, service="payment-db", onset=680,
               severity=Severity.MEDIUM),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, evidence)
    degradation = next(c for c in candidates
                       if c.category is CauseCategory.DEPENDENCY_DEGRADATION)
    assert degradation.service == "payment-db", (
        "the deepest service in the call graph is the root, not the first to trip"
    )


def test_depth_is_computed_from_the_observed_call_graph():
    evidence = LogEvidence(dependency_edges={
        "checkout-api": ["payment-api"],
        "payment-api": ["payment-db"],
    })
    assert evidence.depth_of("checkout-api") == 2
    assert evidence.depth_of("payment-api") == 1
    assert evidence.depth_of("payment-db") == 0
    assert evidence.depth_of("unknown-service") == 0
    assert evidence.depth_of(None) == 0


def test_a_cyclic_call_graph_does_not_hang():
    evidence = LogEvidence(dependency_edges={"a": ["b"], "b": ["a"]})
    assert evidence.depth_of("a") >= 0    # terminates rather than recursing forever


def test_an_application_fault_names_the_deepest_failing_service(plan, windows):
    """Regression from a live payment-5xx run: payment-api was injected with a
    60% error rate, checkout-api returned 5xx because its dependency did, and the
    engine blamed checkout-api — it took the first error signal, and the entry
    point is usually the loudest. Errors propagate upward, so the root is the
    deepest failing component in the call graph."""
    evidence = EvidenceBundle(logs=LogEvidence(dependency_edges={
        "checkout-api": ["payment-api"],
        "payment-api": ["payment-db"],
    }))
    signals = [
        signal(SignalType.HTTP_5XX_BURST, service="checkout-api", onset=600),
        signal(SignalType.HTTP_5XX_BURST, service="payment-api", onset=600),
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=610),
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=610),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, evidence)
    fault = next(c for c in candidates if c.category is CauseCategory.APPLICATION_FAULT)
    assert fault.service == "payment-api", "checkout-api is downstream of the real fault"
    assert "call graph" in fault.rationale


def test_a_crashloop_outranks_restatements_of_its_own_effect(plan, windows):
    """Regression from a live crashloop run. payment-api crashing on startup also
    makes it unavailable and makes calls to it fail, so three rules all fire on
    the same service — and the vaguest of them, 'payment-api slowed down', was
    ranked first at 0.83 while the actual explanation sat at 0.59.

    A crashloop explains both of the others, so they defer to it.
    """
    evidence = EvidenceBundle(logs=LogEvidence(
        dependency_edges={"checkout-api": ["payment-api"], "payment-api": ["payment-db"]},
        patterns=[pattern("payment-api failed to initialise: configuration is invalid",
                          service="payment-api", count=5, level="FATAL")],
    ))
    signals = [
        signal(SignalType.DEPENDENCY_DEGRADED, service="payment-api", onset=600),
        signal(SignalType.CRASHLOOP, service="payment-api", onset=657,
               severity=Severity.CRITICAL),
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-api", onset=660,
               severity=Severity.CRITICAL),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, evidence)

    assert candidates[0].category is CauseCategory.STARTUP_FAILURE
    by_category = {c.category: c for c in candidates}
    assert by_category[CauseCategory.DEPENDENCY_DEGRADATION].score < candidates[0].score
    assert by_category[CauseCategory.DEPENDENCY_FAILURE].score < candidates[0].score
    # and each says explicitly that the crashloop accounts for it
    assert by_category[CauseCategory.DEPENDENCY_FAILURE].contradicting_signals


def test_a_failing_dependency_is_not_described_as_slow(plan, windows):
    """DEPENDENCY_DEGRADED measures a failure ratio, not latency. Calling that
    'slowed down' is untrue and points at the wrong class of fix."""
    evidence = EvidenceBundle(logs=LogEvidence(dependency_edges={
        "checkout-api": ["payment-api"],
    }))
    signals = [signal(SignalType.DEPENDENCY_DEGRADED, service="payment-api", onset=600)]
    candidates = HypothesisEngine().generate(plan, windows, signals, evidence)
    degradation = next(c for c in candidates
                       if c.category is CauseCategory.DEPENDENCY_DEGRADATION)
    assert "slowed down" not in degradation.hypothesis
    assert "returning errors" in degradation.hypothesis


def test_a_pre_existing_condition_cannot_be_the_cause(plan, windows):
    """Regression from a live run: a ScalingReplicaSet event that was already
    present before the window — in fact the *previous* incident's recovery —
    scored 1.00 and was ranked the top cause, ahead of the real outage.

    Clamping a pre-existing onset to the window start (so it stops outranking
    things by being old) put it at the very front of the range instead, which
    handed it the full causal-precedence bonus. It has to be excluded from
    causal reasoning altogether.
    """
    stale = signal(SignalType.DEPLOYMENT_CHANGE, service="payment-db", onset=600,
                   severity=Severity.LOW)
    stale.pre_existing = True
    stale_probe = signal(SignalType.READINESS_FAILURE, service="loadgen", onset=600)
    stale_probe.pre_existing = True

    signals = [
        stale, stale_probe,
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=700,
               severity=Severity.CRITICAL),
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=720),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())

    assert candidates[0].category is CauseCategory.DEPENDENCY_FAILURE
    assert candidates[0].service == "payment-db"
    assert not any(c.category is CauseCategory.CHANGE_INDUCED for c in candidates)
    assert not any(c.service == "loadgen" for c in candidates)


def test_no_signals_yields_an_explicit_no_incident(plan, windows):
    candidates = HypothesisEngine().generate(plan, windows, [], EvidenceBundle())
    assert len(candidates) == 1
    assert candidates[0].category is CauseCategory.NO_INCIDENT


def test_ambiguity_is_detected_when_two_different_causes_are_close():
    from app.models.analysis import Candidate

    close = [
        Candidate(id="cand:1", category=CauseCategory.RESOURCE_EXHAUSTION,
                  service="payment-api", hypothesis="a", score=0.50),
        Candidate(id="cand:2", category=CauseCategory.APPLICATION_FAULT,
                  service="payment-api", hypothesis="b", score=0.45),
    ]
    clear = [
        Candidate(id="cand:1", category=CauseCategory.RESOURCE_EXHAUSTION,
                  service="payment-api", hypothesis="a", score=0.80),
        Candidate(id="cand:2", category=CauseCategory.APPLICATION_FAULT,
                  service="payment-api", hypothesis="b", score=0.30),
    ]
    assert is_ambiguous(close) is True
    assert is_ambiguous(clear) is False


def test_two_close_scores_for_the_same_cause_are_not_ambiguous():
    """They agree. Flagging them was firing the ambiguity warning on most runs
    and capping confidence for a decision the reader never actually had to make."""
    from app.models.analysis import Candidate

    same = [
        Candidate(id="cand:1", category=CauseCategory.DEPENDENCY_FAILURE,
                  service="payment-db", hypothesis="payment-db is down", score=0.60),
        Candidate(id="cand:2", category=CauseCategory.DEPENDENCY_FAILURE,
                  service="payment-db", hypothesis="payment-db stopped answering", score=0.58),
    ]
    assert is_ambiguous(same) is False


def test_duplicate_candidates_for_one_failure_are_merged(plan, windows):
    """A dependency going down surfaces twice — its scrape target drops and its
    callers' failure rate climbs — and each produced its own candidate."""
    signals = [
        Signal(id="sig:DEPENDENCY_UNAVAILABLE:payment-db", type=SignalType.DEPENDENCY_UNAVAILABLE,
               severity=Severity.CRITICAL, service="payment-db", first_seen=at(600),
               description="calls to payment-db are failing"),
        Signal(id="sig:DEPENDENCY_UNAVAILABLE:payment-db-target",
               type=SignalType.DEPENDENCY_UNAVAILABLE, severity=Severity.CRITICAL,
               service="payment-db", first_seen=at(610),
               description="payment-db stopped responding to scrapes"),
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=660),
    ]
    candidates = HypothesisEngine().generate(plan, windows, signals, EvidenceBundle())
    dependency = [c for c in candidates if c.category is CauseCategory.DEPENDENCY_FAILURE]
    assert len(dependency) == 1, "one failing component should yield one candidate"
    # and the merged candidate carries the evidence from both
    assert "sig:DEPENDENCY_UNAVAILABLE:payment-db" in dependency[0].supporting_signals
    assert "sig:DEPENDENCY_UNAVAILABLE:payment-db-target" in dependency[0].supporting_signals
    assert dependency[0].onset == at(600), "keeps the earliest onset of the merged pair"
