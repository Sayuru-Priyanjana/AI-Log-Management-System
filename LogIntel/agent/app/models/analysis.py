from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from .answer import StructuredAnswer, TimelineEntry
from .domain import TimeWindow, utcnow
from .plan import InvestigationPlan
from .signals import Signal


class CauseCategory(str, Enum):
    DEPENDENCY_FAILURE = "dependency_failure"
    DEPENDENCY_DEGRADATION = "dependency_degradation"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    RESOURCE_SATURATION = "resource_saturation"
    STARTUP_FAILURE = "startup_failure"
    READINESS_FAILURE = "readiness_failure"
    SCHEDULING_FAILURE = "scheduling_failure"
    CHANGE_INDUCED = "change_induced"
    APPLICATION_FAULT = "application_fault"
    LOAD_INCREASE = "load_increase"
    NO_INCIDENT = "no_incident"
    UNKNOWN = "unknown"


class Candidate(BaseModel):
    """A pre-computed explanation, produced by deterministic rules.

    The model chooses among these; it does not author them. That is what makes
    the conclusion reproducible and stops a plausible-sounding cause appearing
    with nothing behind it.
    """

    id: str
    category: CauseCategory
    hypothesis: str
    service: str | None = None
    onset: datetime | None = None
    score: float = 0.0
    supporting_signals: list[str] = Field(default_factory=list)
    contradicting_signals: list[str] = Field(default_factory=list)
    rationale: str = ""

    def summary_line(self) -> str:
        head = f"[{self.id}] {self.hypothesis} (score {self.score:.2f})"
        if self.service:
            head += f" | service={self.service}"
        if self.onset:
            head += f" | onset={self.onset:%H:%M:%S}Z"
        lines = [head, f"    why: {self.rationale}"]
        if self.supporting_signals:
            lines.append(f"    supported by: {', '.join(self.supporting_signals)}")
        if self.contradicting_signals:
            lines.append(f"    argues against: {', '.join(self.contradicting_signals)}")
        return "\n".join(lines)


class AnalystChoice(BaseModel):
    """Exactly what the LLM is asked to return. Deliberately small: a choice, a
    confidence, a short justification, and citations."""

    candidate_id: str
    confidence: float = 0.0
    reasoning: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class VerificationIssue(BaseModel):
    code: str
    detail: str
    severity: str = "warning"      # warning | error


class Analysis(BaseModel):
    incident_detected: bool = False
    severity: str = "unknown"
    category: CauseCategory = CauseCategory.UNKNOWN

    chosen_candidate_id: str | None = None
    cause_summary: str = ""
    narrative: str = ""
    timeline: list[str] = Field(default_factory=list)

    confidence: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)

    # Provenance. A run where the model failed and the deterministic engine
    # carried the answer must not be indistinguishable from one where it worked.
    analyst: str = "llm"
    engine_top_candidate_id: str | None = None
    agrees_with_engine: bool = True
    verification: list[VerificationIssue] = Field(default_factory=list)


class InvestigationWindows(BaseModel):
    requested: TimeWindow
    incident: TimeWindow
    baseline: TimeWindow | None = None
    onset: datetime | None = None
    onset_detected: bool = False
    onset_before_window: bool = False
    method: str = ""


class InvestigationResult(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    question: str
    plan: InvestigationPlan
    windows: InvestigationWindows

    signals: list[Signal] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    analysis: Analysis = Field(default_factory=Analysis)
    # The verified, structured answer — reasoning, assumptions, citations and the
    # basis for the confidence. `analysis` carries the same conclusion in the
    # flatter shape the stored history and evaluation harness read.
    answer: StructuredAnswer | None = None

    # Every distinct thing the investigation looked at, in order, with repeats
    # folded into occurrence counts. This is the evidence the conclusion rests
    # on, shown rather than summarised.
    evidence_timeline: list[TimelineEntry] = Field(default_factory=list)

    evidence_summary: dict = Field(default_factory=dict)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
