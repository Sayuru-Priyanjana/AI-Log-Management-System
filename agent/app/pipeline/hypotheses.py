from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.config import settings
from app.models.analysis import Candidate, CauseCategory, InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan
from app.models.signals import Signal, SignalType

logger = logging.getLogger(__name__)

SYMPTOM_TYPES = (
    SignalType.ERROR_RATE_SPIKE,
    SignalType.HTTP_5XX_BURST,
    SignalType.NEW_ERROR_PATTERN,
    SignalType.LATENCY_DEGRADATION,
    SignalType.TRAFFIC_COLLAPSE,
)

INFRASTRUCTURE_TYPES = (
    SignalType.DEPENDENCY_UNAVAILABLE,
    SignalType.OOM_KILL,
    SignalType.CRASHLOOP,
    SignalType.CPU_THROTTLING,
    SignalType.MEMORY_PRESSURE,
    SignalType.SCHEDULING_FAILURE,
    SignalType.READINESS_FAILURE,
    SignalType.IMAGE_PULL_FAILURE,
)


def _of(signals: list[Signal], *types: SignalType) -> list[Signal]:
    """Signals of the given types that could plausibly belong to this incident.

    Pre-existing conditions are excluded: they were already running before the
    window opened, so they cannot be what started it. Leaving them in was worse
    than useless — their onset is clamped to the window start, which made them
    the earliest thing in the run and handed them the full causal-precedence
    bonus. A load generator's readiness blip from three hours earlier was scoring
    0.99 and outranking the actual outage.
    """
    wanted = set(types)
    return [s for s in signals if s.type in wanted and not s.pre_existing]


def _earliest(signals: list[Signal]) -> datetime | None:
    stamps = [s.first_seen for s in signals if s.first_seen]
    return min(stamps) if stamps else None


def _ids(signals: list[Signal]) -> list[str]:
    return [s.id for s in signals]


def _deepest(signals: list[Signal], evidence: EvidenceBundle) -> Signal | None:
    """The failing signal furthest down the observed call graph.

    Failures propagate upward: when a service breaks, everything that calls it
    reports errors too, so the whole chain lights up and the top of it is often
    the loudest. Picking the first or the largest signal therefore names a
    symptom. `depth_of` counts hops *beneath* a service, so the component
    nothing else sits below is the minimum — and that is the candidate root.
    """
    if not signals:
        return None
    return min(
        signals,
        key=lambda s: (evidence.logs.depth_of(s.service),
                       s.first_seen.timestamp() if s.first_seen else 0.0),
    )


class HypothesisEngine:
    """Turns signals into ranked candidate explanations using explicit rules.

    Root-cause reasoning happens here, in Python, where it is reproducible and
    inspectable. The model's later job is to pick from this list and explain the
    pick — which is why it cannot assert a cause that nothing supports.

    Ranking is causal, not just weighted: a candidate whose onset precedes the
    first symptom outranks one that follows it, because an explanation that
    starts after the thing it claims to explain is not an explanation.
    """

    def generate(self, plan: InvestigationPlan, windows: InvestigationWindows,
                 signals: list[Signal], evidence: EvidenceBundle) -> list[Candidate]:
        if not signals:
            return [Candidate(
                id="cand:1",
                category=CauseCategory.NO_INCIDENT,
                hypothesis="No incident detected in the investigated window.",
                score=0.6 if windows.baseline else 0.4,
                rationale=(
                    "No signal crossed its threshold: error rates, latency, restarts, "
                    "resource use and Kubernetes events all stayed within their baseline ranges."
                    + ("" if windows.baseline else
                       " Note that no baseline window was available, so this is a weaker statement "
                       "than it looks.")
                ),
            )]

        symptom_onset = _earliest(_of(signals, *SYMPTOM_TYPES))
        drafts: list[Candidate] = []

        for rule in (
            self._dependency_failure,
            self._dependency_degradation,
            self._memory_exhaustion,
            self._cpu_saturation,
            self._startup_failure,
            self._readiness_failure,
            self._scheduling_failure,
            self._change_induced,
            self._load_increase,
            self._application_fault,
        ):
            drafts.extend(rule(signals, evidence, symptom_onset))

        if not drafts:
            drafts.append(Candidate(
                id="pending",
                category=CauseCategory.UNKNOWN,
                hypothesis="Signals were detected but no rule explains them together.",
                score=0.25,
                supporting_signals=_ids(signals[:5]),
                rationale=("The detected signals do not match any known failure shape. "
                           "Treat the signal list itself as the finding."),
            ))

        drafts = _merge_duplicates(drafts)
        earliest_onset = min((c.onset for c in drafts if c.onset), default=None)
        for candidate in drafts:
            bonus = min(0.08 * len(candidate.supporting_signals), 0.24)
            penalty = 0.15 * len(candidate.contradicting_signals)

            # Causal precedence: does this start before the symptoms it claims
            # to explain?
            precedence = 0.0
            if candidate.onset and symptom_onset:
                if candidate.onset <= symptom_onset + timedelta(seconds=30):
                    precedence += 0.15
                else:
                    precedence -= 0.20
            if candidate.onset and earliest_onset and candidate.onset <= earliest_onset:
                precedence += 0.10

            candidate.score = round(max(0.0, min(1.0, candidate.score + bonus - penalty + precedence)), 3)

        ranked = sorted(drafts, key=lambda c: (-c.score, c.onset or windows.incident.end))
        for index, candidate in enumerate(ranked, start=1):
            candidate.id = f"cand:{index}"

        logger.info("Generated %d candidate(s); top: %s (%.2f)",
                    len(ranked), ranked[0].category.value, ranked[0].score)
        return ranked

    # ------------------------------------------------------------------ rules
    def _dependency_failure(self, signals, evidence, symptom_onset) -> list[Candidate]:
        outages = _of(signals, SignalType.DEPENDENCY_UNAVAILABLE)
        if not outages:
            return []
        candidates = []
        for outage in outages:
            downstream = [
                s for s in _of(signals, *SYMPTOM_TYPES)
                if s.service != outage.service
                and s.first_seen and outage.first_seen
                and s.first_seen >= outage.first_seen - timedelta(seconds=60)
            ]
            # "It became unavailable" is true but shallow when something else
            # already says *why* it went away. A crashloop, an OOM kill or a
            # scheduling failure on the same service is the more specific
            # account, and should not have to compete with a restatement of its
            # own consequence.
            explained_by = [
                s for s in _of(signals, SignalType.CRASHLOOP, SignalType.OOM_KILL,
                               SignalType.SCHEDULING_FAILURE, SignalType.IMAGE_PULL_FAILURE)
                if s.service == outage.service
            ]
            rationale = (
                f"{outage.description} "
                + (f"{len(downstream)} downstream symptom(s) in other services began at or "
                   f"after that point, which is the direction of causation you would expect."
                   if downstream else
                   "No downstream symptoms were observed, so the blast radius may be limited.")
            )
            if explained_by:
                kinds = ", ".join(sorted({s.type.value for s in explained_by}))
                rationale += (f" Note that {outage.service} is showing {kinds}, which explains "
                              f"why it went away — that is the underlying failure, and this "
                              f"describes its effect.")

            candidates.append(Candidate(
                id="pending",
                category=CauseCategory.DEPENDENCY_FAILURE,
                hypothesis=f"{outage.service} became unavailable and its callers failed as a result.",
                service=outage.service,
                onset=outage.first_seen,
                score=0.60 if not explained_by else 0.30,
                supporting_signals=_ids([outage] + downstream),
                contradicting_signals=_ids(explained_by),
                rationale=rationale,
            ))
        return candidates

    def _dependency_degradation(self, signals, evidence, symptom_onset) -> list[Candidate]:
        degraded = _of(signals, SignalType.DEPENDENCY_DEGRADED)
        latency = _of(signals, SignalType.LATENCY_DEGRADATION)
        if not degraded and not latency:
            return []

        ordered = sorted([s for s in latency if s.first_seen], key=lambda s: s.first_seen)
        if not degraded and len(ordered) < 2:
            return []

        # When latency degrades across a whole call chain, every service in it
        # looks slow — the caller is slow *because* its dependency is. So the
        # root is the deepest affected service in the observed call graph, not
        # whichever one happened to cross the threshold first. Onset order is
        # unreliable here: a caller can trip a p95 threshold before the
        # dependency that is actually causing the delay.
        candidates_for_root = degraded or latency
        root = _deepest(candidates_for_root, evidence)
        if root is None:
            return []

        supporting = list({s.id for s in degraded + ordered})
        chain_note = ""
        if len(ordered) > 1:
            by_depth = sorted(
                {s.service for s in ordered if s.service},
                key=lambda name: evidence.logs.depth_of(name),
            )
            chain_note = (f" Latency degraded across {len(ordered)} services "
                          f"({', '.join(by_depth)}); {root.service} sits deepest in the "
                          f"observed call graph, so the others are downstream of it.")

        # If the root service is itself crashlooping, being OOM-killed or unable
        # to schedule, that *explains* why calls to it are failing — "the
        # dependency is degraded" is then a restatement of the symptom, and a
        # weaker claim than the rule that names the actual failure. Without this
        # a crashloop investigation ranked "payment-api slowed down" above
        # "payment-api fails during startup and restarts in a loop".
        explained_by = [
            s for s in _of(signals, SignalType.CRASHLOOP, SignalType.OOM_KILL,
                           SignalType.SCHEDULING_FAILURE, SignalType.IMAGE_PULL_FAILURE)
            if s.service == root.service
        ]

        # DEPENDENCY_DEGRADED is a *failure ratio*, not a latency measurement.
        # Describing a service that is returning errors as having "slowed down"
        # is simply untrue, and it pushed the reader toward the wrong class of
        # fix. Say which of the two is actually happening.
        slow = bool(latency)
        if slow:
            hypothesis = f"{root.service} slowed down and the delay propagated to its callers."
        else:
            hypothesis = (f"{root.service} is returning errors to its callers, "
                          f"which are failing as a result.")

        base = 0.5 if slow else 0.35
        rationale = f"{root.description}{chain_note}"
        if explained_by:
            base = 0.15
            kinds = ", ".join(sorted({s.type.value for s in explained_by}))
            rationale += (f" But {root.service} is itself showing {kinds}, which already "
                          f"accounts for its callers failing — that is the more specific "
                          f"explanation, and this one only restates its effect.")

        return [Candidate(
            id="pending",
            category=CauseCategory.DEPENDENCY_DEGRADATION,
            hypothesis=hypothesis,
            service=root.service,
            onset=root.first_seen,
            # A failure-ratio-only degradation overlaps heavily with an outright
            # application fault in the dependency, and that rule describes it
            # better; leave the stronger claim to it rather than competing.
            score=base,
            supporting_signals=supporting,
            contradicting_signals=_ids(explained_by),
            rationale=rationale,
        )]

    def _memory_exhaustion(self, signals, evidence, symptom_onset) -> list[Candidate]:
        ooms = _of(signals, SignalType.OOM_KILL)
        pressure = _of(signals, SignalType.MEMORY_PRESSURE)
        if not ooms and not pressure:
            return []
        restarts = _of(signals, SignalType.POD_RESTART, SignalType.CRASHLOOP)
        anchor = (pressure or ooms)[0]
        supporting = _ids(pressure + ooms + restarts)

        score = 0.55 if ooms else 0.40
        chain = []
        if pressure:
            chain.append(f"memory climbed to {pressure[0].magnitude.describe()}"
                         if pressure[0].magnitude else "memory pressure was observed")
        if ooms:
            chain.append("the container was OOM-killed")
        if restarts:
            chain.append("the pod restarted")

        return [Candidate(
            id="pending",
            category=CauseCategory.RESOURCE_EXHAUSTION,
            hypothesis=f"{anchor.service or anchor.pod} exceeded its memory limit and was killed.",
            service=anchor.service,
            onset=_earliest(pressure) or _earliest(ooms),
            score=score,
            supporting_signals=supporting,
            rationale="In order: " + ", then ".join(chain) + ". "
                      "That sequence runs cause-to-effect, not the reverse.",
        )]

    def _cpu_saturation(self, signals, evidence, symptom_onset) -> list[Candidate]:
        throttling = _of(signals, SignalType.CPU_THROTTLING)
        saturation = _of(signals, SignalType.CPU_SATURATION)
        if not throttling and not saturation:
            return []
        latency = _of(signals, SignalType.LATENCY_DEGRADATION)
        anchor = (throttling or saturation)[0]

        # An OOM or a dependency outage explains slowness better than CPU does;
        # record them as arguing against rather than silently ignoring them.
        contradicting = _ids(_of(signals, SignalType.OOM_KILL, SignalType.DEPENDENCY_UNAVAILABLE))

        return [Candidate(
            id="pending",
            category=CauseCategory.RESOURCE_SATURATION,
            hypothesis=f"{anchor.service or anchor.pod} is CPU-bound against its limit.",
            service=anchor.service,
            onset=_earliest(throttling + saturation),
            score=0.50 if throttling else 0.35,
            supporting_signals=_ids(throttling + saturation + latency),
            contradicting_signals=contradicting,
            rationale=(
                f"{anchor.description} "
                + ("Latency degraded over the same period, which is the expected "
                   "consequence of throttling." if latency else
                   "No latency degradation accompanied it, so the impact may be limited.")
            ),
        )]

    def _startup_failure(self, signals, evidence, symptom_onset) -> list[Candidate]:
        crashloops = _of(signals, SignalType.CRASHLOOP)
        if not crashloops:
            return []
        # A crashloop with a memory explanation is not a startup failure.
        resource_signals = _of(signals, SignalType.OOM_KILL, SignalType.MEMORY_PRESSURE)

        fatal_patterns = [
            p for p in evidence.logs.patterns
            if p.level in ("FATAL", "CRITICAL")
            or "startup" in p.template.lower()
            or "failed to initialise" in p.template.lower()
            or "failed to initialize" in p.template.lower()
        ]
        anchor = crashloops[0]
        score = 0.55 if fatal_patterns else 0.35

        return [Candidate(
            id="pending",
            category=CauseCategory.STARTUP_FAILURE,
            hypothesis=f"{anchor.service or anchor.pod} fails during startup and restarts in a loop.",
            service=anchor.service,
            onset=_earliest(crashloops),
            score=score,
            supporting_signals=_ids(crashloops) + [p.id for p in fatal_patterns[:3]],
            contradicting_signals=_ids(resource_signals),
            rationale=(
                f"{anchor.description} "
                + (f"A fatal startup log accompanies each attempt: "
                   f"\"{fatal_patterns[0].example[:160]}\"" if fatal_patterns else
                   "No startup-time fatal log was captured, so the exit reason is not yet established.")
                + (" Memory signals are also present, which would point at resource exhaustion instead."
                   if resource_signals else "")
            ),
        )]

    def _readiness_failure(self, signals, evidence, symptom_onset) -> list[Candidate]:
        readiness = _of(signals, SignalType.READINESS_FAILURE)
        if not readiness:
            return []
        restarts = _of(signals, SignalType.POD_RESTART, SignalType.CRASHLOOP)
        anchor = readiness[0]
        return [Candidate(
            id="pending",
            category=CauseCategory.READINESS_FAILURE,
            hypothesis=f"{anchor.service or anchor.pod} is running but failing its readiness probe.",
            service=anchor.service,
            onset=_earliest(readiness),
            score=0.50 if not restarts else 0.30,
            supporting_signals=_ids(readiness),
            contradicting_signals=_ids(restarts),
            rationale=(
                f"{anchor.description} "
                + ("The pod is not restarting, which distinguishes this from a crash loop: "
                   "the process is alive but not serving." if not restarts else
                   "The pod is also restarting, so the probe failure may be a symptom of the "
                   "restarts rather than the cause.")
            ),
        )]

    def _scheduling_failure(self, signals, evidence, symptom_onset) -> list[Candidate]:
        scheduling = _of(signals, SignalType.SCHEDULING_FAILURE)
        if not scheduling:
            return []
        anchor = scheduling[0]
        changes = _of(signals, SignalType.DEPLOYMENT_CHANGE)
        return [Candidate(
            id="pending",
            category=CauseCategory.SCHEDULING_FAILURE,
            hypothesis=f"{anchor.service or anchor.pod} cannot be scheduled onto any node.",
            service=anchor.service,
            onset=_earliest(scheduling),
            score=0.65,
            supporting_signals=_ids(scheduling + changes),
            rationale=(
                f"{anchor.description} "
                + ("A deployment change immediately precedes it, so the new pod spec is the "
                   "likely reason it no longer fits." if changes else
                   "No deployment change was observed, so cluster capacity is the likelier constraint.")
            ),
        )]

    def _change_induced(self, signals, evidence, symptom_onset) -> list[Candidate]:
        changes = [s for s in _of(signals, SignalType.DEPLOYMENT_CHANGE) if s.first_seen]
        if not changes or symptom_onset is None:
            return []
        # Only a change that lands shortly *before* the symptoms is a suspect.
        suspects = [
            change for change in changes
            if timedelta(0) <= (symptom_onset - change.first_seen) <= timedelta(minutes=10)
        ]
        if not suspects:
            return []
        change = suspects[0]
        symptoms = _of(signals, *SYMPTOM_TYPES)
        gap = (symptom_onset - change.first_seen).total_seconds()
        return [Candidate(
            id="pending",
            category=CauseCategory.CHANGE_INDUCED,
            hypothesis=f"A change to {change.service} introduced the failure.",
            service=change.service,
            onset=change.first_seen,
            score=0.55,
            supporting_signals=_ids([change] + symptoms),
            rationale=(f"{change.description} Symptoms began {gap:.0f}s later. "
                       f"Proximity is not proof, but a change immediately followed by new "
                       f"failures is the first thing to rule out."),
        )]

    def _load_increase(self, signals, evidence, symptom_onset) -> list[Candidate]:
        surges = _of(signals, SignalType.TRAFFIC_SURGE)
        if not surges:
            return []
        latency = _of(signals, SignalType.LATENCY_DEGRADATION)
        saturation = _of(signals, SignalType.CPU_SATURATION, SignalType.CPU_THROTTLING)
        # Load explains slowness. It does not explain a dependency being gone.
        contradicting = _ids(_of(signals, SignalType.DEPENDENCY_UNAVAILABLE,
                                 SignalType.OOM_KILL, SignalType.CRASHLOOP))
        surge = surges[0]
        return [Candidate(
            id="pending",
            category=CauseCategory.LOAD_INCREASE,
            hypothesis="Increased traffic, rather than a fault, is driving the change in behaviour.",
            service=surge.service,
            onset=_earliest(surges),
            score=0.50 if (latency or saturation) else 0.30,
            supporting_signals=_ids(surges + latency + saturation),
            contradicting_signals=contradicting,
            rationale=(
                f"{surge.description} "
                + ("Latency and resource use rose with it, which is what load looks like — "
                   "no component is necessarily broken."
                   if (latency or saturation) else
                   "Nothing else changed alongside it, so the extra load appears to be absorbed.")
            ),
        )]

    def _application_fault(self, signals, evidence, symptom_onset) -> list[Candidate]:
        errors = _of(signals, SignalType.HTTP_5XX_BURST, SignalType.ERROR_RATE_SPIKE,
                     SignalType.NEW_ERROR_PATTERN)
        if not errors:
            return []
        infrastructure = _of(signals, *INFRASTRUCTURE_TYPES)

        # Errors with an infrastructure explanation are not an application fault.
        # This candidate exists so that "the code is failing" stays on the table
        # when nothing else explains it — and drops away when something does.
        #
        # The anchor is the deepest failing service, not the first signal in the
        # list. When one service starts returning 500s, every service above it
        # returns 500s too, and the entry point is usually the loudest — so
        # taking the first error signal named the caller and blamed the wrong
        # component while looking entirely plausible.
        anchor = _deepest(errors, evidence) or errors[0]
        upstream = [s for s in errors if s.service != anchor.service]
        new_patterns = [p for p in evidence.logs.patterns
                        if p.is_new and p.level == "ERROR"
                        and (p.service == anchor.service or not p.service)]
        detail = (f" The dominant new error there is: \"{new_patterns[0].example[:160]}\""
                  if new_patterns else "")
        chain_note = ""
        if upstream:
            chain_note = (f" {len(upstream)} service(s) above it in the call graph report errors "
                          f"too ({', '.join(sorted({s.service for s in upstream if s.service}))}), "
                          f"which is what happens downstream of a failing dependency.")

        return [Candidate(
            id="pending",
            category=CauseCategory.APPLICATION_FAULT,
            hypothesis=f"{anchor.service} is failing internally with no infrastructure cause.",
            service=anchor.service,
            onset=_earliest(errors),
            score=0.45 if not infrastructure else 0.20,
            supporting_signals=_ids(errors),
            contradicting_signals=_ids(infrastructure),
            rationale=(
                f"{anchor.description}{detail}{chain_note} "
                + ("No restart, resource or dependency signal accompanies it, so the failure "
                   "appears to originate in the service itself."
                   if not infrastructure else
                   f"However {len(infrastructure)} infrastructure signal(s) are present and would "
                   f"explain these errors as a symptom rather than the cause.")
            ),
        )]


def _merge_duplicates(drafts: list[Candidate]) -> list[Candidate]:
    """Collapses candidates that say the same thing about the same component.

    Several signals can point at one failure — a dependency going down shows up
    both as its scrape target dropping and as its callers' failure rate — and
    each produced its own candidate. Two entries reading "payment-db became
    unavailable" are not competing explanations; leaving them separate padded
    the list and made every run look ambiguous.
    """
    merged: dict[tuple, Candidate] = {}
    for draft in drafts:
        key = (draft.category, draft.service)
        existing = merged.get(key)
        if existing is None:
            merged[key] = draft
            continue
        # Keep the stronger claim, but pool the evidence and the earliest onset.
        winner, loser = ((existing, draft) if existing.score >= draft.score
                         else (draft, existing))
        winner.supporting_signals = list(
            dict.fromkeys(winner.supporting_signals + loser.supporting_signals))
        winner.contradicting_signals = list(
            dict.fromkeys(winner.contradicting_signals + loser.contradicting_signals))
        if loser.onset and (winner.onset is None or loser.onset < winner.onset):
            winner.onset = loser.onset
        merged[key] = winner
    return list(merged.values())


def is_ambiguous(candidates: list[Candidate]) -> bool:
    """True when the top two are genuinely competing explanations.

    Only counts when they point at different causes: two close scores that both
    say "payment-db failed" do not leave a reader with a decision to make, and
    flagging those was turning a real warning into background noise.
    """
    if len(candidates) < 2:
        return False
    top, second = candidates[0], candidates[1]
    if (top.category, top.service) == (second.category, second.service):
        return False
    return (top.score - second.score) < settings.candidate_ambiguity_margin
