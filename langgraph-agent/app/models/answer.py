from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .domain import TimeWindow
from app.util.timefmt import clock


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


class Episode(BaseModel):
    """One distinct stretch of elevated errors inside the period asked about.

    Onset detection returns a single moment — where the *first* accepted
    departure is. That is the right anchor for a root-cause window and the wrong
    answer to "what went wrong in the last six hours", because everything after
    the first episode ends is simply not described. A question covering three
    separate failures came back as one.

    So the range asked about is swept separately from onset detection, and every
    elevated stretch in it becomes one of these. The first is investigated in
    full; the rest are measured and reported, which is far cheaper than analysing
    each one and is the difference between "one issue" and "three issues, here is
    when each ran and what was in it".
    """

    id: str
    start: datetime
    end: datetime

    peak_errors_per_min: float = 0.0
    mean_errors_per_min: float = 0.0
    total_errors: int = 0
    # How many times busier this stretch is than the quiet level of the range it
    # was found in. Baseline-relative like every other magnitude here.
    elevation: float | None = None
    severity: str = "medium"        # critical | high | medium | low

    # Still elevated at the end of the range: this one has not resolved.
    ongoing: bool = False
    # The stretch the deep analysis was run over. Exactly one episode is primary.
    primary: bool = False

    # Filled in by the per-episode breakdown, which is one aggregation per
    # episode rather than a second full investigation.
    services: list[str] = Field(default_factory=list)
    top_errors: list[str] = Field(default_factory=list)
    breakdown_status: str = "pending"   # pending | ok | unavailable

    @property
    def minutes(self) -> float:
        return max((self.end - self.start).total_seconds() / 60.0, 1.0)

    def summary_line(self) -> str:
        head = (f"[{self.id}] {clock(self.start)}-{clock(self.end)} "
                f"({self.minutes:.0f}m, {self.severity})")
        if self.ongoing:
            head += " STILL ELEVATED AT THE END OF THE RANGE"
        if self.primary:
            head += " [investigated in full below]"
        body = (f"peak {self.peak_errors_per_min:.1f}/min, "
                f"mean {self.mean_errors_per_min:.1f}/min, "
                f"{self.total_errors} errors")
        if self.elevation is not None:
            body += f", {self.elevation:.1f}x the quiet level of the range"
        parts = [head, f"    {body}"]
        if self.services:
            parts.append(f"    services: {', '.join(self.services)}")
        for line in self.top_errors:
            parts.append(f"    - {line}")
        return "\n".join(parts)


class RecentStatus(BaseModel):
    """What the system is doing *now*, independent of the window asked about.

    A question about a period that ended an hour ago is answered about that
    period, which is correct and, on its own, unusable: the reader cannot tell a
    resolved incident from one still running. This is measured over the last
    `recent_status_minutes` whatever was asked, so every root-cause and
    health-check answer carries both tenses.

    Deterministic and cheap — two aggregations and one metric query, no model
    call — so carrying it does not make an investigation slower or more
    expensive.
    """

    window: TimeWindow
    minutes: int = 30
    status: str = "unknown"          # healthy | degraded | critical | unknown
    summary: str = ""

    total_documents: int = 0
    errors: int = 0
    warnings: int = 0
    errors_per_min: float = 0.0
    # service -> errors in the recent window, worst first.
    errors_by_service: dict[str, int] = Field(default_factory=dict)
    top_errors: list[str] = Field(default_factory=list)

    # Pods not Ready, and pods that restarted inside the recent window. A low
    # error rate does not mean healthy: a crashlooping service serves almost no
    # traffic and therefore produces almost no errors, so the rate check passes
    # while the system is still broken.
    unready_pods: list[str] = Field(default_factory=list)
    restarting_pods: list[str] = Field(default_factory=list)

    status_reason: str = ""
    unavailable: str | None = None

    def summary_line(self) -> str:
        parts = [f"CURRENT STATUS (the last {self.minutes} minutes, "
                 f"to {clock(self.window.end)}): {self.status.upper()}"]
        if self.status_reason:
            parts.append(f"    why: {self.status_reason}")
        parts.append(f"    {self.errors_per_min:.1f} errors/min "
                     f"({self.errors} errors, {self.warnings} warnings, "
                     f"{self.total_documents} log lines)")
        if self.errors_by_service:
            worst = ", ".join(f"{name} {count}"
                              for name, count in list(self.errors_by_service.items())[:5])
            parts.append(f"    errors by service: {worst}")
        if self.unready_pods:
            parts.append(f"    NOT READY right now: {', '.join(self.unready_pods[:6])}")
        if self.restarting_pods:
            parts.append(f"    restarted in this period: {', '.join(self.restarting_pods[:6])}")
        for line in self.top_errors:
            parts.append(f"    - {line}")
        if self.unavailable:
            parts.append(f"    (not fully measured: {self.unavailable})")
        return "\n".join(parts)

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
    warning_analysis: str = ""      # LLM's separate analysis of warning logs

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

    # Every elevated stretch found across the whole period asked about. The
    # narrative above is about one of them; this is the list, so a reader can see
    # at a glance that a six-hour question held three separate failures rather
    # than the one that got the full analysis. Attached by the verifier from what
    # the sweep measured, not from anything the model wrote — the model can
    # discuss them, it cannot invent one.
    window_issues: list[Episode] = Field(default_factory=list)

    # What the system is doing now, measured over a fixed recent window whatever
    # period the question covered. Present on root-cause and health-check answers
    # so "is it still happening" is answered without a second investigation.
    recent_status: RecentStatus | None = None

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
        """Every step that names no evidence, whatever it calls itself.

        Observations used to be exempt, on the theory that they merely restate a
        tool result. They do not: a tool result carries its IDs, so an observation
        with none is either a genuine null or an invention, and the label alone
        cannot tell them apart. The exemption became a loophole — one run answered
        "No log patterns matched" as an uncited *observation* and scored 80% while
        six signals sat uncollected. The healthy-system case that motivated the
        exemption is handled where it belongs, at the call site: citing nothing
        only counts against an answer when there was something to cite.
        """
        return [s for s in self.reasoning if not s.evidence_ids]


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
