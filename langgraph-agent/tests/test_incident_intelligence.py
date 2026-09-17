"""Operational memory must be scoped and tested against current measurements."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.analysis import Candidate, CauseCategory, InvestigationWindows
from app.models.answer import AnswerMode
from app.models.domain import TimeWindow
from app.models.evidence import EvidenceBundle, LogEvidence, LogPattern
from app.models.intelligence import HistoricalMatch, IncidentFingerprint
from app.models.plan import Intent, InvestigationPlan
from app.models.signals import Severity, Signal, SignalType
from app.pipeline.intelligence import (
    build_fingerprint, build_topology, classify_roles, rank_history,
    similarity, evaluate_hypotheses, historical_candidates,
)
from app.pipeline.answer_check import verify_answer
from app.pipeline.hypotheses import HypothesisEngine
from app.store.investigations import InvestigationStore


NOW = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)


def plan(system="shop", environment="prod"):
    return InvestigationPlan(
        intent=Intent.INCIDENT_INVESTIGATION, system_id=system,
        system_name=system, environment=environment,
        requested_window=TimeWindow(start=NOW - timedelta(hours=1), end=NOW),
        tools=["logs", "events", "metrics"], goal="Why did checkout fail?",
    )


def signal(sid, kind, service, minute):
    return Signal(id=sid, type=kind, severity=Severity.HIGH,
                  description=f"{kind.value} on {service}", service=service,
                  first_seen=NOW + timedelta(minutes=minute))


def current():
    signals = [
        signal("sig:dep", SignalType.DEPENDENCY_UNAVAILABLE, "payment-db", 1),
        signal("sig:http", SignalType.HTTP_5XX_BURST, "checkout-api", 3),
    ]
    evidence = EvidenceBundle(logs=LogEvidence(
        patterns=[LogPattern(id="pat:timeout", template="ConnectionTimeout 123",
                             example="ConnectionTimeout 123", level="ERROR",
                             service="checkout-api", count=20)],
        dependency_edges={"checkout-api": ["payment-db"]},
    ))
    topology = build_topology(plan(), evidence, signals)
    return signals, evidence, topology


def historical_doc(system="shop", environment="prod", signal_type="DEPENDENCY_UNAVAILABLE"):
    return {
        "id": f"inv-{system}-{environment}",
        "plan": {"system_id": system, "environment": environment},
        "signals": [{"type": signal_type, "service": "payment-db",
                     "first_seen": NOW.isoformat()},
                    {"type": "HTTP_5XX_BURST", "service": "checkout-api",
                     "first_seen": (NOW + timedelta(minutes=2)).isoformat()}],
        "analysis": {"category": "dependency_failure",
                     "cause_summary": "payment-db failed"},
        "answer": {"headline": "payment-db failed",
                   "root_cause_service": "payment-db"},
    }


def test_topology_and_fingerprint_use_current_scoped_observations():
    signals, evidence, topology = current()
    assert topology.system_id == "shop"
    assert [(e.source, e.target, e.relation) for e in topology.edges] == [
        ("service:checkout-api", "service:payment-db", "calls")]
    assert [(p.upstream_signal_id, p.downstream_signal_id)
            for p in topology.propagation] == [("sig:dep", "sig:http")]

    fingerprint = build_fingerprint(plan(), evidence, signals, topology)
    assert fingerprint.sequence == ["payment-db:DEPENDENCY_UNAVAILABLE",
                                    "checkout-api:HTTP_5XX_BURST"]
    assert fingerprint.dependency_edges == ["service:checkout-api>service:payment-db"]
    assert fingerprint.log_templates == ["connectiontimeout <n>"]


def test_history_never_crosses_system_or_environment_even_if_adapter_returns_it():
    signals, evidence, topology = current()
    fingerprint = build_fingerprint(plan(), evidence, signals, topology)
    docs = [historical_doc(), historical_doc("another"),
            historical_doc("shop", "staging")]
    matches = rank_history(fingerprint, docs, minimum_score=0.0)
    assert [m.investigation_id for m in matches] == ["inv-shop-prod"]
    assert matches[0].root_cause_service == "payment-db"
    assert matches[0].note.startswith("Prior conclusion")


def test_similarity_uses_signal_order_and_does_not_use_past_rca():
    base = IncidentFingerprint(system_id="shop", environment="prod",
                               affected_services=["payment-db", "checkout-api"],
                               signal_types=["DEPENDENCY_UNAVAILABLE", "HTTP_5XX_BURST"],
                               sequence=["payment-db:DEPENDENCY_UNAVAILABLE",
                                         "checkout-api:HTTP_5XX_BURST"])
    same = base.model_copy(deep=True)
    reversed_order = base.model_copy(update={"sequence": list(reversed(base.sequence))})
    score_same, _ = similarity(base, same)
    score_reversed, _ = similarity(base, reversed_order)
    assert score_same > score_reversed


def test_a_past_rca_becomes_only_a_supported_low_ranked_hypothesis():
    signals, _, _ = current()
    prior = HistoricalMatch(investigation_id="inv-old", score=0.9,
                            cause_category="dependency_failure",
                            root_cause_service="payment-db")
    added = historical_candidates([], [prior], signals)
    assert len(added) == 1
    assert added[0].origin == "history"
    assert added[0].score <= 0.2
    assert added[0].supporting_signals == ["sig:dep"]
    assert historical_candidates([], [prior], signals[1:]) == []


def test_hypothesis_is_rejected_when_cause_follows_symptom():
    signals, _, topology = current()
    candidate = Candidate(id="cand:1", category=CauseCategory.DEPENDENCY_FAILURE,
                          hypothesis="payment-db failed", service="payment-db",
                          onset=NOW + timedelta(minutes=5),
                          supporting_signals=["sig:dep"])
    result = evaluate_hypotheses([candidate], signals, topology)[0]
    assert result.verdict == "reject"
    assert any(c.outcome == "contradicted" and "before downstream" in c.expectation
               for c in result.checks)
    roles = classify_roles(signals, [candidate], [result])
    assert not any(role.role == "root_cause" for role in roles)

    window = TimeWindow(start=NOW, end=NOW + timedelta(minutes=30))
    answer = verify_answer(
        raw={"headline": "payment-db caused the outage",
             "root_cause_service": "payment-db", "confidence": 0.95},
        mode=AnswerMode.ROOT_CAUSE, signals=signals, candidates=[candidate],
        evidence=EvidenceBundle(),
        windows=InvestigationWindows(requested=window, incident=window,
                                     baseline=TimeWindow(start=NOW - timedelta(minutes=30),
                                                         end=NOW)),
        exposed_ids=set(), hypothesis_tests=[result],
    )
    assert answer.confidence <= 0.4
    assert any("rejected" in item.lower() for item in answer.limitations)


def test_crashloop_and_rollout_timing_do_not_establish_an_exit_cause():
    window = TimeWindow(start=NOW, end=NOW + timedelta(minutes=30))
    windows = InvestigationWindows(
        requested=window, incident=window,
        baseline=TimeWindow(start=NOW - timedelta(minutes=30), end=NOW))
    signals = [
        signal("sig:CRASHLOOP:doe-pod", SignalType.CRASHLOOP,
               "doe-result-service", 2),
        signal("sig:DEPLOYMENT_CHANGE:doe-result-service",
               SignalType.DEPLOYMENT_CHANGE, "doe-result-service", 2),
    ]
    evidence = EvidenceBundle()
    candidates = HypothesisEngine().generate(plan(), windows, signals, evidence)
    startup = next(c for c in candidates if c.category is CauseCategory.STARTUP_FAILURE)
    assert "exit reason is unknown" in startup.hypothesis
    tests = evaluate_hypotheses(candidates, signals,
                                build_topology(plan(), evidence, signals))
    assert next(t for t in tests if t.candidate_id == startup.id).verdict == "uncertain"
    assert not any(r.role == "root_cause" for r in classify_roles(signals, candidates, tests))

    answer = verify_answer(
        raw={"headline": "The deployment caused doe-result-service to crashloop.",
             "detail": "Both began at the same reported time.",
             "root_cause_service": "doe-result-service", "confidence": 0.9,
             "reasoning": [{"claim": "The rollout triggered the crashloop",
                            "evidence_ids": [s.id for s in signals]}]},
        mode=AnswerMode.ROOT_CAUSE, signals=signals, candidates=candidates,
        evidence=evidence, windows=windows,
        exposed_ids={s.id for s in signals}, hypothesis_tests=tests)
    assert answer.root_cause_service is None
    assert "not established" in answer.headline
    assert "matching timestamps" in answer.detail
    assert "unconfirmed" in answer.reasoning[0].claim
    assert answer.confidence <= 0.4
    assert "termination reason" in answer.next_steps[0].label


def test_a_cited_fatal_startup_log_preserves_a_supported_crashloop_explanation():
    window = TimeWindow(start=NOW, end=NOW + timedelta(minutes=30))
    windows = InvestigationWindows(
        requested=window, incident=window,
        baseline=TimeWindow(start=NOW - timedelta(minutes=30), end=NOW))
    signals = [signal("sig:CRASHLOOP:doe-pod", SignalType.CRASHLOOP,
                      "doe-result-service", 2)]
    evidence = EvidenceBundle(logs=LogEvidence(patterns=[LogPattern(
        id="pat:startup", template="failed to initialize database client",
        example="failed to initialize database client", level="FATAL",
        service="doe-result-service", count=3)]))
    candidates = HypothesisEngine().generate(plan(), windows, signals, evidence)
    startup = next(c for c in candidates if c.category is CauseCategory.STARTUP_FAILURE)
    tests = evaluate_hypotheses(candidates, signals,
                                build_topology(plan(), evidence, signals))
    assert next(t for t in tests if t.candidate_id == startup.id).verdict == "keep"
    answer = verify_answer(
        raw={"headline": "doe-result-service exits because database initialization fails",
             "root_cause_service": "doe-result-service", "confidence": 0.7,
             "reasoning": [{"claim": "The startup log records the failure",
                            "evidence_ids": ["pat:startup"]}]},
        mode=AnswerMode.ROOT_CAUSE, signals=signals, candidates=candidates,
        evidence=evidence, windows=windows,
        exposed_ids={signals[0].id, "pat:startup"}, hypothesis_tests=tests)
    assert answer.root_cause_service == "doe-result-service"
    assert "database initialization" in answer.headline


def test_a_live_log_citation_is_resolved_when_the_tool_exposed_it():
    window = TimeWindow(start=NOW, end=NOW + timedelta(minutes=30))
    windows = InvestigationWindows(requested=window, incident=window)
    crashloop = signal("sig:CRASHLOOP:doe-pod", SignalType.CRASHLOOP,
                       "doe-result-service", 2)
    answer = verify_answer(
        raw={"headline": "doe-result-service exits on startup",
             "root_cause_service": "doe-result-service",
             "reasoning": [{"claim": "The process exits after a startup error",
                            "evidence_ids": ["log:abc123"]}]},
        mode=AnswerMode.ROOT_CAUSE, signals=[crashloop], candidates=[],
        evidence=EvidenceBundle(), windows=windows,
        exposed_ids={crashloop.id, "log:abc123"})
    assert answer.root_cause_service == "doe-result-service"
    assert answer.citations[0].status.value == "resolved"


@pytest.mark.asyncio
async def test_history_query_filters_in_opensearch_before_ranking():
    class FakeClient:
        def __init__(self):
            self.query = None

        async def search(self, index, query):
            self.query = query
            return {"hits": {"hits": []}}

    client = FakeClient()
    store = InvestigationStore(client)
    assert await store.history_for_system("", "prod") == []
    await store.history_for_system("shop", "prod")
    filters = client.query["query"]["bool"]["filter"]
    assert {"term": {"plan.system_id": "shop"}} in filters
    assert {"term": {"plan.environment": "prod"}} in filters
    assert client.query["size"] <= 100


@pytest.mark.asyncio
async def test_durable_thread_memory_is_partitioned_by_system_and_environment():
    class FakeClient:
        def __init__(self):
            self.documents = {}

        async def index_document(self, index, document, doc_id=None):
            self.documents[(index, doc_id)] = document
            return {"result": "created"}

        async def get_document(self, index, doc_id):
            return self.documents.get((index, doc_id))

    store = InvestigationStore(FakeClient())
    messages = [{"role": "user", "content": "Why did checkout fail?"}]
    assert await store.save_thread_memory("shop", "prod", "thread-1", messages)
    assert await store.get_thread_memory("shop", "prod", "thread-1") == messages
    assert await store.get_thread_memory("other", "prod", "thread-1") == []
    assert await store.get_thread_memory("shop", "staging", "thread-1") == []
