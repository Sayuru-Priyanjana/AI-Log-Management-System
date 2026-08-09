from __future__ import annotations

import logging
from datetime import timedelta

from app.config import settings
from app.models.analysis import (
    Analysis,
    AnalystChoice,
    Candidate,
    CauseCategory,
    InvestigationWindows,
    VerificationIssue,
)
from app.models.evidence import EvidenceBundle
from app.models.signals import Severity, Signal, SignalType
from app.pipeline.hypotheses import SYMPTOM_TYPES, is_ambiguous

logger = logging.getLogger(__name__)

_CAUSAL_GRACE = timedelta(seconds=60)


def _severity_from_signals(signals: list[Signal]) -> str:
    if not signals:
        return "none"
    return max(signals, key=lambda s: s.severity.rank).severity.value


def verify(
    *,
    choice: AnalystChoice | None,
    candidates: list[Candidate],
    signals: list[Signal],
    evidence: EvidenceBundle,
    windows: InvestigationWindows,
    evidence_index: dict[str, str],
    timeline: list[str],
    warnings: list[str],
) -> Analysis:
    """Checks the model's answer against the evidence before anyone sees it.

    Nothing here trusts the model. Citations must resolve, the chosen cause must
    be supported by signals that actually fired, and it must not start after the
    symptoms it claims to explain. Where the answer fails a check it is corrected
    or downgraded, and the correction is recorded rather than hidden.
    """
    # Warnings may carry an explicit code as "code|detail"; a truncated prompt
    # and an unreachable model both cost the same answer but need different fixes.
    issues: list[VerificationIssue] = []
    for warning in warnings:
        code, separator, detail = warning.partition("|")
        issues.append(VerificationIssue(
            code=code if separator else "llm_warning",
            detail=detail if separator else warning,
            severity="error" if code == "prompt_truncated" else "warning",
        ))

    engine_top = candidates[0] if candidates else None
    analyst = "llm"

    # -- 1. resolve the chosen candidate ----------------------------------
    if choice is None:
        chosen = engine_top
        confidence = engine_top.score if engine_top else 0.0
        reasoning = ""
        analyst = "deterministic"
        issues.append(VerificationIssue(
            code="llm_unavailable",
            detail="The model did not produce a usable answer; the deterministic ranking was "
                   "used instead. Confidence is the engine's rule score, not a model judgement.",
            severity="warning",
        ))
        cited: list[str] = []
        next_steps: list[str] = []
    else:
        chosen = next((c for c in candidates if c.id == choice.candidate_id), engine_top)
        confidence = choice.confidence
        reasoning = choice.reasoning.strip()
        cited = list(choice.evidence_ids or [])
        next_steps = list(choice.next_steps or [])

    if chosen is None:
        return Analysis(
            incident_detected=False, severity="unknown", category=CauseCategory.UNKNOWN,
            cause_summary="No candidate explanation could be produced.",
            confidence=0.0, analyst=analyst, verification=issues,
            evidence_gaps=evidence.gaps(), timeline=timeline,
        )

    # -- 2. citations must resolve ----------------------------------------
    verified_ids, rejected = [], []
    for evidence_id in cited:
        (verified_ids if evidence_id in evidence_index else rejected).append(evidence_id)
    if rejected:
        issues.append(VerificationIssue(
            code="unresolvable_citations",
            detail=f"Dropped {len(rejected)} citation(s) that refer to nothing in the evidence: "
                   f"{', '.join(rejected[:5])}",
            severity="warning",
        ))
        confidence = min(confidence, 0.7)

    # Always include the candidate's own support, so the output cites something
    # real even when the model cited nothing.
    for signal_id in chosen.supporting_signals:
        if signal_id not in verified_ids and signal_id in evidence_index:
            verified_ids.append(signal_id)

    # -- 3. the cause must be supported -----------------------------------
    live_signal_ids = {signal.id for signal in signals}
    supporting_present = [s for s in chosen.supporting_signals if s in live_signal_ids]
    if chosen.category not in (CauseCategory.NO_INCIDENT, CauseCategory.UNKNOWN) and not supporting_present:
        issues.append(VerificationIssue(
            code="unsupported_cause",
            detail=f"'{chosen.hypothesis}' is not backed by any signal that fired. "
                   f"Falling back to the engine's top-ranked candidate.",
            severity="error",
        ))
        chosen = engine_top or chosen
        confidence = min(confidence, 0.4)

    # -- 4. causal ordering -------------------------------------------------
    symptom_onsets = [
        s.first_seen for s in signals if s.type in SYMPTOM_TYPES and s.first_seen
    ]
    if chosen.onset and symptom_onsets:
        earliest_symptom = min(symptom_onsets)
        if chosen.onset > earliest_symptom + _CAUSAL_GRACE:
            gap = (chosen.onset - earliest_symptom).total_seconds()
            issues.append(VerificationIssue(
                code="effect_precedes_cause",
                detail=f"The chosen cause starts {gap:.0f}s AFTER the first symptom "
                       f"({earliest_symptom:%H:%M:%S}Z). It is more likely a consequence "
                       f"than the origin.",
                severity="error",
            ))
            confidence = min(confidence, 0.35)

    # -- 5. disagreement with the engine ------------------------------------
    agrees = engine_top is None or chosen.id == engine_top.id
    if not agrees:
        issues.append(VerificationIssue(
            code="engine_disagreement",
            detail=f"The model chose '{chosen.hypothesis}' while the rules ranked "
                   f"'{engine_top.hypothesis}' highest "
                   f"({engine_top.score:.2f} vs {chosen.score:.2f}). Both are reported.",
            severity="warning",
        ))

    # -- 6. ambiguity and gaps cap confidence -------------------------------
    if is_ambiguous(candidates):
        issues.append(VerificationIssue(
            code="ambiguous_candidates",
            detail=f"The top two explanations score within "
                   f"{settings.candidate_ambiguity_margin:.2f} of each other; the evidence does "
                   f"not clearly separate them.",
            severity="warning",
        ))
        confidence = min(confidence, 0.6)

    gaps = evidence.gaps()
    if gaps:
        issues.append(VerificationIssue(
            code="incomplete_evidence",
            detail=f"{len(gaps)} evidence source(s) were incomplete; an absent signal here "
                   f"does not mean the condition was absent.",
            severity="warning",
        ))
        confidence = min(confidence, 0.65)

    if windows.baseline is None:
        issues.append(VerificationIssue(
            code="no_baseline",
            detail="No quiet baseline window was available, so 'elevated' could not be "
                   "established by comparison.",
            severity="warning",
        ))
        confidence = min(confidence, 0.55)

    if windows.onset_before_window:
        issues.append(VerificationIssue(
            code="onset_outside_range",
            detail="Errors were already elevated at the earliest point examined; the incident "
                   "began before this window and its true start was not observed.",
            severity="warning",
        ))

    # -- assemble ------------------------------------------------------------
    incident_detected = chosen.category is not CauseCategory.NO_INCIDENT and bool(signals)
    summary = chosen.hypothesis if incident_detected else "No incident detected."
    if reasoning:
        # Small models often restate the hypothesis verbatim at the start of
        # their reasoning before elaborating. Prepending it again in that case
        # produces a visibly duplicated sentence, so a fuzzy prefix match skips
        # the second copy instead.
        prefix = chosen.hypothesis.strip().lower()[:40]
        if prefix and reasoning.strip().lower().startswith(prefix):
            summary = reasoning.strip()
        else:
            summary = f"{chosen.hypothesis} {reasoning}".strip()

    if not next_steps:
        next_steps = _default_next_steps(chosen, signals)

    analysis = Analysis(
        incident_detected=incident_detected,
        severity=_severity_from_signals(signals),
        category=chosen.category,
        chosen_candidate_id=chosen.id,
        cause_summary=summary,
        timeline=timeline,
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        evidence_ids=verified_ids[:20],
        next_steps=next_steps[:6],
        evidence_gaps=gaps,
        analyst=analyst,
        engine_top_candidate_id=engine_top.id if engine_top else None,
        agrees_with_engine=agrees,
        verification=issues,
    )
    logger.info("Verified: %s (confidence %.2f, %d issue(s))",
                analysis.category.value, analysis.confidence, len(issues))
    return analysis


def _default_next_steps(chosen: Candidate, signals: list[Signal]) -> list[str]:
    """Concrete follow-ups derived from the cause, so the output is actionable
    even when the model returned none."""
    by_category = {
        CauseCategory.DEPENDENCY_FAILURE: [
            f"Check whether {chosen.service} has running, ready pods.",
            f"Confirm the Service and endpoints for {chosen.service} still resolve.",
        ],
        CauseCategory.DEPENDENCY_DEGRADATION: [
            f"Profile {chosen.service} to find where the added latency is spent.",
            "Review client-side timeouts and retry budgets against the new latency.",
        ],
        CauseCategory.RESOURCE_EXHAUSTION: [
            f"Inspect the memory profile of {chosen.service} for a leak.",
            f"Review the memory limit on {chosen.service} against its working set.",
        ],
        CauseCategory.RESOURCE_SATURATION: [
            f"Raise the CPU limit for {chosen.service} or scale it horizontally.",
            "Identify what changed in per-request CPU cost.",
        ],
        CauseCategory.STARTUP_FAILURE: [
            f"Read the full startup logs of the failing {chosen.service} container.",
            "Check recent config, secret and environment changes for that workload.",
        ],
        CauseCategory.READINESS_FAILURE: [
            f"Call the readiness endpoint of {chosen.service} directly from inside the cluster.",
            "Check whether the probe timeout is now shorter than the real response time.",
        ],
        CauseCategory.SCHEDULING_FAILURE: [
            "Compare the pod's resource requests against allocatable node capacity.",
            "Check node taints, affinity rules and pressure conditions.",
        ],
        CauseCategory.CHANGE_INDUCED: [
            f"Diff the last two revisions of the {chosen.service} deployment.",
            f"Roll back {chosen.service} and confirm whether the symptoms clear.",
        ],
        CauseCategory.APPLICATION_FAULT: [
            f"Trace a failing request through {chosen.service} end to end.",
            "Check whether the error correlates with a specific route or input.",
        ],
        CauseCategory.LOAD_INCREASE: [
            "Confirm whether the extra traffic is legitimate.",
            "Check headroom and autoscaling settings against the new rate.",
        ],
        CauseCategory.NO_INCIDENT: [
            "Widen the time range if the reported problem is believed to be real.",
            "Confirm the correct system and environment were selected.",
        ],
    }
    steps = by_category.get(chosen.category, ["Review the signal list and the timeline above."])
    if any(s.type is SignalType.OOM_KILL for s in signals):
        steps.append("Capture a heap profile before the next OOM kill.")
    return steps
