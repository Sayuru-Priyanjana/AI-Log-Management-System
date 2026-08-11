"""
Turns the model's reply into a verified answer.

Nothing the loop says is taken on trust. Citations are matched against the IDs
the tools actually exposed, the confidence is rebuilt from what the run can
support rather than accepted as self-reported, and every adjustment is recorded
as a factor the reader can see and argue with.

The guiding rule: a wrong answer that admits its weaknesses is recoverable; a
wrong answer wearing 95% confidence is not.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.models.analysis import Candidate, InvestigationWindows
from app.models.answer import (
    Assumption,
    AnswerMode,
    Citation,
    CitationStatus,
    ConfidenceFactor,
    DataTable,
    ReasoningStep,
    StructuredAnswer,
)
from app.models.evidence import EvidenceBundle
from app.models.signals import Signal

logger = logging.getLogger(__name__)


def build_evidence_index(signals: list[Signal], candidates: list[Candidate],
                         evidence: EvidenceBundle) -> dict[str, str]:
    """Every ID that refers to something real in this run."""
    index: dict[str, str] = {}
    for signal in signals:
        index[signal.id] = (f"{signal.type.value} on "
                            f"{signal.service or signal.pod or 'system'}")
    for candidate in candidates:
        index[candidate.id] = candidate.hypothesis
    for pattern in evidence.logs.patterns:
        index[pattern.id] = f"log pattern in {pattern.service}: {pattern.template[:70]}"
    for sample in evidence.logs.samples:
        index[sample.id] = f"log line from {sample.service}"
    for event in evidence.events.events:
        index[event.id] = (f"Kubernetes {event.reason} on "
                           f"{event.pod or event.involved_name}")
    for series in evidence.metrics.series:
        index[series.id] = f"metric {series.metric} for {series.pod or series.service}"
    return index


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


# Schema placeholders the model sometimes copies verbatim instead of substituting
# a real ID: "sig:...", "pat:<id>", "evt:xxx".
_PLACEHOLDER_TAILS = ("...", "…", "<id>", "xxx", "id", "example", "your_id")


def _is_placeholder(evidence_id: str) -> bool:
    _, _, tail = evidence_id.partition(":")
    tail = tail.strip().lower()
    return not tail or tail in _PLACEHOLDER_TAILS or set(tail) <= {".", "…"}


def verify_answer(
    *,
    raw: dict,
    mode: AnswerMode,
    signals: list[Signal],
    candidates: list[Candidate],
    evidence: EvidenceBundle,
    windows: InvestigationWindows,
    exposed_ids: set[str],
    table: DataTable | None = None,
    steps_used: int = 0,
    degraded: str | None = None,
) -> StructuredAnswer:
    index = build_evidence_index(signals, candidates, evidence)
    factors: list[ConfidenceFactor] = []

    answer = StructuredAnswer(
        mode=mode,
        headline=(raw.get("headline") or "").strip(),
        detail=(raw.get("detail") or "").strip(),
        root_cause_service=raw.get("root_cause_service") or None,
        table=table,
    )

    # -- reasoning ---------------------------------------------------------
    cited: list[str] = []
    for item in raw.get("reasoning") or []:
        if not isinstance(item, dict) or not item.get("claim"):
            continue
        ids = [str(i) for i in (item.get("evidence_ids") or []) if i]
        cited.extend(ids)
        answer.reasoning.append(ReasoningStep(
            claim=str(item["claim"]).strip(),
            because=str(item.get("because") or "").strip(),
            evidence_ids=ids,
            kind=str(item.get("kind") or "inference"),
        ))

    for item in raw.get("assumptions") or []:
        if isinstance(item, dict) and item.get("statement"):
            answer.assumptions.append(Assumption(
                statement=str(item["statement"]).strip(),
                basis=str(item.get("basis") or "").strip(),
                impact_if_wrong=str(item.get("impact_if_wrong") or "").strip(),
            ))
        elif isinstance(item, str) and item.strip():
            answer.assumptions.append(Assumption(statement=item.strip()))

    answer.limitations = [str(x).strip() for x in (raw.get("limitations") or []) if x]
    answer.next_steps = [str(x).strip() for x in (raw.get("next_steps") or []) if x]

    # -- citations ---------------------------------------------------------
    # Three outcomes, kept distinct: it resolves, it was never shown to the
    # model, or the pipeline added it. Collapsing them would hide the difference
    # between a model that cited carefully and one that guessed.
    seen: set[str] = set()
    placeholders = 0
    for evidence_id in cited:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        if evidence_id in index:
            answer.citations.append(Citation(
                id=evidence_id, label=index[evidence_id],
                status=CitationStatus.RESOLVED,
            ))
            continue

        # A copied schema placeholder is a formatting slip, not a fabricated
        # claim. Both are rejected, but they say different things about the run
        # and lumping them together makes the diagnosis harder.
        if _is_placeholder(evidence_id):
            placeholders += 1
            answer.citations.append(Citation(
                id=evidence_id, label="",
                status=CitationStatus.UNRESOLVED,
                detail="A placeholder copied from the schema, not a real reference.",
            ))
            continue

        answer.citations.append(Citation(
            id=evidence_id, label="",
            status=CitationStatus.UNRESOLVED,
            detail=("This ID does not match any evidence collected in this run."
                    + ("" if evidence_id in exposed_ids else
                       " It was never shown to the model.")),
        ))

    # The strongest evidence in the run, added so an answer that cited nothing
    # still points somewhere real — marked as inferred so it is not mistaken for
    # the model's own reasoning. Only meaningful when there was evidence to cite:
    # on a healthy system the honest answer cites nothing.
    if signals and not any(c.status is CitationStatus.RESOLVED for c in answer.citations):
        for signal in sorted(signals, key=lambda s: -s.severity.rank)[:3]:
            answer.citations.append(Citation(
                id=signal.id, label=index.get(signal.id, ""),
                status=CitationStatus.INFERRED,
                detail="Added by the pipeline: the model cited no usable evidence.",
            ))

    # -- confidence, rebuilt from scratch ----------------------------------
    stated = raw.get("confidence")
    try:
        confidence = _clamp(float(stated))
    except (TypeError, ValueError):
        confidence = 0.5
    if stated is None:
        factors.append(ConfidenceFactor(
            factor="the model gave no confidence; started from 0.5",
            direction="lowers", weight=0.0))

    # Caps and bonuses are accumulated separately and combined at the end. A cap
    # is a ceiling on what this run can support, so a bonus must not be able to
    # lift the answer back above one: applying them in call order let a truncated
    # prompt score 0.5 because agreeing with the rule engine undid its own cap.
    ceiling = 1.0
    bonus = 0.0

    def adjust(cap: float | None, delta: float, factor: str, direction: str) -> None:
        nonlocal ceiling, bonus
        if cap is not None:
            ceiling = min(ceiling, cap)
        bonus += delta
        factors.append(ConfidenceFactor(
            factor=factor, direction=direction,
            weight=round(delta if delta else -(1.0 - (cap or 1.0)), 3)))

    unresolved = answer.unresolved_citations
    fabricated = [c for c in unresolved if not _is_placeholder(c.id)]
    if fabricated:
        adjust(0.6, 0.0,
               f"{len(fabricated)} citation(s) refer to evidence that does not exist "
               f"({', '.join(c.id for c in fabricated[:3])})", "lowers")
    elif placeholders:
        # Sloppy rather than dishonest, so a lighter cap.
        adjust(0.75, 0.0,
               f"{placeholders} citation(s) were schema placeholders rather than real "
               f"references", "lowers")

    # Citing nothing is only a weakness when there was something to cite. On a
    # healthy system "no signal fired" is the whole answer, and demanding a
    # reference for it would punish the correct response.
    unsupported = answer.unsupported_claims
    if unsupported and signals:
        adjust(0.65, 0.0,
               f"{len(unsupported)} reasoning step(s) cite no evidence at all",
               "lowers")

    if not signals and mode is AnswerMode.ROOT_CAUSE:
        adjust(0.4, 0.0,
               "no measurement crossed its threshold, so there is nothing to "
               "attribute a cause to", "lowers")

    if windows.baseline is None:
        adjust(settings.no_baseline_confidence_cap, 0.0,
               "no quiet baseline window was available, so 'elevated' could not be "
               "established by comparison", "lowers")

    gaps = evidence.gaps()
    if gaps:
        adjust(0.7, 0.0,
               f"{len(gaps)} evidence source(s) were incomplete; an absent signal "
               f"does not prove the condition was absent", "lowers")

    if windows.onset_before_window:
        adjust(0.75, 0.0,
               "the incident began before the window examined, so its true start "
               "was not observed", "lowers")

    if degraded:
        adjust(0.35, 0.0, degraded, "lowers")

    # Agreement with the rule engine is the one thing that can raise confidence:
    # it is the only independent check available on the model's choice.
    top = candidates[0] if candidates else None
    if top and answer.root_cause_service and mode is AnswerMode.ROOT_CAUSE:
        if top.service == answer.root_cause_service:
            adjust(None, 0.1,
                   f"the rule engine independently ranked {top.service} highest, "
                   f"which agrees with this answer", "raises")
        else:
            adjust(0.6, 0.0,
                   f"the rule engine ranked '{top.service}' highest, not "
                   f"'{answer.root_cause_service}' — the two disagree", "lowers")

    # Classify by the candidate naming the same component, whether or not it was
    # ranked first. Taking the category only from the top candidate meant that
    # every time the model disagreed the answer was filed as "unknown" — losing
    # the classification precisely when the disagreement made it interesting.
    if answer.root_cause_service and not answer.cause_category:
        match = next((c for c in candidates
                      if c.service == answer.root_cause_service), None)
        if match:
            answer.cause_category = match.category.value

    resolved = [c for c in answer.citations if c.status is CitationStatus.RESOLVED]
    if len(resolved) >= 3:
        adjust(None, 0.05,
               f"{len(resolved)} citations resolve to real evidence", "raises")

    # Nothing this pipeline produces is certain. The model routinely reports 1.0 —
    # one live run returned "no error logs found" at full confidence after four
    # malformed searches — and a number that never expresses doubt carries no
    # information. The ceiling is what stops a self-report from becoming a fact.
    adjust(settings.max_reportable_confidence, 0.0,
           f"capped at {settings.max_reportable_confidence:.0%}: an automated "
           f"conclusion drawn from a bounded window is never certain", "lowers")

    # A negative finding is only as good as the search behind it, and the loop
    # cannot see whether it searched well. Absence needs corroboration.
    if mode in (AnswerMode.DATA_EXTRACTION, AnswerMode.AGGREGATION):
        if table is None or not table.rows:
            adjust(0.6, 0.0,
                   "nothing was returned, and an empty result can mean the filter was "
                   "wrong as easily as it can mean the data is absent", "lowers")

    answer.confidence = _clamp(min(confidence + bonus, ceiling))
    answer.confidence_factors = factors

    # -- honesty backstops -------------------------------------------------
    if not answer.headline:
        answer.headline = "The agent did not produce a usable answer."
        answer.confidence = 0.0

    if mode is AnswerMode.ROOT_CAUSE and not signals:
        answer.limitations.insert(
            0, "No signal crossed its threshold in this window, so no cause could be "
               "established from measurement alone.")

    for gap in gaps:
        if gap not in answer.limitations:
            answer.limitations.append(gap)

    if steps_used:
        logger.info("Answer verified after %d step(s): mode=%s confidence=%.2f "
                    "(%d resolved, %d unresolved citations)",
                    steps_used, mode.value, answer.confidence,
                    len(resolved), len(unresolved))
    return answer
