from __future__ import annotations

from app.models.analysis import AnalystChoice, Candidate, CauseCategory
from app.models.evidence import EvidenceBundle
from app.models.signals import Severity, Signal, SignalType
from app.pipeline.verify import verify
from tests.conftest import at


def signal(signal_type: SignalType, *, service: str, onset: int) -> Signal:
    return Signal(id=f"sig:{signal_type.value}:{service}", type=signal_type,
                  severity=Severity.HIGH, service=service, first_seen=at(onset),
                  description="test signal")


def candidate(cid: str, category: CauseCategory, *, score: float, onset: int,
              supporting: list[str] | None = None) -> Candidate:
    return Candidate(id=cid, category=category, hypothesis=f"{category.value} hypothesis",
                     score=score, onset=at(onset), supporting_signals=supporting or [],
                     rationale="because")


def complete_evidence() -> EvidenceBundle:
    """Evidence with no declared gaps.

    A bare EvidenceBundle() reports "no application logs matched", which is
    itself a gap and correctly caps confidence — so tests about *other* caps
    need evidence that is actually complete.
    """
    from app.models.evidence import LogEvidence
    from tests.conftest import pattern

    return EvidenceBundle(logs=LogEvidence(patterns=[
        pattern("something went wrong", service="payment-api", count=10),
    ]))


def run(choice, candidates, signals, windows, index=None, warnings=None, evidence=None):
    return verify(
        choice=choice, candidates=candidates, signals=signals,
        evidence=evidence if evidence is not None else complete_evidence(),
        windows=windows,
        evidence_index=index if index is not None else {s.id: "x" for s in signals},
        timeline=["12:10:00Z something happened"], warnings=warnings or [],
    )


def test_incomplete_evidence_caps_confidence(windows):
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.APPLICATION_FAULT, score=0.9, onset=600,
                            supporting=[signals[0].id])]
    choice = AnalystChoice(candidate_id="cand:1", confidence=0.99, reasoning="sure")

    analysis = run(choice, candidates, signals, windows, evidence=EvidenceBundle())

    assert analysis.confidence <= 0.65
    assert any(issue.code == "incomplete_evidence" for issue in analysis.verification)


def test_invented_citations_are_stripped(windows):
    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.DEPENDENCY_FAILURE, score=0.8, onset=600,
                            supporting=[signals[0].id])]
    choice = AnalystChoice(candidate_id="cand:1", confidence=0.95, reasoning="db is down",
                           evidence_ids=[signals[0].id, "pat:made-up:deadbeef", "sig:INVENTED:x"])

    analysis = run(choice, candidates, signals, windows)

    assert "pat:made-up:deadbeef" not in analysis.evidence_ids
    assert "sig:INVENTED:x" not in analysis.evidence_ids
    assert signals[0].id in analysis.evidence_ids
    assert any(issue.code == "unresolvable_citations" for issue in analysis.verification)
    assert analysis.confidence <= 0.7, "fabricated citations must cost confidence"


def test_a_cause_that_postdates_the_symptom_is_downgraded(windows):
    signals = [
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=600),
        signal(SignalType.DEPLOYMENT_CHANGE, service="payment-api", onset=1200),
    ]
    candidates = [
        candidate("cand:1", CauseCategory.CHANGE_INDUCED, score=0.7, onset=1200,
                  supporting=[signals[1].id]),
    ]
    choice = AnalystChoice(candidate_id="cand:1", confidence=0.9, reasoning="the deploy did it")

    analysis = run(choice, candidates, signals, windows)

    assert any(issue.code == "effect_precedes_cause" for issue in analysis.verification)
    assert analysis.confidence <= 0.35


def test_a_cause_with_no_live_signals_falls_back_to_the_engine(windows):
    signals = [signal(SignalType.OOM_KILL, service="payment-api", onset=600)]
    engine_top = candidate("cand:1", CauseCategory.RESOURCE_EXHAUSTION, score=0.8, onset=600,
                           supporting=[signals[0].id])
    unsupported = candidate("cand:2", CauseCategory.SCHEDULING_FAILURE, score=0.3, onset=600,
                            supporting=["sig:SCHEDULING_FAILURE:ghost"])
    choice = AnalystChoice(candidate_id="cand:2", confidence=0.9, reasoning="scheduling")

    analysis = run(choice, [engine_top, unsupported], signals, windows)

    assert analysis.chosen_candidate_id == "cand:1"
    assert any(issue.code == "unsupported_cause" and issue.severity == "error"
               for issue in analysis.verification)


def test_no_llm_answer_falls_back_and_says_so(windows):
    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.DEPENDENCY_FAILURE, score=0.72, onset=600,
                            supporting=[signals[0].id])]

    analysis = run(None, candidates, signals, windows)

    assert analysis.analyst == "deterministic"
    assert analysis.chosen_candidate_id == "cand:1"
    assert analysis.confidence == 0.72
    assert any(issue.code == "llm_unavailable" for issue in analysis.verification)


def test_disagreement_with_the_engine_is_reported_not_hidden(windows):
    signals = [
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600),
        signal(SignalType.HTTP_5XX_BURST, service="checkout-api", onset=660),
    ]
    candidates = [
        candidate("cand:1", CauseCategory.DEPENDENCY_FAILURE, score=0.80, onset=600,
                  supporting=[signals[0].id]),
        candidate("cand:2", CauseCategory.APPLICATION_FAULT, score=0.40, onset=660,
                  supporting=[signals[1].id]),
    ]
    choice = AnalystChoice(candidate_id="cand:2", confidence=0.8, reasoning="app bug")

    analysis = run(choice, candidates, signals, windows)

    assert analysis.agrees_with_engine is False
    assert analysis.engine_top_candidate_id == "cand:1"
    assert any(issue.code == "engine_disagreement" for issue in analysis.verification)


def test_a_missing_baseline_caps_confidence(windows):
    windows.baseline = None
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.APPLICATION_FAULT, score=0.6, onset=600,
                            supporting=[signals[0].id])]
    choice = AnalystChoice(candidate_id="cand:1", confidence=0.99, reasoning="certain")

    analysis = run(choice, candidates, signals, windows)

    assert analysis.confidence <= 0.55
    assert any(issue.code == "no_baseline" for issue in analysis.verification)


def test_a_reasoning_that_echoes_the_hypothesis_is_not_duplicated(windows):
    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.DEPENDENCY_FAILURE, score=0.8, onset=600,
                            supporting=[signals[0].id])]
    choice = AnalystChoice(
        candidate_id="cand:1", confidence=0.8,
        reasoning="Dependency_failure hypothesis is supported by the outage signal on payment-db.",
    )
    analysis = run(choice, candidates, signals, windows)
    assert analysis.cause_summary == choice.reasoning
    assert analysis.cause_summary.lower().count("dependency_failure hypothesis") == 1


def test_next_steps_are_produced_even_when_the_model_offers_none(windows):
    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [candidate("cand:1", CauseCategory.DEPENDENCY_FAILURE, score=0.8, onset=600,
                            supporting=[signals[0].id])]
    candidates[0].service = "payment-db"
    choice = AnalystChoice(candidate_id="cand:1", confidence=0.8, reasoning="db down",
                           next_steps=[])

    analysis = run(choice, candidates, signals, windows)

    assert analysis.next_steps
    assert any("payment-db" in step for step in analysis.next_steps)
