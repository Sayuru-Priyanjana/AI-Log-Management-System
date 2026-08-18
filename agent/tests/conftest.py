from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.analysis import InvestigationWindows
from app.models.domain import TimeWindow
from app.models.evidence import (
    EvidenceBundle,
    K8sEvent,
    LogBucket,
    LogPattern,
    MetricPoint,
    MetricSeries,
    MetricStats,
)
from app.models.plan import Intent, InvestigationPlan

T0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def plan() -> InvestigationPlan:
    return InvestigationPlan(
        intent=Intent.INCIDENT_INVESTIGATION,
        system_id="shopdemo",
        system_name="Shop Demo",
        environment="staging",
        service="checkout-api",
        namespaces=["shopdemo"],
        requested_window=TimeWindow(start=T0, end=at(1800)),
        tools=["logs", "events", "metrics"],
        goal="why is checkout failing",
    )


@pytest.fixture
def windows() -> InvestigationWindows:
    return InvestigationWindows(
        requested=TimeWindow(start=T0, end=at(1800)),
        incident=TimeWindow(start=at(600), end=at(1800), label="incident"),
        baseline=TimeWindow(start=T0, end=at(600), label="baseline"),
        onset=at(720),
        onset_detected=True,
        method="test",
    )


def buckets(counts: list[int], *, level: str = "ERROR", step: int = 60) -> list[LogBucket]:
    return [
        LogBucket(timestamp=at(index * step), total=count, by_level={level: count})
        for index, count in enumerate(counts)
    ]


def series(metric: str, values: list[float], *, labels: dict[str, str],
           baseline: list[float] | None = None, unit: str = "") -> MetricSeries:
    def stats(data: list[float]) -> MetricStats:
        ordered = sorted(data)
        return MetricStats(
            count=len(data), minimum=ordered[0], maximum=ordered[-1],
            average=sum(data) / len(data), p95=ordered[int(0.95 * (len(ordered) - 1))],
            first=data[0], last=data[-1],
        )

    identity = ":".join(labels.get(k, "-") for k in ("pod", "container", "service", "dependency", "app")
                        if k in labels)
    return MetricSeries(
        id=f"met:{metric}:{identity}",
        metric=metric,
        unit=unit,
        labels=labels,
        incident=stats(values),
        baseline=stats(baseline) if baseline else None,
        points=[MetricPoint(timestamp=at(600 + i * 60), value=v) for i, v in enumerate(values)],
    )


def pattern(template: str, *, service: str, count: int, baseline_count: int = 0,
            level: str = "ERROR", first: int = 660) -> LogPattern:
    return LogPattern(
        id=f"pat:{service}:{abs(hash(template)) % 10**8:08d}",
        template=template, example=template, level=level, service=service,
        count=count, baseline_count=baseline_count,
        first_seen=at(first), last_seen=at(1800),
    )


def event(reason: str, *, pod: str, count: int = 1, severity: str = "warning",
          first: int = 660, message: str = "") -> K8sEvent:
    return K8sEvent(
        id=f"evt:{pod}:{reason}", reason=reason, type="Warning", severity=severity,
        message=message or f"{reason} on {pod}", count=count,
        first_timestamp=at(first), last_timestamp=at(1800),
        namespace="shopdemo", pod=pod,
    )


@pytest.fixture
def empty_evidence() -> EvidenceBundle:
    return EvidenceBundle()
