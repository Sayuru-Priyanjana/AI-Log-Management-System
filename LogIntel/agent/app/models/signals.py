from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    # Application behaviour
    ERROR_RATE_SPIKE = "ERROR_RATE_SPIKE"
    NEW_ERROR_PATTERN = "NEW_ERROR_PATTERN"
    HTTP_5XX_BURST = "HTTP_5XX_BURST"
    LATENCY_DEGRADATION = "LATENCY_DEGRADATION"
    TRAFFIC_SURGE = "TRAFFIC_SURGE"
    TRAFFIC_COLLAPSE = "TRAFFIC_COLLAPSE"

    # Dependencies
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_DEGRADED = "DEPENDENCY_DEGRADED"

    # Workload lifecycle
    POD_RESTART = "POD_RESTART"
    CRASHLOOP = "CRASHLOOP"
    OOM_KILL = "OOM_KILL"
    READINESS_FAILURE = "READINESS_FAILURE"
    SCHEDULING_FAILURE = "SCHEDULING_FAILURE"
    IMAGE_PULL_FAILURE = "IMAGE_PULL_FAILURE"
    DEPLOYMENT_CHANGE = "DEPLOYMENT_CHANGE"

    # Resources
    CPU_SATURATION = "CPU_SATURATION"
    CPU_THROTTLING = "CPU_THROTTLING"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class Magnitude(BaseModel):
    """Always baseline-relative and always carrying its unit.

    Comparing a raw value against a bare constant is how a CPU threshold of "80"
    ends up being tested against 0.42 cores. Units and baselines are therefore
    part of the type, not a convention.
    """

    baseline: float | None = None
    incident: float | None = None
    unit: str = ""
    ratio: float | None = None

    def describe(self) -> str:
        def fmt(value: float | None) -> str:
            if value is None:
                return "n/a"
            if abs(value) >= 1000:
                return f"{value:,.0f}"
            if abs(value) >= 1:
                return f"{value:.2f}"
            return f"{value:.4f}".rstrip("0").rstrip(".")

        text = f"{fmt(self.incident)} {self.unit}".strip()
        if self.baseline is not None:
            text += f" (baseline {fmt(self.baseline)} {self.unit})".rstrip()
        if self.ratio is not None:
            text += f" — {self.ratio:.1f}x"
        return text


class Signal(BaseModel):
    id: str
    type: SignalType
    severity: Severity
    description: str

    service: str | None = None
    pod: str | None = None
    namespace: str | None = None

    first_seen: datetime | None = None
    last_seen: datetime | None = None

    # The condition was already running before the investigated window began, so
    # `first_seen` has been clamped to the window start. It cannot be what
    # triggered this incident, and must not earn causal precedence for appearing
    # at the very beginning of the range.
    pre_existing: bool = False

    magnitude: Magnitude | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    detail: dict = Field(default_factory=dict)

    def summary_line(self) -> str:
        parts = [f"[{self.id}] {self.type.value} ({self.severity.value})"]
        if self.service:
            parts.append(f"service={self.service}")
        if self.pod:
            parts.append(f"pod={self.pod}")
        if self.first_seen:
            parts.append(f"onset={self.first_seen:%H:%M:%S}Z")
        if self.magnitude:
            parts.append(self.magnitude.describe())
        return " | ".join(parts) + f"\n    {self.description}"
