from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class AnswerMode(str, Enum):
    """What shape of answer the question actually calls for.

    "List the 5xx errors from payment-api" and "why did checkout fail" are not
    the same question, and answering the first with an incident report is a wrong
    answer however good the report is. The mode is decided from the plan's intent
    and drives both what the agent is asked to produce and how the UI renders it.
    """

    ROOT_CAUSE = "root_cause"           # why did this break
    DATA_EXTRACTION = "data_extraction"  # show me / list / find
    AGGREGATION = "aggregation"          # how many / count / rate
    HEALTH_CHECK = "health_check"        # is it healthy right now
    EXPLANATION = "explanation"          # what does this mean / describe


class CitationStatus(str, Enum):
    RESOLVED = "resolved"       # points at real evidence in this run
    UNRESOLVED = "unresolved"   # refers to nothing that was collected
    INFERRED = "inferred"       # added by the pipeline, not cited by the model


class Citation(BaseModel):
    """A pointer from a claim to the evidence behind it.

    Every citation is checked against the evidence actually collected, and the
    result is kept on the citation rather than silently dropping the bad ones —
    a reader who can see that two of five references went nowhere can judge the
    answer for themselves.
    """

    id: str
    label: str = ""
    status: CitationStatus = CitationStatus.RESOLVED
    detail: str = ""


class ReasoningStep(BaseModel):
    """One link in the chain: a claim, why it follows, and what backs it.

    Prose hides its own gaps. Forcing each step to name its support makes an
    unsupported leap visible as an empty `evidence_ids` rather than a confident
    sentence.
    """

    claim: str
    because: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    kind: str = "inference"     # observation | inference | elimination


class Assumption(BaseModel):
    """Something taken as true that the evidence does not establish."""

    statement: str
    basis: str = ""             # why it is reasonable to assume
    impact_if_wrong: str = ""   # what the conclusion loses if it does not hold


class ConfidenceFactor(BaseModel):
    """One reason the confidence is where it is.

    A bare number is not accountable. Listing what raised and lowered it makes
    the score arguable, which is the point.
    """

    factor: str
    direction: str      # raises | lowers
    weight: float = 0.0


class TimelineEntry(BaseModel):
    """One thing that happened, with everything repeated about it folded in.

    Deliberately *not* one row per log line. A window holding 40,000 documents
    holds maybe a dozen distinct things; printing every line buries the dozen.
    Each entry is a distinct message template, Kubernetes event, or metric
    movement, with `occurrences` recording how many times it happened and
    `first_seen`/`last_seen` bounding when.
    """

    id: str
    kind: str                       # log | event | metric | signal | marker
    first_seen: datetime
    last_seen: datetime | None = None

    title: str
    detail: str = ""
    service: str | None = None
    level: str = ""                 # ERROR / WARN / Warning / critical / ...

    occurrences: int = 1
    baseline_occurrences: int | None = None

    # Whether this is worth the reader's attention, and why. Highlighting
    # everything highlights nothing, so the reason has to be stateable.
    notable: bool = False
    notable_reason: str = ""

    @property
    def is_repeated(self) -> bool:
        return self.occurrences > 1


class NextStep(BaseModel):
    """Something to do next, and — where possible — the means to do it.

    A suggestion the reader has to go and perform by hand is a dead end at the
    exact moment the investigation was getting somewhere. Each step therefore
    carries an executable form when one can be derived:

    - `tool`   — re-run a single tool against the evidence already gathered.
                 Cheap and instant: no model call, nothing re-reasoned.
    - `question` — hand the step back to the agent as a fresh investigation.

    The tool call is validated against the real registry before it is offered, so
    a button never appears for something that cannot run.
    """

    label: str
    kind: str = "manual"                    # tool | investigation | manual
    tool: str | None = None
    tool_input: dict = Field(default_factory=dict)
    question: str | None = None
    reason: str = ""                        # why this is worth doing

    @property
    def is_executable(self) -> bool:
        return self.kind in ("tool", "investigation")


class DataTable(BaseModel):
    """Tabular result for extraction and aggregation answers."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    total_matched: int = 0
    truncated: bool = False
    query_description: str = ""


class StructuredAnswer(BaseModel):
    """The final answer, in a shape that can be read, checked and scored."""

    mode: AnswerMode = AnswerMode.ROOT_CAUSE

    headline: str = ""              # the direct answer, one sentence
    detail: str = ""                # the fuller explanation

    # Root-cause specifics. Empty for other modes.
    root_cause_service: str | None = None
    cause_category: str | None = None

    reasoning: list[ReasoningStep] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)

    confidence: float = 0.0
    confidence_factors: list[ConfidenceFactor] = Field(default_factory=list)

    limitations: list[str] = Field(default_factory=list)
    next_steps: list[NextStep] = Field(default_factory=list)

    # Populated for extraction and aggregation answers.
    table: DataTable | None = None

    @field_validator("next_steps", mode="before")
    @classmethod
    def _accept_plain_strings(cls, value):
        """Investigations stored before next steps became executable hold plain
        strings. Coerce rather than fail: the history is the audit trail, and it
        must stay readable across a schema change."""
        if not isinstance(value, list):
            return value
        return [{"label": item, "kind": "manual"} if isinstance(item, str) else item
                for item in value]

    @property
    def unresolved_citations(self) -> list[Citation]:
        return [c for c in self.citations if c.status is CitationStatus.UNRESOLVED]

    @property
    def unsupported_claims(self) -> list[ReasoningStep]:
        return [s for s in self.reasoning
                if s.kind != "observation" and not s.evidence_ids]


# Which mode each intent produces. A lookup rather than a model decision: the
# intent has already been validated, and re-asking invites disagreement between
# two stages that must agree.
MODE_BY_INTENT: dict[str, AnswerMode] = {
    "incident_investigation": AnswerMode.ROOT_CAUSE,
    "health_check": AnswerMode.HEALTH_CHECK,
    "performance_review": AnswerMode.ROOT_CAUSE,
    "historical_query": AnswerMode.DATA_EXTRACTION,
    "data_extraction": AnswerMode.DATA_EXTRACTION,
    "aggregation": AnswerMode.AGGREGATION,
}
