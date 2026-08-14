from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.config import settings
from app.models.domain import TimeWindow
from app.models.evidence import MetricEvidence, MetricPoint, MetricSeries, MetricStats
from app.models.plan import InvestigationPlan
from app.sources.prometheus import PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    unit: str
    expr: str          # {ns} is substituted with a namespace selector
    identity: tuple[str, ...] = ("pod", "container")
    # True for queries that select a specific condition (e.g. reason="OOMKilled").
    # Prometheus emits no series at all when the condition has never held, which
    # means "it did not happen" — not "the data is missing". Counting that as an
    # evidence gap would cap confidence on every healthy run.
    absence_means_false: bool = False


# Every expression is a fixed template. Nothing here is ever assembled from model
# output or user text — the only substitution is a namespace selector built from
# the registry.
SPECS: tuple[MetricSpec, ...] = (
    # --- container resources -------------------------------------------------
    MetricSpec("cpu_usage_cores", "cores",
               'sum by (pod, container) (rate(container_cpu_usage_seconds_total{{{ns}, container!=""}}[2m]))'),
    MetricSpec("cpu_limit_cores", "cores",
               'max by (pod, container) (container_spec_cpu_quota{{{ns}, container!=""}} '
               '/ container_spec_cpu_period{{{ns}, container!=""}})'),
    MetricSpec("cpu_throttle_ratio", "ratio",
               'sum by (pod, container) (rate(container_cpu_cfs_throttled_seconds_total{{{ns}, container!=""}}[2m])) '
               '/ clamp_min(sum by (pod, container) '
               '(rate(container_cpu_cfs_periods_total{{{ns}, container!=""}}[2m])), 0.001)'),
    MetricSpec("memory_working_set_bytes", "bytes",
               'max by (pod, container) (container_memory_working_set_bytes{{{ns}, container!=""}})'),
    MetricSpec("memory_limit_bytes", "bytes",
               'max by (pod, container) (container_spec_memory_limit_bytes{{{ns}, container!=""}} > 0)'),

    # --- workload lifecycle --------------------------------------------------
    MetricSpec("pod_restarts_total", "count",
               'max by (pod, container) (kube_pod_container_status_restarts_total{{{ns}}})'),
    # condition="true" is not optional: kube_pod_status_ready reports one series
    # per condition value, so without it a value of 1 could mean either
    # "ready" or "definitely not ready".
    MetricSpec("pod_ready", "bool",
               'max by (pod) (kube_pod_status_ready{{{ns}, condition="true"}})', ("pod",)),
    MetricSpec("pod_pending", "bool",
               'max by (pod) (kube_pod_status_phase{{{ns}, phase="Pending"}})', ("pod",)),
    MetricSpec("pod_oom_terminated", "bool",
               'max by (pod, container) '
               '(kube_pod_container_status_last_terminated_reason{{{ns}, reason="OOMKilled"}})',
               absence_means_false=True),
    MetricSpec("deployment_generation", "count",
               'max by (deployment) (kube_deployment_metadata_generation{{{ns}}})', ("deployment",)),

    # --- application behaviour (RED) ----------------------------------------
    MetricSpec("http_request_rate", "req/s",
               'sum by (service) (rate(http_requests_total{{{ns}}}[2m]))', ("service",)),
    MetricSpec("http_error_rate", "req/s",
               'sum by (service) (rate(http_requests_total{{{ns}, status=~"5.."}}[2m]))', ("service",)),
    MetricSpec("http_latency_p95", "seconds",
               'histogram_quantile(0.95, sum by (service, le) '
               '(rate(http_request_duration_seconds_bucket{{{ns}}}[2m])))', ("service",)),

    # --- dependencies --------------------------------------------------------
    MetricSpec("dependency_failure_rate", "req/s",
               'sum by (service, dependency) (rate(app_dependency_requests_total'
               '{{{ns}, outcome=~"failure|unreachable"}}[2m]))', ("service", "dependency")),
    MetricSpec("dependency_request_rate", "req/s",
               'sum by (service, dependency) '
               '(rate(app_dependency_requests_total{{{ns}}}[2m]))', ("service", "dependency")),
    MetricSpec("target_up", "bool",
               'max by (app, pod) (up{{{ns}}})', ("app", "pod")),
)


def summarize(values: list[float]) -> MetricStats:
    if not values:
        return MetricStats()
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    return MetricStats(
        count=len(values),
        minimum=ordered[0],
        maximum=ordered[-1],
        average=sum(values) / len(values),
        median=median,
        p95=ordered[index],
        first=values[0],
        last=values[-1],
    )


def downsample(points: list[MetricPoint], limit: int) -> list[MetricPoint]:
    """Keeps the shape of the curve without shipping every sample.

    The last point is always retained so the current value is never lost to
    rounding of the stride.
    """
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    picked = [points[int(i * stride)] for i in range(limit)]
    if picked[-1] is not points[-1]:
        picked[-1] = points[-1]
    return picked


class MetricTool:
    def __init__(self, client: PrometheusClient, concurrency: int = 6) -> None:
        self._client = client
        self._semaphore = asyncio.Semaphore(concurrency)

    @staticmethod
    def _namespace_selector(plan: InvestigationPlan) -> str:
        system_filter = f'system_id="{plan.system_id}"'
        if not plan.namespaces:
            return f'{system_filter}, namespace!=""'
        if len(plan.namespaces) == 1:
            return f'{system_filter}, namespace="{plan.namespaces[0]}"'
        return f'{system_filter}, namespace=~"' + "|".join(plan.namespaces) + '"'

    @staticmethod
    def _series_id(spec: MetricSpec, labels: dict[str, str]) -> str:
        identity = ":".join(labels.get(key, "-") for key in spec.identity)
        return f"met:{spec.metric}:{identity}"

    async def _range(self, expression: str, window: TimeWindow) -> list[dict]:
        async with self._semaphore:
            return await self._client.query_range(
                expression, window, step=self._client.step_for(window)
            )

    async def _collect_spec(self, spec: MetricSpec, namespace_selector: str,
                            incident: TimeWindow, baseline: TimeWindow | None
                            ) -> tuple[MetricSpec, str, list[MetricSeries], str | None]:
        expression = spec.expr.format(ns=namespace_selector)
        try:
            incident_raw = await self._range(expression, incident)
            baseline_raw = await self._range(expression, baseline) if baseline else []
        except PrometheusError as exc:
            return spec, expression, [], str(exc)

        baseline_by_id: dict[str, MetricStats] = {}
        for raw in baseline_raw:
            labels = {k: v for k, v in raw.get("metric", {}).items() if k != "__name__"}
            values = [value for _, value in self._client.to_points(raw)]
            baseline_by_id[self._series_id(spec, labels)] = summarize(values)

        series: list[MetricSeries] = []
        for raw in incident_raw:
            labels = {k: v for k, v in raw.get("metric", {}).items() if k != "__name__"}
            points = [MetricPoint(timestamp=ts, value=value)
                      for ts, value in self._client.to_points(raw)]
            if not points:
                continue
            series_id = self._series_id(spec, labels)
            series.append(MetricSeries(
                id=series_id,
                metric=spec.metric,
                unit=spec.unit,
                labels=labels,
                incident=summarize([point.value for point in points]),
                baseline=baseline_by_id.get(series_id),
                points=downsample(points, settings.max_metric_points),
            ))
        return spec, expression, series, None

    async def collect(self, plan: InvestigationPlan, incident: TimeWindow,
                      baseline: TimeWindow | None) -> MetricEvidence:
        evidence = MetricEvidence()
        namespace_selector = self._namespace_selector(plan)

        if not await self._client.ready():
            evidence.status = "unavailable"
            evidence.reason = f"Prometheus not ready at {self._client.base_url}"
            logger.warning(evidence.reason)
            return evidence

        results = await asyncio.gather(
            *(self._collect_spec(spec, namespace_selector, incident, baseline) for spec in SPECS),
            return_exceptions=True,
        )

        for outcome in results:
            if isinstance(outcome, BaseException):
                logger.warning("Metric collection task failed: %s", outcome)
                continue
            spec, expression, series, error = outcome
            evidence.queries[spec.metric] = expression
            if error:
                evidence.unavailable[spec.metric] = error
            elif not series and not spec.absence_means_false:
                evidence.unavailable[spec.metric] = "no series returned"
            else:
                evidence.series.extend(series)

        if not evidence.series:
            evidence.status = "unavailable"
            evidence.reason = "no metric series matched the investigation scope"
        elif evidence.unavailable:
            # Partial data is usable, but the pipeline must know what is missing
            # so a signal's absence is not read as evidence of health.
            evidence.status = "partial"
            evidence.reason = f"{len(evidence.unavailable)} of {len(SPECS)} metrics unavailable"
        return evidence
