"""Operational topology, fingerprints, historical similarity and falsification."""
from __future__ import annotations

import re
from collections import defaultdict

from app.models.analysis import Candidate, CauseCategory
from app.models.evidence import EvidenceBundle
from app.models.intelligence import (
    CausalRole, HistoricalMatch, HypothesisCheck, HypothesisTest,
    IncidentFingerprint, OperationalTopology, PropagationLink, TopologyEdge,
    TopologyNode,
)
from app.models.plan import InvestigationPlan
from app.models.signals import Signal, SignalType


def build_topology(plan: InvestigationPlan, evidence: EvidenceBundle,
                   signals: list[Signal]) -> OperationalTopology:
    """Use observed relationships only; a missing edge is not a negative finding."""
    nodes: dict[str, TopologyNode] = {}
    edges: dict[tuple[str, str, str], TopologyEdge] = {}

    def node(kind: str, name: str | None) -> str | None:
        if not name:
            return None
        key = f"{kind}:{name}"
        nodes[key] = TopologyNode(id=key, kind=kind, label=name)
        return key

    def edge(source: str | None, target: str | None, relation: str,
             evidence_id: str | None = None) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, relation)
        item = edges.setdefault(key, TopologyEdge(source=source, target=target,
                                                  relation=relation))
        if evidence_id and evidence_id not in item.evidence_ids:
            item.evidence_ids.append(evidence_id)

    for signal in signals:
        node("service", signal.service)
        pod_name = (f"{signal.namespace}/{signal.pod}" if signal.namespace and signal.pod
                    else signal.pod)
        pod = node("pod", pod_name)
        edge(pod, node("service", signal.service), "belongs_to", signal.id)
    for caller, callees in evidence.logs.dependency_edges.items():
        for callee in callees:
            edge(node("service", caller), node("service", callee), "calls")
    for event in evidence.events.events:
        pod_name = (f"{event.namespace}/{event.pod}" if event.namespace and event.pod
                    else event.pod)
        pod = node("pod", pod_name)
        edge(pod, node("service", event.service), "belongs_to", event.id)
        edge(pod, node("node", event.node), "runs_on", event.id)
    for series in evidence.metrics.series:
        namespace = series.labels.get("namespace")
        pod_name = (f"{namespace}/{series.pod}" if namespace and series.pod
                    else series.pod)
        pod = node("pod", pod_name)
        edge(pod, node("service", series.service), "belongs_to", series.id)
        edge(pod, node("node", series.labels.get("node")), "runs_on", series.id)

    by_service: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        if signal.service and signal.first_seen and not signal.pre_existing:
            by_service[signal.service].append(signal)
    links: list[PropagationLink] = []
    seen: set[tuple[str, str]] = set()
    for caller, callees in evidence.logs.dependency_edges.items():
        for callee in callees:
            for upstream in by_service.get(callee, []):
                for downstream in by_service.get(caller, []):
                    lag = (downstream.first_seen - upstream.first_seen).total_seconds()
                    key = (upstream.id, downstream.id)
                    if 0 <= lag <= 600 and key not in seen:
                        seen.add(key)
                        links.append(PropagationLink(
                            upstream_signal_id=upstream.id,
                            downstream_signal_id=downstream.id,
                            upstream_service=callee, downstream_service=caller,
                            lag_seconds=lag,
                        ))
    links.sort(key=lambda item: (item.lag_seconds, item.upstream_signal_id,
                                 item.downstream_signal_id))
    return OperationalTopology(system_id=plan.system_id, environment=plan.environment,
                               nodes=sorted(nodes.values(), key=lambda n: n.id),
                               edges=sorted(edges.values(), key=lambda e: (e.source, e.target,
                                                                           e.relation)),
                               propagation=links[:100])


def _template(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b", "<id>", value)
    value = re.sub(r"\b\d+\b", "<n>", value)
    return " ".join(value.split())[:160]


def build_fingerprint(plan: InvestigationPlan, evidence: EvidenceBundle,
                      signals: list[Signal], topology: OperationalTopology
                      ) -> IncidentFingerprint:
    ordered = sorted((s for s in signals if s.first_seen and not s.pre_existing),
                     key=lambda s: s.first_seen)
    services = {s.service for s in signals if s.service}
    services.update(p.service for p in evidence.logs.patterns if p.service)
    shapes: dict[str, str] = {}
    for series in evidence.metrics.series:
        ratio = series.ratio_to_baseline()
        if ratio is None:
            continue
        key = f"{series.service or 'system'}:{series.metric}"
        if ratio >= 2:
            shapes[key] = "elevated"
        elif ratio <= 0.5 and shapes.get(key) != "elevated":
            shapes[key] = "depressed"
        elif key not in shapes:
            shapes[key] = "stable"
    return IncidentFingerprint(
        system_id=plan.system_id, environment=plan.environment,
        affected_services=sorted(services),
        signal_types=sorted({s.type.value for s in signals}),
        sequence=[f"{s.service or 'system'}:{s.type.value}" for s in ordered[:30]],
        log_templates=sorted({_template(p.template) for p in evidence.logs.patterns
                              if p.level in ("ERROR", "FATAL", "CRITICAL", "WARN")})[:30],
        resource_state=shapes,
        dependency_edges=sorted(f"{e.source}>{e.target}" for e in topology.edges
                                if e.relation == "calls"),
    )


def fingerprint_from_stored(doc: dict) -> IncidentFingerprint | None:
    plan = doc.get("plan") or {}
    if not plan.get("system_id") or not plan.get("environment"):
        return None
    if doc.get("fingerprint"):
        try:
            return IncidentFingerprint.model_validate(doc["fingerprint"])
        except Exception:
            pass
    # Older investigations have signals and an evidence timeline but no stored
    # fingerprint. Reconstruct only what those documents actually preserved.
    signals = doc.get("signals") or []
    timeline = doc.get("evidence_timeline") or []
    ordered = sorted((s for s in signals if s.get("first_seen") and not s.get("pre_existing")),
                     key=lambda s: s["first_seen"])
    return IncidentFingerprint(
        system_id=plan["system_id"], environment=plan["environment"],
        affected_services=sorted({s["service"] for s in signals if s.get("service")}),
        signal_types=sorted({s["type"] for s in signals if s.get("type")}),
        sequence=[f"{s.get('service') or 'system'}:{s['type']}" for s in ordered[:30]],
        log_templates=sorted({_template(e.get("title", "")) for e in timeline
                              if e.get("kind") == "log" and e.get("title")})[:30],
    )


def _jaccard(left: list[str], right: list[str]) -> float | None:
    a, b = set(left), set(right)
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def _sequence_similarity(left: list[str], right: list[str]) -> float | None:
    if not left or not right:
        return None
    a, b = left[:25], right[:25]
    row = [0] * (len(b) + 1)
    for item in a:
        next_row = [0]
        for index, other in enumerate(b, 1):
            next_row.append(row[index - 1] + 1 if item == other
                            else max(row[index], next_row[-1]))
        row = next_row
    return row[-1] / max(len(a), len(b))


def similarity(current: IncidentFingerprint, past: IncidentFingerprint
               ) -> tuple[float, dict[str, float]]:
    """Only comparable observed features contribute; no historical RCA shortcut."""
    weights = {"signals": 0.35, "sequence": 0.20, "services": 0.15,
               "templates": 0.15, "resources": 0.10, "topology": 0.05}
    raw = {
        "signals": _jaccard(current.signal_types, past.signal_types),
        "sequence": _sequence_similarity(current.sequence, past.sequence),
        "services": _jaccard(current.affected_services, past.affected_services),
        "templates": _jaccard(current.log_templates, past.log_templates),
        "resources": _jaccard([f"{k}:{v}" for k, v in current.resource_state.items()],
                              [f"{k}:{v}" for k, v in past.resource_state.items()]),
        "topology": _jaccard(current.dependency_edges, past.dependency_edges),
    }
    factors = {key: round(value, 3) for key, value in raw.items() if value is not None}
    total_weight = sum(weights[key] for key in factors)
    return (round(sum(weights[key] * value for key, value in factors.items()) /
                  total_weight, 3) if total_weight else 0.0), factors


def rank_history(current: IncidentFingerprint, documents: list[dict], *,
                 limit: int = 3, minimum_score: float = 0.45) -> list[HistoricalMatch]:
    matches: list[HistoricalMatch] = []
    for doc in documents:
        plan = doc.get("plan") or {}
        # Defense in depth: never trust the search adapter alone to enforce scope.
        if (plan.get("system_id") != current.system_id or
                plan.get("environment") != current.environment):
            continue
        past = fingerprint_from_stored(doc)
        if (past is None or past.system_id != current.system_id or
                past.environment != current.environment or
                not past.signal_types or not current.signal_types):
            continue
        score, factors = similarity(current, past)
        if score < minimum_score or factors.get("signals", 0) == 0:
            continue
        answer = doc.get("answer") or {}
        analysis = doc.get("analysis") or {}
        matches.append(HistoricalMatch(
            investigation_id=doc.get("id", ""), score=score, factors=factors,
            headline=str(answer.get("headline") or analysis.get("cause_summary") or "")[:250],
            cause_category=str(analysis.get("category") or ""),
            root_cause_service=answer.get("root_cause_service"),
            created_at=str(doc.get("created_at") or ""),
        ))
    return sorted(matches, key=lambda match: (-match.score, match.investigation_id))[:limit]


_EXPECTED: dict[CauseCategory, tuple[tuple[SignalType, ...], ...]] = {
    CauseCategory.DEPENDENCY_FAILURE: ((SignalType.DEPENDENCY_UNAVAILABLE,),),
    CauseCategory.DEPENDENCY_DEGRADATION: ((SignalType.DEPENDENCY_DEGRADED,
                                            SignalType.LATENCY_DEGRADATION),),
    CauseCategory.RESOURCE_EXHAUSTION: ((SignalType.OOM_KILL, SignalType.MEMORY_PRESSURE),),
    CauseCategory.RESOURCE_SATURATION: ((SignalType.CPU_SATURATION,
                                          SignalType.CPU_THROTTLING),),
    CauseCategory.STARTUP_FAILURE: ((SignalType.CRASHLOOP, SignalType.IMAGE_PULL_FAILURE),),
    CauseCategory.READINESS_FAILURE: ((SignalType.READINESS_FAILURE,),),
    CauseCategory.SCHEDULING_FAILURE: ((SignalType.SCHEDULING_FAILURE,),),
    CauseCategory.CHANGE_INDUCED: ((SignalType.DEPLOYMENT_CHANGE,),),
    CauseCategory.LOAD_INCREASE: ((SignalType.TRAFFIC_SURGE,),),
    CauseCategory.APPLICATION_FAULT: ((SignalType.NEW_ERROR_PATTERN,
                                       SignalType.ERROR_RATE_SPIKE),),
}
_SYMPTOMS = {SignalType.ERROR_RATE_SPIKE, SignalType.HTTP_5XX_BURST,
             SignalType.LATENCY_DEGRADATION, SignalType.TRAFFIC_COLLAPSE}


def historical_candidates(candidates: list[Candidate], matches: list[HistoricalMatch],
                          signals: list[Signal]) -> list[Candidate]:
    """Add low-ranked past-RCA leads only when current telemetry supports them."""
    output = list(candidates)
    existing = {(candidate.category, candidate.service) for candidate in candidates}
    for match in matches:
        try:
            category = CauseCategory(match.cause_category)
        except ValueError:
            continue
        service = match.root_cause_service
        if not service or (category, service) in existing or category not in _EXPECTED:
            continue
        expected = {kind for group in _EXPECTED[category] for kind in group}
        support = [s.id for s in signals if s.service == service and s.type in expected
                   and not s.pre_existing]
        if not support:
            continue
        output.append(Candidate(
            id=f"cand:history:{len(output) + 1}", category=category,
            hypothesis=f"Check whether {category.value.replace('_', ' ')} on {service} recurred.",
            service=service, score=round(min(0.2, 0.1 + 0.1 * match.score), 3),
            supporting_signals=support[:5],
            rationale=(f"Prior investigation {match.investigation_id} looked similar, "
                       "and current signals match this failure type. The past RCA is "
                       "a lead, not evidence of the current cause."),
            origin="history",
        ))
        existing.add((category, service))
    return output


def evaluate_hypotheses(candidates: list[Candidate], signals: list[Signal],
                        topology: OperationalTopology) -> list[HypothesisTest]:
    by_id = {signal.id: signal for signal in signals}
    tests: list[HypothesisTest] = []
    for candidate in candidates:
        checks: list[HypothesisCheck] = []
        for group in _EXPECTED.get(candidate.category, ()):
            found = [s for s in signals if s.type in group and not s.pre_existing
                     and (candidate.service is None or s.service == candidate.service)]
            checks.append(HypothesisCheck(
                expectation="Expected current " + "/".join(t.value for t in group),
                outcome="observed" if found else "unknown",
                evidence_ids=[s.id for s in found[:5]],
            ))
        supported = [sid for sid in candidate.supporting_signals if sid in by_id]
        checks.append(HypothesisCheck(
            expectation="Rule support is present in this investigation",
            outcome="observed" if supported else "unknown", evidence_ids=supported[:8],
        ))
        contradicted = [sid for sid in candidate.contradicting_signals if sid in by_id]
        if contradicted:
            checks.append(HypothesisCheck(
                expectation="No measured signal contradicts this explanation",
                outcome="contradicted", evidence_ids=contradicted[:8],
            ))
        symptoms = [s for s in signals if s.type in _SYMPTOMS and s.first_seen
                    and not s.pre_existing and s.service != candidate.service]
        if candidate.onset and symptoms:
            first_symptom = min(symptoms, key=lambda s: s.first_seen)
            timely = candidate.onset <= first_symptom.first_seen
            checks.append(HypothesisCheck(
                expectation="Proposed cause starts before downstream symptoms",
                outcome="observed" if timely else "contradicted",
                evidence_ids=[first_symptom.id] + supported[:2],
            ))
        if candidate.service:
            propagated = [p for p in topology.propagation
                          if p.upstream_service == candidate.service]
            checks.append(HypothesisCheck(
                expectation="Observed dependency and onset order support propagation",
                outcome="observed" if propagated else "unknown",
                evidence_ids=list(dict.fromkeys(
                    sid for p in propagated[:5]
                    for sid in (p.upstream_signal_id, p.downstream_signal_id))),
            ))
        observations = sum(c.outcome == "observed" for c in checks)
        contradictions = sum(c.outcome == "contradicted" for c in checks)
        impossible_order = any(c.outcome == "contradicted" and
                               "before downstream symptoms" in c.expectation
                               for c in checks)
        verdict = ("reject" if impossible_order or
                   (contradictions and contradictions >= observations)
                   else "keep" if observations >= 2 and not contradictions
                   else "uncertain")
        tests.append(HypothesisTest(candidate_id=candidate.id, verdict=verdict,
                                    checks=checks))
    return tests


def classify_roles(signals: list[Signal], candidates: list[Candidate],
                   tests: list[HypothesisTest]) -> list[CausalRole]:
    kept = {test.candidate_id for test in tests if test.verdict == "keep"}
    root = next((c for c in candidates if c.id in kept and c.service), None)
    root_types = ({signal_type for group in _EXPECTED.get(root.category, ())
                   for signal_type in group} if root else set())
    roles: list[CausalRole] = []
    for signal in signals:
        if (root and signal.service == root.service and signal.id in root.supporting_signals
                and signal.type in root_types):
            role = "root_cause"
            basis = f"Supports retained candidate {root.id}; role remains a hypothesis."
        elif signal.type == SignalType.TRAFFIC_SURGE:
            role, basis = "contributing_factor", "Load may have amplified another failure."
        elif signal.type in (SignalType.HTTP_5XX_BURST, SignalType.ERROR_RATE_SPIKE,
                             SignalType.TRAFFIC_COLLAPSE):
            role, basis = "impact", "User-facing or application error measurement."
        elif signal.type in (SignalType.POD_RESTART, SignalType.READINESS_FAILURE):
            role, basis = "consequence", "Workload state; onset alone does not establish cause."
        else:
            role, basis = "symptom", "Measured condition; causal role remains unproven."
        roles.append(CausalRole(role=role, label=signal.description[:200],
                                service=signal.service, evidence_ids=[signal.id],
                                basis=basis))
    return roles
