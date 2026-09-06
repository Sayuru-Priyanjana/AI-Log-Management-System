from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .domain import TimeWindow


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------
class LogPattern(BaseModel):
    """A message template with its occurrence statistics.

    Patterns, not raw lines, are the unit of log evidence. A window holding
    thousands of documents usually holds a couple of dozen distinct templates,
    and that is the part worth putting in front of a model.
    """

    id: str
    template: str          # message with volatile parts masked
    example: str           # one real message
    level: str
    service: str | None = None
    count: int = 0
    baseline_count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    # Whether `baseline_count` was actually established, rather than merely not
    # found. The baseline aggregation is capped, so a pattern ranking below the
    # cut comes back with no bucket at all — indistinguishable, without this,
    # from one that genuinely never occurred.
    baseline_verified: bool = True

    @property
    def is_new(self) -> bool:
        """Absent from the baseline and present now. The single highest-value
        cheap signal in log analysis — and the easiest to get wrong.

        `baseline_count == 0` alone is not enough. The baseline's message
        aggregation returns at most a few dozen buckets per service, so a pattern
        that occurred in the baseline but ranked below that cut has no bucket,
        reads as count 0, and is announced as brand new. That fires
        NEW_ERROR_PATTERN — a HIGH-severity signal — on a line that has been
        happening all day.

        So absence only counts when it was checked. `baseline_verified` is set by
        a targeted lookup when the aggregation could not settle it.
        """
        return self.baseline_count == 0 and self.count > 0 and self.baseline_verified

    @property
    def growth(self) -> float | None:
        if self.baseline_count == 0:
            return None
        return self.count / self.baseline_count


class LogBucket(BaseModel):
    timestamp: datetime
    total: int = 0
    by_level: dict[str, int] = Field(default_factory=dict)

    @property
    def errors(self) -> int:
        return sum(v for k, v in self.by_level.items() if k in ("ERROR", "FATAL", "CRITICAL"))


class LogSample(BaseModel):
    id: str
    timestamp: datetime
    level: str
    service: str | None = None
    pod: str | None = None
    message: str
    http_status: int | None = None
    error_type: str | None = None
    trace_id: str | None = None


class LogEvidence(BaseModel):
    status: str = "ok"
    reason: str | None = None
    histogram: list[LogBucket] = Field(default_factory=list)
    patterns: list[LogPattern] = Field(default_factory=list)
    samples: list[LogSample] = Field(default_factory=list)
    warning_samples: list[LogSample] = Field(default_factory=list)
    totals_by_level: dict[str, int] = Field(default_factory=dict)
    baseline_totals_by_level: dict[str, int] = Field(default_factory=dict)
    # Exact document counts per service per level, straight from an aggregation
    # over the whole window. `patterns` is a ranked, truncated view for display;
    # measuring an error *rate* from it silently undercounts any service with
    # more distinct error templates than the cut allows.
    by_service_level: dict[str, dict[str, int]] = Field(default_factory=dict)
    baseline_by_service_level: dict[str, dict[str, int]] = Field(default_factory=dict)
    total_documents: int = 0
    baseline_documents: int = 0
    unparsed_documents: int = 0
    # caller -> the services it calls, observed from the logs themselves
    dependency_edges: dict[str, list[str]] = Field(default_factory=dict)

    def depth_of(self, service: str | None, _seen: frozenset[str] | None = None) -> int:
        """How many hops of dependencies sit beneath a service.

        A leaf (a datastore that calls nothing) is 0; the service at the top of
        the chain is highest. When several components are failing at once, the
        deepest one is the candidate root — the ones above it are downstream of
        whatever is wrong there.
        """
        if not service or service not in self.dependency_edges:
            return 0
        seen = _seen or frozenset()
        if service in seen:
            return 0        # cyclic graphs are possible; do not recurse forever
        below = self.dependency_edges.get(service, [])
        if not below:
            return 0
        return 1 + max(self.depth_of(child, seen | {service}) for child in below)

    def error_rate_per_minute(self, window: TimeWindow) -> float:
        errors = sum(v for k, v in self.totals_by_level.items() if k in ("ERROR", "FATAL", "CRITICAL"))
        return errors / window.minutes

    def baseline_error_rate_per_minute(self, window: TimeWindow) -> float:
        errors = sum(
            v for k, v in self.baseline_totals_by_level.items()
            if k in ("ERROR", "FATAL", "CRITICAL")
        )
        return errors / window.minutes


# --------------------------------------------------------------------------
# Kubernetes events
# --------------------------------------------------------------------------
class K8sEvent(BaseModel):
    id: str
    reason: str
    type: str = "Normal"                 # Normal | Warning, the real value
    severity: str = "info"               # info | warning | critical
    message: str = ""
    count: int = 1
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    node: str | None = None
    service: str | None = None
    involved_kind: str | None = None
    involved_name: str | None = None

    @property
    def onset(self) -> datetime | None:
        """When this condition actually started. A Kubernetes event aggregates
        repeats, so its document timestamp is the *last* occurrence — using it
        for ordering would place a long-running problem after its own effects."""
        return self.first_timestamp or self.last_timestamp


class EventEvidence(BaseModel):
    status: str = "ok"
    reason: str | None = None
    events: list[K8sEvent] = Field(default_factory=list)
    baseline_reasons: dict[str, int] = Field(default_factory=dict)

    def by_reason(self, *reasons: str) -> list[K8sEvent]:
        wanted = {r.lower() for r in reasons}
        return [e for e in self.events if e.reason.lower() in wanted]

    def matching(self, needle: str) -> list[K8sEvent]:
        needle = needle.lower()
        return [e for e in self.events
                if needle in e.reason.lower() or needle in e.message.lower()]


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
class MetricPoint(BaseModel):
    timestamp: datetime
    value: float


class MetricStats(BaseModel):
    count: int = 0
    minimum: float | None = None
    maximum: float | None = None
    average: float | None = None
    # The typical value over the window. For a series that is itself a
    # percentile, this is the statistic to compare: the mean and the max are
    # both dominated by brief spikes, and on a shared host those spikes hit
    # every service at once and say nothing about any one of them.
    median: float | None = None
    p95: float | None = None
    first: float | None = None
    last: float | None = None

    @property
    def delta(self) -> float | None:
        if self.first is None or self.last is None:
            return None
        return self.last - self.first

    @property
    def is_empty(self) -> bool:
        return self.count == 0


class MetricSeries(BaseModel):
    id: str
    metric: str                  # logical name, e.g. "cpu_usage_cores"
    unit: str = ""
    labels: dict[str, str] = Field(default_factory=dict)
    incident: MetricStats = Field(default_factory=MetricStats)
    baseline: MetricStats | None = None
    points: list[MetricPoint] = Field(default_factory=list)

    @property
    def pod(self) -> str | None:
        return self.labels.get("pod")

    @property
    def container(self) -> str | None:
        return self.labels.get("container")

    @property
    def service(self) -> str | None:
        return self.labels.get("service") or self.labels.get("app") or self.container

    def ratio_to_baseline(self) -> float | None:
        if not self.baseline or self.baseline.average in (None, 0):
            return None
        if self.incident.average is None:
            return None
        return self.incident.average / self.baseline.average


class MetricEvidence(BaseModel):
    status: str = "ok"
    reason: str | None = None
    series: list[MetricSeries] = Field(default_factory=list)
    queries: dict[str, str] = Field(default_factory=dict)
    unavailable: dict[str, str] = Field(default_factory=dict)

    def of(self, metric: str) -> list[MetricSeries]:
        return [s for s in self.series if s.metric == metric]

    def peak(self, metric: str) -> MetricSeries | None:
        candidates = [s for s in self.of(metric) if s.incident.maximum is not None]
        return max(candidates, key=lambda s: s.incident.maximum or 0.0, default=None)


# --------------------------------------------------------------------------
# Bundle
# --------------------------------------------------------------------------
class EvidenceBundle(BaseModel):
    logs: LogEvidence = Field(default_factory=LogEvidence)
    events: EventEvidence = Field(default_factory=EventEvidence)
    metrics: MetricEvidence = Field(default_factory=MetricEvidence)

    def statuses(self) -> dict[str, str]:
        return {
            "logs": self.logs.status,
            "events": self.events.status,
            "metrics": self.metrics.status,
        }

    def gaps(self) -> list[str]:
        """What could not be collected. Reported explicitly so a conclusion drawn
        from partial evidence is never presented as if it were complete."""
        missing = []
        for name, source in (("application logs", self.logs),
                             ("kubernetes events", self.events),
                             ("metrics", self.metrics)):
            if source.status != "ok":
                missing.append(f"{name}: {source.reason or source.status}")
        if self.logs.status == "ok" and not self.logs.patterns:
            missing.append("no application logs matched the investigation scope")
        return missing
