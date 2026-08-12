from __future__ import annotations

from app.models.answer import AnswerMode, CitationStatus
from app.models.evidence import EvidenceBundle, LogEvidence
from app.models.signals import Magnitude, Severity, Signal, SignalType
from app.pipeline.answer_check import verify_answer
from tests.conftest import at, pattern
from tests.test_hypotheses import signal


def complete_evidence() -> EvidenceBundle:
    """Evidence with no declared gaps, so tests about other caps are not masked."""
    return EvidenceBundle(logs=LogEvidence(patterns=[
        pattern("something went wrong", service="payment-api", count=10),
    ]))


def check(raw, *, signals=None, candidates=None, windows, evidence=None,
          exposed=None, mode=AnswerMode.ROOT_CAUSE, degraded=None):
    signals = signals if signals is not None else []
    return verify_answer(
        raw=raw, mode=mode, signals=signals, candidates=candidates or [],
        evidence=evidence if evidence is not None else complete_evidence(),
        windows=windows,
        exposed_ids=exposed if exposed is not None else {s.id for s in signals},
        degraded=degraded,
    )


def test_invented_citations_are_marked_not_silently_dropped(windows):
    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    answer = check({
        "headline": "payment-db went down",
        "confidence": 0.95,
        "reasoning": [{"claim": "db is down", "because": "scrapes failed",
                       "evidence_ids": [signals[0].id, "sig:INVENTED:x"]}],
    }, signals=signals, windows=windows)

    by_id = {c.id: c for c in answer.citations}
    assert by_id[signals[0].id].status is CitationStatus.RESOLVED
    assert by_id["sig:INVENTED:x"].status is CitationStatus.UNRESOLVED
    # kept, not deleted — a reader can see the model referenced nothing
    assert "sig:INVENTED:x" in by_id
    assert answer.confidence <= 0.6
    assert any("does not exist" in f.factor for f in answer.confidence_factors)


def test_a_copied_schema_placeholder_is_distinguished_from_a_fabrication(windows):
    """Observed live: the model emitted the literal `sig:...` from the schema.

    Both are rejected, but a copied placeholder is a formatting slip while an
    invented ID is a claim about evidence that does not exist. Treating them the
    same makes the run harder to diagnose and over-penalises the lesser fault.
    """
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    placeholder = check(
        {"headline": "x", "confidence": 0.9,
         "reasoning": [{"claim": "c", "evidence_ids": ["sig:..."]}]},
        signals=signals, windows=windows)
    fabricated = check(
        {"headline": "x", "confidence": 0.9,
         "reasoning": [{"claim": "c", "evidence_ids": ["sig:OOM_KILL:never-happened"]}]},
        signals=signals, windows=windows)

    assert any("placeholder" in f.factor for f in placeholder.confidence_factors)
    assert any("does not exist" in f.factor for f in fabricated.confidence_factors)
    assert fabricated.confidence < placeholder.confidence


def test_citing_nothing_is_not_penalised_when_there_is_nothing_to_cite(windows):
    """A healthy system produces no signals, so the correct answer references
    nothing. Demanding evidence for 'nothing happened' punishes the right answer."""
    answer = check({
        "headline": "The system is healthy.",
        "confidence": 0.7,
        "reasoning": [{"claim": "no signal crossed its threshold",
                       "kind": "observation", "evidence_ids": []}],
    }, signals=[], windows=windows, mode=AnswerMode.HEALTH_CHECK)

    assert not any("cite no evidence" in f.factor for f in answer.confidence_factors)
    assert not answer.citations, "nothing should be invented to fill the gap"
    assert answer.confidence >= 0.6


def test_a_reasoning_step_with_no_evidence_is_counted_against_it(windows):
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    answer = check({
        "headline": "payment-api is broken",
        "confidence": 0.99,
        "reasoning": [
            {"claim": "errors rose", "evidence_ids": [signals[0].id], "kind": "observation"},
            {"claim": "therefore the database is at fault", "kind": "inference"},
        ],
    }, signals=signals, windows=windows)

    assert len(answer.unsupported_claims) == 1
    assert answer.confidence <= 0.65


def test_confidence_is_rebuilt_not_taken_from_the_model(windows):
    """A model that claims 0.99 while citing nothing must not get 0.99."""
    answer = check({"headline": "everything is fine", "confidence": 0.99},
                   signals=[], windows=windows)
    assert answer.confidence <= 0.4
    assert answer.confidence_factors


def test_agreement_with_the_rule_engine_raises_confidence(windows):
    from app.models.analysis import Candidate, CauseCategory

    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [Candidate(id="cand:1", category=CauseCategory.DEPENDENCY_FAILURE,
                            service="payment-db", hypothesis="payment-db is down",
                            score=0.8)]
    agreeing = check({"headline": "payment-db is down", "confidence": 0.7,
                      "root_cause_service": "payment-db",
                      "reasoning": [{"claim": "db down", "evidence_ids": [signals[0].id]}]},
                     signals=signals, candidates=candidates, windows=windows)
    disagreeing = check({"headline": "checkout-api is broken", "confidence": 0.7,
                         "root_cause_service": "checkout-api",
                         "reasoning": [{"claim": "checkout broken",
                                        "evidence_ids": [signals[0].id]}]},
                        signals=signals, candidates=candidates, windows=windows)

    assert agreeing.confidence > disagreeing.confidence
    assert agreeing.cause_category == "dependency_failure"
    assert any("agrees" in f.factor for f in agreeing.confidence_factors)
    assert any("disagree" in f.factor for f in disagreeing.confidence_factors)


def test_a_bonus_cannot_lift_an_answer_above_its_own_cap(windows):
    """Regression: caps were applied in call order, so a later bonus undid them.

    A run whose reasoning loop never completed scored 0.5 because agreeing with
    the rule engine added 0.1 back on top of its own 0.35 ceiling. A cap is a
    statement about what the run can support; nothing may lift the answer past it.
    """
    from app.models.analysis import Candidate, CauseCategory

    signals = [signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600)]
    candidates = [Candidate(id="cand:1", category=CauseCategory.DEPENDENCY_FAILURE,
                            service="payment-db", hypothesis="payment-db is down",
                            score=0.8)]
    answer = check(
        {"headline": "payment-db is down", "confidence": 0.9,
         "root_cause_service": "payment-db",
         "reasoning": [{"claim": "down", "evidence_ids": [signals[0].id]}]},
        signals=signals, candidates=candidates, windows=windows,
        degraded="the reasoning loop did not complete",
    )

    assert answer.confidence <= 0.35
    # the agreement is still reported — it just cannot raise the score
    assert any(f.direction == "raises" for f in answer.confidence_factors)


def test_a_valid_tool_suggestion_becomes_a_runnable_step(windows):
    answer = check({
        "headline": "payment-db is slow", "confidence": 0.7,
        "next_steps": [{"label": "Check payment-db error logs",
                        "tool": "search_logs",
                        "tool_input": {"service_name": "payment-db", "level": "ERROR"}}],
    }, signals=[], windows=windows)

    step = answer.next_steps[0]
    assert step.kind == "tool"
    assert step.tool == "search_logs"
    assert step.tool_input == {"service_name": "payment-db", "level": "ERROR"}
    assert step.is_executable


def test_an_invented_tool_falls_back_to_a_follow_up_investigation(windows):
    """A button that fails when pressed is worse than no button. A tool the model
    made up is not offered as one — the step becomes a fresh question instead."""
    answer = check({
        "headline": "x", "confidence": 0.7,
        "next_steps": [{"label": "Inspect the database connection pool",
                        "tool": "get_connection_pool_stats", "tool_input": {}}],
    }, signals=[], windows=windows)

    step = answer.next_steps[0]
    assert step.kind == "investigation"
    assert step.tool is None
    assert step.question == "Inspect the database connection pool"


def test_unknown_tool_parameters_are_dropped_not_passed_through(windows):
    answer = check({
        "headline": "x", "confidence": 0.7,
        "next_steps": [{"label": "search", "tool": "search_logs",
                        "tool_input": {"service_name": "payment-db",
                                       "sql_injection": "; DROP TABLE"}}],
    }, signals=[], windows=windows)

    assert answer.next_steps[0].tool_input == {"service_name": "payment-db"}


def test_a_service_in_the_call_graph_that_was_never_examined_is_offered(windows):
    """The most useful follow-up there is, and the model rarely proposes it."""
    from app.models.evidence import EvidenceBundle, LogEvidence

    evidence = EvidenceBundle(logs=LogEvidence(
        patterns=[pattern("boom", service="payment-api", count=10)],
        dependency_edges={"payment-api": ["payment-db"]},
    ))
    answer = check({"headline": "x", "confidence": 0.7, "next_steps": []},
                   signals=[], windows=windows, evidence=evidence)

    derived = [s for s in answer.next_steps if "payment-db" in s.label]
    assert derived, "payment-db is in the call graph but produced no log patterns"
    assert derived[0].kind == "tool"
    assert derived[0].tool_input["service_name"] == "payment-db"


def test_plain_string_next_steps_from_older_runs_still_load(windows):
    from app.models.answer import StructuredAnswer

    revived = StructuredAnswer(**{"headline": "x", "next_steps": ["do the thing"]})
    assert revived.next_steps[0].label == "do the thing"
    assert revived.next_steps[0].kind == "manual"
    assert not revived.next_steps[0].is_executable


def test_a_disagreeing_answer_is_still_classified(windows):
    """Regression: the category was read only from the top-ranked candidate, so
    whenever the model disagreed the run was filed as 'unknown' — discarding the
    classification exactly when the disagreement made it worth recording, and
    making every such run score as wrong in the evaluation harness."""
    from app.models.analysis import Candidate, CauseCategory

    signals = [
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600),
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=660),
    ]
    candidates = [
        Candidate(id="cand:1", category=CauseCategory.DEPENDENCY_FAILURE,
                  service="payment-db", hypothesis="db down", score=0.8),
        Candidate(id="cand:2", category=CauseCategory.APPLICATION_FAULT,
                  service="payment-api", hypothesis="app fault", score=0.5),
    ]
    answer = check({"headline": "payment-api is at fault", "confidence": 0.7,
                    "root_cause_service": "payment-api",
                    "reasoning": [{"claim": "x", "evidence_ids": [signals[1].id]}]},
                   signals=signals, candidates=candidates, windows=windows)

    assert answer.cause_category == "application_fault"
    assert any("disagree" in f.factor for f in answer.confidence_factors)


def test_a_missing_baseline_caps_confidence(windows):
    windows.baseline = None
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    answer = check({"headline": "errors spiked", "confidence": 0.99,
                    "reasoning": [{"claim": "spike", "evidence_ids": [signals[0].id]}]},
                   signals=signals, windows=windows)

    assert answer.confidence <= 0.55
    assert any("baseline" in f.factor for f in answer.confidence_factors)


def test_a_degraded_run_is_capped_and_says_why(windows):
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    answer = check({"headline": "something broke", "confidence": 0.9,
                    "reasoning": [{"claim": "x", "evidence_ids": [signals[0].id]}]},
                   signals=signals, windows=windows,
                   degraded="the reasoning loop hit its step limit")

    assert answer.confidence <= 0.35
    assert any("step limit" in f.factor for f in answer.confidence_factors)


def test_an_answer_citing_nothing_still_points_at_the_strongest_evidence(windows):
    signals = [
        signal(SignalType.DEPENDENCY_UNAVAILABLE, service="payment-db", onset=600),
        signal(SignalType.ERROR_RATE_SPIKE, service="checkout-api", onset=660),
    ]
    signals[0].severity = Severity.CRITICAL
    answer = check({"headline": "payment-db failed", "confidence": 0.8},
                   signals=signals, windows=windows)

    inferred = [c for c in answer.citations if c.status is CitationStatus.INFERRED]
    assert inferred, "should fall back to the strongest signals rather than cite nothing"
    assert any("cited no usable evidence" in c.detail for c in inferred)


def test_no_signals_in_a_root_cause_question_is_stated_as_a_limitation(windows):
    answer = check({"headline": "probably the database", "confidence": 0.9},
                   signals=[], windows=windows)
    assert answer.confidence <= 0.4
    assert any("no signal crossed" in lim.lower() for lim in answer.limitations)


def test_an_empty_headline_produces_an_honest_failure(windows):
    answer = check({"confidence": 0.9}, signals=[], windows=windows)
    assert answer.confidence == 0.0
    assert "did not produce" in answer.headline


def test_evidence_gaps_are_appended_to_limitations(windows):
    # A bare bundle reports "no application logs matched", which is a real gap.
    answer = check({"headline": "all clear", "confidence": 0.9},
                   signals=[], windows=windows, evidence=EvidenceBundle())
    assert any("no application logs" in lim.lower() for lim in answer.limitations)
    assert answer.confidence <= 0.7


def test_assumptions_survive_in_both_shapes(windows):
    answer = check({
        "headline": "x", "confidence": 0.5,
        "assumptions": [
            {"statement": "traffic was steady", "basis": "no surge signal",
             "impact_if_wrong": "rates would not be comparable"},
            "the clocks are synchronised",
        ],
    }, signals=[], windows=windows)

    assert len(answer.assumptions) == 2
    assert answer.assumptions[0].impact_if_wrong
    assert answer.assumptions[1].statement == "the clocks are synchronised"


def test_an_uncited_claim_labelled_observation_does_not_escape_the_cap(windows):
    """Regression: `kind` was self-reported, and calling a claim an observation
    exempted it from needing evidence. A live run scored 80% on "No log patterns
    matched" with nothing cited, while six signals sat uncollected."""
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    answer = check({
        "headline": "The investigation examined the window.",
        "confidence": 0.8,
        "reasoning": [{"claim": "No log patterns matched service='all'",
                       "kind": "observation", "evidence_ids": []}],
    }, signals=signals, windows=windows)

    assert len(answer.unsupported_claims) == 1
    assert answer.confidence <= 0.65
    assert any("cite no evidence" in f.factor for f in answer.confidence_factors)


def test_reporting_nothing_found_while_signals_fired_is_scored_as_contradicted(windows):
    """The exact failure the user hit: a log search came back empty, the loop
    stopped there, and the answer read as an all-clear while metrics and events
    had already measured three spikes."""
    signals = [
        signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600),
        signal(SignalType.TRAFFIC_SURGE, service="checkout-api", onset=620),
    ]
    answer = check({
        "headline": "No log patterns matched the query, so nothing was found.",
        "confidence": 0.9,
        "reasoning": [{"claim": "searched all levels", "evidence_ids": [signals[0].id]}],
    }, signals=signals, windows=windows)

    assert answer.confidence <= 0.4
    assert any("signal(s) were measured" in f.factor for f in answer.confidence_factors)
    # and the reader is told which ones, not just handed a smaller number
    assert any("measured in the same window" in lim for lim in answer.limitations)
    assert any("TRAFFIC_SURGE" in lim for lim in answer.limitations)


def test_a_mid_argument_elimination_is_not_mistaken_for_an_all_clear(windows):
    """"No disk pressure was found" as a *step* is good reasoning. Only the
    headline — the answer itself — asserting emptiness is the contradiction."""
    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    answer = check({
        "headline": "payment-api began returning 5xx after its database stopped responding.",
        "confidence": 0.8,
        "reasoning": [
            {"claim": "errors rose sharply", "evidence_ids": [signals[0].id],
             "kind": "observation"},
            {"claim": "no disk pressure was found on any node",
             "evidence_ids": [signals[0].id], "kind": "elimination"},
        ],
    }, signals=signals, windows=windows)

    assert not any("signal(s) were measured" in f.factor for f in answer.confidence_factors)
    assert answer.confidence > 0.4


def test_a_candidate_that_names_no_component_neither_agrees_nor_disagrees(windows):
    """A system-wide candidate has no service. Comparing it against a named one
    produced "the rule engine ranked 'None' highest" — a penalty for nothing,
    phrased as though the engine had made a competing claim."""
    from app.models.analysis import Candidate, CauseCategory

    signals = [signal(SignalType.ERROR_RATE_SPIKE, service="payment-api", onset=600)]
    system_wide = Candidate(id="cand:1", category=CauseCategory.UNKNOWN, service=None,
                            score=0.4, hypothesis="something is off", rationale="x")
    answer = check({
        "headline": "payment-db stopped responding",
        "root_cause_service": "payment-db",
        "confidence": 0.8,
        "reasoning": [{"claim": "db down", "evidence_ids": [signals[0].id]}],
    }, signals=signals, candidates=[system_wide], windows=windows)

    assert not any("ranked" in f.factor for f in answer.confidence_factors)
