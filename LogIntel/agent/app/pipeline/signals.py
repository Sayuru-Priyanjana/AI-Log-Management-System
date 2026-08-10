from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime

from app.config import settings
from app.models.analysis import InvestigationWindows
from app.models.evidence import EvidenceBundle, MetricSeries
from app.models.plan import InvestigationPlan
from app.models.signals import Magnitude, Severity, Signal, SignalType

logger = logging.getLogger(__name__)

ERROR_LEVELS = ("ERROR", "FATAL", "CRITICAL")

# payment-api-69d7b68776-mqxxd -> payment-api
#
# The character class is Kubernetes' own "safe" alphabet for generated suffixes:
# consonants and digits, no vowels. Using a plain [a-z0-9] here would treat any
# eight-letter word as a hash, so `unknown-workload-abc12` would resolve to
# "unknown".
_HASH = "[bcdfghjkmnpqrstvwxz2456789]"
_REPLICASET_POD = re.compile(rf"^(?P<name>.+?)-{_HASH}{{8,10}}-{_HASH}{{5}}$")
_SIMPLE_POD = re.compile(rf"^(?P<name>.+?)-{_HASH}{{5}}$")


class ServiceResolver:
    """Maps pod names back to service names.

    Container names cannot be relied on for this — every workload here runs a
    container literally called `app`. Matching against the services the registry
    actually knows about is both more accurate and self-correcting.
    """

    def __init__(self, known_services: list[str]) -> None:
        self._known = sorted(known_services, key=len, reverse=True)

    def from_pod(self, pod: str | None) -> str | None:
        if not pod:
            return None
        for service in self._known:
            if pod.startswith(service + "-") or pod == service:
                return service
        for pattern in (_REPLICASET_POD, _SIMPLE_POD):
            match = pattern.match(pod)
            if match:
                return match.group("name")
        return pod

    def of(self, series: MetricSeries) -> str | None:
        explicit = series.labels.get("service") or series.labels.get("app")
        if explicit:
            return explicit
        return self.from_pod(series.labels.get("pod"))


def _first_crossing(series: MetricSeries, predicate) -> datetime | None:
    """When the series first met a condition — the signal's true onset.

    Using the window start instead would make every signal appear simultaneous
    and destroy the ordering the hypothesis engine ranks on.
    """
    for point in series.points:
        if predicate(point.value):
            return point.timestamp
    return series.points[0].timestamp if series.points else None


def _pair_by_identity(numerators: list[MetricSeries],
                      denominators: list[MetricSeries],
                      keys: tuple[str, ...]) -> list[tuple[MetricSeries, MetricSeries]]:
    index = {tuple(s.labels.get(k, "") for k in keys): s for s in denominators}
    pairs = []
    for series in numerators:
        match = index.get(tuple(series.labels.get(k, "") for k in keys))
        if match is not None:
            pairs.append((series, match))
    return pairs


def _peak_ratio(numerator: MetricSeries, denominator: MetricSeries) -> tuple[float, datetime | None]:
    """The highest pointwise ratio in the window, and when it occurred.

    Averaging the two series and dividing understates a burst that occupies only
    part of the window — and it usually does, because the window deliberately
    starts before the onset. A 60%-of-requests failure rate measured across a
    window that is half pre-incident averages out to under the threshold and
    vanishes.

    Taking the peak is safe here specifically because these series are already
    `rate[2m]` values: every point is an average over two minutes, so a "peak"
    is a sustained condition rather than a single stray sample.
    """
    denominators = {point.timestamp: point.value for point in denominator.points}
    best, when = 0.0, None
    for point in numerator.points:
        total = denominators.get(point.timestamp)
        if not total:
            continue
        ratio = point.value / total
        if ratio > best:
            best, when = ratio, point.timestamp
    return best, when


class SignalEngine:
    def __init__(self, known_services: list[str] | None = None) -> None:
        self.resolver = ServiceResolver(known_services or [])
        self._counter: dict[str, int] = defaultdict(int)

    def _signal_id(self, signal_type: SignalType, scope: str | None) -> str:
        slug = (scope or "system").replace(":", "-")
        key = f"{signal_type.value}:{slug}"
        self._counter[key] += 1
        suffix = "" if self._counter[key] == 1 else f"#{self._counter[key]}"
        return f"sig:{signal_type.value}:{slug}{suffix}"

    def detect(self, plan: InvestigationPlan, windows: InvestigationWindows,
               evidence: EvidenceBundle) -> list[Signal]:
        self._counter.clear()
        signals: list[Signal] = []

        signals += self._from_logs(windows, evidence)
        signals += self._from_http_metrics(evidence)
        signals += self._from_dependencies(evidence)
        signals += self._from_resources(evidence)
        signals += self._from_lifecycle(evidence)
        signals += self._from_events(evidence, windows)

        # Causal ordering. Signals without a known onset sort last: they cannot
        # be shown to precede anything, so they must not outrank something that can.
        signals.sort(key=lambda s: (s.first_seen is None, s.first_seen or windows.incident.end))
        logger.info("Detected %d signal(s): %s", len(signals),
                    ", ".join(sorted({s.type.value for s in signals})) or "none")
        return signals

    # ------------------------------------------------------------------ logs
    def _from_logs(self, windows: InvestigationWindows, evidence: EvidenceBundle) -> list[Signal]:
        logs = evidence.logs
        if logs.status != "ok":
            return []
        signals: list[Signal] = []
        baseline_window = windows.baseline

        # -- error rate, per service -------------------------------------
        incident_errors: dict[str, int] = defaultdict(int)
        baseline_errors: dict[str, int] = defaultdict(int)
        for pattern in logs.patterns:
            if pattern.level in ERROR_LEVELS:
                incident_errors[pattern.service or "system"] += pattern.count
                baseline_errors[pattern.service or "system"] += pattern.baseline_count

        for service, count in incident_errors.items():
            rate = count / windows.incident.minutes
            if rate < settings.error_rate_min_per_minute:
                continue
            baseline_rate = (
                baseline_errors[service] / baseline_window.minutes if baseline_window else None
            )
            ratio = (rate / baseline_rate) if baseline_rate else None

            # With no baseline we cannot say the rate *rose*, only that it is
            # high. That distinction is preserved rather than assumed away.
            if baseline_rate is None:
                elevated, note = rate >= settings.error_rate_min_per_minute * 3, "no baseline available"
            elif baseline_rate == 0:
                elevated, note, ratio = True, "no errors at all in the baseline window", None
            else:
                elevated = ratio is not None and ratio >= settings.error_rate_spike_multiplier
                note = f"{ratio:.1f}x the baseline rate" if ratio else ""

            if not elevated:
                continue
            onset = min(
                (p.first_seen for p in logs.patterns
                 if p.service == service and p.level in ERROR_LEVELS and p.first_seen),
                default=None,
            )
            signals.append(Signal(
                id=self._signal_id(SignalType.ERROR_RATE_SPIKE, service),
                type=SignalType.ERROR_RATE_SPIKE,
                severity=Severity.HIGH,
                service=None if service == "system" else service,
                first_seen=onset,
                last_seen=windows.incident.end,
                magnitude=Magnitude(baseline=baseline_rate, incident=rate,
                                    unit="errors/min", ratio=ratio),
                description=f"{service} error rate is {rate:.1f}/min ({note}).",
                evidence_ids=[p.id for p in logs.patterns
                              if p.service == service and p.level in ERROR_LEVELS][:5],
            ))

        # -- brand new error patterns -------------------------------------
        if baseline_window is not None:
            for pattern in logs.patterns:
                if not (pattern.is_new and pattern.level in ERROR_LEVELS):
                    continue
                if pattern.count < 3:
                    continue
                signals.append(Signal(
                    id=self._signal_id(SignalType.NEW_ERROR_PATTERN, pattern.service),
                    type=SignalType.NEW_ERROR_PATTERN,
                    severity=Severity.HIGH,
                    service=pattern.service,
                    first_seen=pattern.first_seen,
                    last_seen=pattern.last_seen,
                    magnitude=Magnitude(baseline=0, incident=pattern.count, unit="occurrences"),
                    description=(f"An error not seen at all in the baseline window appeared "
                                 f"{pattern.count} times: \"{pattern.example[:180]}\""),
                    evidence_ids=[pattern.id],
                    detail={"template": pattern.template},
                ))
        return signals

    # ------------------------------------------------------- HTTP behaviour
    def _from_http_metrics(self, evidence: EvidenceBundle) -> list[Signal]:
        metrics = evidence.metrics
        if metrics.status == "unavailable":
            return []
        signals: list[Signal] = []

        # -- 5xx ratio -----------------------------------------------------
        for errors, total in _pair_by_identity(
            metrics.of("http_error_rate"), metrics.of("http_request_rate"), ("service",)
        ):
            if not total.incident.average:
                continue
            ratio, peaked_at = _peak_ratio(errors, total)
            if ratio < settings.http_5xx_ratio_threshold:
                continue
            service = errors.labels.get("service")
            baseline_ratio = None
            if errors.baseline and total.baseline and total.baseline.average:
                baseline_ratio = (errors.baseline.average or 0.0) / total.baseline.average
            signals.append(Signal(
                id=self._signal_id(SignalType.HTTP_5XX_BURST, service),
                type=SignalType.HTTP_5XX_BURST,
                severity=Severity.HIGH,
                service=service,
                first_seen=_first_crossing(errors, lambda v: v > 0),
                magnitude=Magnitude(baseline=baseline_ratio, incident=ratio,
                                    unit="of requests at peak",
                                    ratio=(ratio / baseline_ratio) if baseline_ratio else None),
                description=(f"{ratio:.0%} of {service} requests returned 5xx at the worst point"
                             + (f" ({peaked_at:%H:%M:%S}Z)." if peaked_at else ".")),
                evidence_ids=[errors.id, total.id],
            ))

        # -- latency -------------------------------------------------------
        # Compared on the median of the p95 series — the *typical* tail — rather
        # than its peak. A shared host stalls occasionally and drives every
        # service's peak p95 to the same value at the same moment; that is a
        # property of the host, not evidence about any service. The median moves
        # only when latency is genuinely, persistently worse.
        for series in metrics.of("http_latency_p95"):
            incident_latency = series.incident.median or series.incident.average
            if incident_latency is None or incident_latency < settings.latency_min_seconds:
                continue
            baseline_latency = (series.baseline.median or series.baseline.average) if series.baseline else None
            ratio = (incident_latency / baseline_latency) if baseline_latency else None
            if ratio is not None and ratio < settings.latency_degradation_multiplier:
                continue
            if ratio is None and incident_latency < settings.latency_min_seconds * 4:
                continue
            service = series.labels.get("service")
            threshold = ((baseline_latency or 0) * settings.latency_degradation_multiplier
                         if baseline_latency else settings.latency_min_seconds)
            signals.append(Signal(
                id=self._signal_id(SignalType.LATENCY_DEGRADATION, service),
                type=SignalType.LATENCY_DEGRADATION,
                severity=Severity.MEDIUM,
                service=service,
                first_seen=_first_crossing(series, lambda v, t=threshold: v >= t),
                magnitude=Magnitude(baseline=baseline_latency, incident=incident_latency,
                                    unit="s p95 (typical)", ratio=ratio),
                description=(f"{service} p95 latency is typically {incident_latency:.2f}s"
                             + (f", {ratio:.1f}x the baseline." if ratio else ".")),
                evidence_ids=[series.id],
            ))

        # -- traffic volume -------------------------------------------------
        for series in metrics.of("http_request_rate"):
            ratio = series.ratio_to_baseline()
            service = series.labels.get("service")
            if ratio and ratio >= settings.traffic_surge_multiplier:
                signals.append(Signal(
                    id=self._signal_id(SignalType.TRAFFIC_SURGE, service),
                    type=SignalType.TRAFFIC_SURGE,
                    severity=Severity.MEDIUM,
                    service=service,
                    first_seen=_first_crossing(
                        series, lambda v: v >= (series.baseline.average or 0) * settings.traffic_surge_multiplier),
                    magnitude=Magnitude(baseline=series.baseline.average if series.baseline else None,
                                        incident=series.incident.average, unit="req/s", ratio=ratio),
                    description=f"{service} request rate rose {ratio:.1f}x above baseline.",
                    evidence_ids=[series.id],
                ))
            elif (ratio is not None and ratio <= 0.3 and series.baseline
                  and (series.baseline.average or 0) > 0.2):
                signals.append(Signal(
                    id=self._signal_id(SignalType.TRAFFIC_COLLAPSE, service),
                    type=SignalType.TRAFFIC_COLLAPSE,
                    severity=Severity.HIGH,
                    service=service,
                    first_seen=_first_crossing(
                        series, lambda v: v <= (series.baseline.average or 0) * 0.3),
                    magnitude=Magnitude(baseline=series.baseline.average,
                                        incident=series.incident.average, unit="req/s", ratio=ratio),
                    description=(f"{service} request rate collapsed to {ratio:.0%} of baseline — "
                                 f"it may have stopped receiving traffic."),
                    evidence_ids=[series.id],
                ))
        return signals

    # --------------------------------------------------------- dependencies
    def _from_dependencies(self, evidence: EvidenceBundle) -> list[Signal]:
        metrics = evidence.metrics
        signals: list[Signal] = []

        for failures, total in _pair_by_identity(
            metrics.of("dependency_failure_rate"), metrics.of("dependency_request_rate"),
            ("service", "dependency"),
        ):
            if not total.incident.average:
                continue
            # Peak rather than window average, for the same reason as the 5xx
            # ratio: the window starts before the onset by design, so averaging
            # dilutes a real outage towards nothing.
            ratio, _ = _peak_ratio(failures, total)
            if ratio < 0.1:
                continue
            caller = failures.labels.get("service")
            dependency = failures.labels.get("dependency")
            unavailable = ratio >= 0.9
            signals.append(Signal(
                id=self._signal_id(
                    SignalType.DEPENDENCY_UNAVAILABLE if unavailable else SignalType.DEPENDENCY_DEGRADED,
                    dependency),
                type=SignalType.DEPENDENCY_UNAVAILABLE if unavailable else SignalType.DEPENDENCY_DEGRADED,
                severity=Severity.CRITICAL if unavailable else Severity.HIGH,
                # Attributed to the dependency, not the caller reporting it. The
                # failing component is the answer; the caller is the symptom.
                service=dependency,
                first_seen=_first_crossing(failures, lambda v: v > 0),
                magnitude=Magnitude(
                    baseline=((failures.baseline.average or 0.0) / total.baseline.average)
                    if failures.baseline and total.baseline and total.baseline.average else None,
                    incident=ratio, unit="of calls failing"),
                description=(f"{ratio:.0%} of calls from {caller} to {dependency} are failing"
                             + (" — the dependency looks unavailable." if unavailable
                                else " — the dependency is degraded.")),
                evidence_ids=[failures.id, total.id],
                detail={"caller": caller, "dependency": dependency},
            ))

        # A scrape target that stopped answering is direct evidence the process
        # is gone, independent of whatever its callers observed.
        for series in metrics.of("target_up"):
            if series.incident.minimum is None or series.incident.minimum > 0:
                continue
            service = series.labels.get("app") or self.resolver.from_pod(series.labels.get("pod"))
            signals.append(Signal(
                id=self._signal_id(SignalType.DEPENDENCY_UNAVAILABLE, f"{service}-target"),
                type=SignalType.DEPENDENCY_UNAVAILABLE,
                severity=Severity.CRITICAL,
                service=service,
                pod=series.labels.get("pod"),
                first_seen=_first_crossing(series, lambda v: v == 0),
                magnitude=Magnitude(baseline=series.baseline.average if series.baseline else None,
                                    incident=series.incident.average, unit="up"),
                description=f"{service} stopped responding to metric scrapes (up = 0).",
                evidence_ids=[series.id],
            ))
        return signals

    # ------------------------------------------------------------ resources
    def _from_resources(self, evidence: EvidenceBundle) -> list[Signal]:
        metrics = evidence.metrics
        signals: list[Signal] = []

        # -- CPU against its own limit -------------------------------------
        for usage, limit in _pair_by_identity(
            metrics.of("cpu_usage_cores"), metrics.of("cpu_limit_cores"), ("pod", "container")
        ):
            limit_cores = limit.incident.maximum
            if not limit_cores or limit_cores <= 0:
                continue
            peak_ratio = (usage.incident.maximum or 0.0) / limit_cores
            if peak_ratio < settings.cpu_saturation_ratio:
                continue
            pod = usage.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.CPU_SATURATION, pod),
                type=SignalType.CPU_SATURATION,
                severity=Severity.MEDIUM,
                service=self.resolver.of(usage), pod=pod,
                first_seen=_first_crossing(
                    usage, lambda v: v >= limit_cores * settings.cpu_saturation_ratio),
                magnitude=Magnitude(
                    baseline=(usage.baseline.maximum / limit_cores)
                    if usage.baseline and usage.baseline.maximum else None,
                    incident=peak_ratio, unit="of CPU limit"),
                description=(f"{pod} reached {peak_ratio:.0%} of its CPU limit "
                             f"({usage.incident.maximum:.2f} of {limit_cores:.2f} cores)."),
                evidence_ids=[usage.id, limit.id],
            ))

        # -- Throttling: the consequence of saturation, and better evidence --
        for series in metrics.of("cpu_throttle_ratio"):
            peak = series.incident.maximum or 0.0
            if peak < settings.cpu_throttle_ratio:
                continue
            pod = series.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.CPU_THROTTLING, pod),
                type=SignalType.CPU_THROTTLING,
                severity=Severity.HIGH,
                service=self.resolver.of(series), pod=pod,
                first_seen=_first_crossing(series, lambda v: v >= settings.cpu_throttle_ratio),
                magnitude=Magnitude(baseline=series.baseline.maximum if series.baseline else None,
                                    incident=peak, unit="of periods throttled",
                                    ratio=series.ratio_to_baseline()),
                description=(f"{pod} had {peak:.0%} of its CPU periods throttled — "
                             f"it is being actively held back by its limit."),
                evidence_ids=[series.id],
            ))

        # -- Memory against its own limit ------------------------------------
        for usage, limit in _pair_by_identity(
            metrics.of("memory_working_set_bytes"), metrics.of("memory_limit_bytes"),
            ("pod", "container"),
        ):
            limit_bytes = limit.incident.maximum
            if not limit_bytes or limit_bytes <= 0:
                continue
            peak_ratio = (usage.incident.maximum or 0.0) / limit_bytes
            if peak_ratio < settings.memory_pressure_ratio:
                continue
            pod = usage.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.MEMORY_PRESSURE, pod),
                type=SignalType.MEMORY_PRESSURE,
                severity=Severity.HIGH,
                service=self.resolver.of(usage), pod=pod,
                first_seen=_first_crossing(
                    usage, lambda v: v >= limit_bytes * settings.memory_pressure_ratio),
                magnitude=Magnitude(
                    baseline=(usage.baseline.maximum / limit_bytes)
                    if usage.baseline and usage.baseline.maximum else None,
                    incident=peak_ratio, unit="of memory limit"),
                description=(f"{pod} reached {peak_ratio:.0%} of its memory limit "
                             f"({(usage.incident.maximum or 0) / 1e6:.0f}MB of "
                             f"{limit_bytes / 1e6:.0f}MB)."),
                evidence_ids=[usage.id, limit.id],
            ))
        return signals

    # ------------------------------------------------------------ lifecycle
    def _from_lifecycle(self, evidence: EvidenceBundle) -> list[Signal]:
        metrics = evidence.metrics
        signals: list[Signal] = []
        backoff_pods = {e.pod for e in evidence.events.matching("backoff") if e.pod}

        for series in metrics.of("pod_restarts_total"):
            delta = series.incident.delta or 0.0
            if delta <= 0:
                continue
            pod = series.labels.get("pod")
            crashlooping = delta >= 2 or pod in backoff_pods
            signals.append(Signal(
                id=self._signal_id(
                    SignalType.CRASHLOOP if crashlooping else SignalType.POD_RESTART, pod),
                type=SignalType.CRASHLOOP if crashlooping else SignalType.POD_RESTART,
                severity=Severity.CRITICAL if crashlooping else Severity.HIGH,
                service=self.resolver.of(series), pod=pod,
                first_seen=_first_crossing(series, lambda v: v > (series.incident.first or 0)),
                magnitude=Magnitude(baseline=series.incident.first, incident=series.incident.last,
                                    unit="restarts"),
                description=(f"{pod} restarted {delta:.0f} time(s) during the window"
                             + (" and is in a restart backoff loop." if crashlooping else ".")),
                evidence_ids=[series.id],
            ))

        for series in metrics.of("pod_oom_terminated"):
            if (series.incident.maximum or 0) < 1:
                continue
            pod = series.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.OOM_KILL, pod),
                type=SignalType.OOM_KILL,
                severity=Severity.CRITICAL,
                service=self.resolver.of(series), pod=pod,
                first_seen=_first_crossing(series, lambda v: v >= 1),
                description=f"{pod} was last terminated by the kernel OOM killer.",
                evidence_ids=[series.id],
            ))

        for series in metrics.of("pod_ready"):
            if series.incident.minimum is None or series.incident.minimum > 0:
                continue
            pod = series.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.READINESS_FAILURE, pod),
                type=SignalType.READINESS_FAILURE,
                severity=Severity.HIGH,
                service=self.resolver.from_pod(pod), pod=pod,
                first_seen=_first_crossing(series, lambda v: v == 0),
                magnitude=Magnitude(baseline=series.baseline.average if series.baseline else None,
                                    incident=series.incident.average, unit="ready"),
                description=f"{pod} was not Ready and was removed from its Service endpoints.",
                evidence_ids=[series.id],
            ))

        for series in metrics.of("pod_pending"):
            if (series.incident.maximum or 0) < 1:
                continue
            pod = series.labels.get("pod")
            signals.append(Signal(
                id=self._signal_id(SignalType.SCHEDULING_FAILURE, pod),
                type=SignalType.SCHEDULING_FAILURE,
                severity=Severity.CRITICAL,
                service=self.resolver.from_pod(pod), pod=pod,
                first_seen=_first_crossing(series, lambda v: v >= 1),
                description=f"{pod} is stuck in Pending and has not been scheduled.",
                evidence_ids=[series.id],
            ))

        for series in metrics.of("deployment_generation"):
            if (series.incident.delta or 0) <= 0:
                continue
            deployment = series.labels.get("deployment")
            signals.append(Signal(
                id=self._signal_id(SignalType.DEPLOYMENT_CHANGE, deployment),
                type=SignalType.DEPLOYMENT_CHANGE,
                severity=Severity.MEDIUM,
                service=deployment,
                first_seen=_first_crossing(series, lambda v: v > (series.incident.first or 0)),
                magnitude=Magnitude(baseline=series.incident.first, incident=series.incident.last,
                                    unit="generation"),
                description=(f"Deployment {deployment} changed during the window "
                             f"(generation {series.incident.first:.0f} -> "
                             f"{series.incident.last:.0f})."),
                evidence_ids=[series.id],
            ))
        return signals

    # --------------------------------------------------------------- events
    def _from_events(self, evidence: EvidenceBundle,
                     windows: InvestigationWindows) -> list[Signal]:
        """Event-derived signals, keyed on `reason`.

        Reason is the stable, meaningful field. Deriving these from a normalised
        `action` would mean anything outside the mapping table becomes invisible —
        and in practice the unmapped reasons are the interesting ones.
        """
        signals: list[Signal] = []
        window_start = windows.incident.start
        rules: tuple[tuple[tuple[str, ...], SignalType, Severity, str], ...] = (
            (("OOMKilling", "OOMKilled", "SystemOOM"), SignalType.OOM_KILL,
             Severity.CRITICAL, "container was OOM-killed"),
            (("BackOff",), SignalType.CRASHLOOP,
             Severity.CRITICAL, "container is in CrashLoopBackOff"),
            (("FailedScheduling",), SignalType.SCHEDULING_FAILURE,
             Severity.CRITICAL, "pod could not be scheduled"),
            (("Unhealthy", "ProbeWarning"), SignalType.READINESS_FAILURE,
             Severity.HIGH, "probe failure reported by the kubelet"),
            (("ImagePullBackOff", "ErrImagePull", "Failed"), SignalType.IMAGE_PULL_FAILURE,
             Severity.HIGH, "image could not be pulled"),
            (("Evicted",), SignalType.MEMORY_PRESSURE,
             Severity.CRITICAL, "pod was evicted under node pressure"),
            (("ScalingReplicaSet", "SuccessfulCreate"), SignalType.DEPLOYMENT_CHANGE,
             Severity.LOW, "workload was scaled or rolled out"),
        )

        for reasons, signal_type, severity, blurb in rules:
            for event in evidence.events.by_reason(*reasons):
                # Image pull "Failed" is a generic reason; only treat it as an
                # image problem when the message actually says so.
                if signal_type is SignalType.IMAGE_PULL_FAILURE and "image" not in event.message.lower():
                    continue
                baseline_count = evidence.events.baseline_reasons.get(event.reason, 0)

                # first_timestamp, not the document timestamp: the condition
                # started when it first fired, not when it last repeated.
                # But a condition that was already running long before the window
                # is a pre-existing state, not this incident's trigger — reporting
                # its true start as the onset would make it outrank everything on
                # causal precedence purely for being old.
                true_onset = event.onset
                pre_existing = bool(true_onset and true_onset < window_start)
                effective_onset = window_start if pre_existing else true_onset

                note = ""
                if pre_existing and true_onset:
                    note = (f" This condition was already present before the window "
                            f"(first seen {true_onset:%Y-%m-%d %H:%M:%S}Z), so it predates "
                            f"the incident rather than starting it.")

                signals.append(Signal(
                    id=self._signal_id(signal_type, f"{event.pod or event.involved_name}-{event.reason}"),
                    type=signal_type,
                    severity=Severity.MEDIUM if pre_existing and severity is Severity.HIGH else severity,
                    # Events on Deployments and ReplicaSets carry no pod, and the
                    # collector cannot resolve their labels from the pod cache, so
                    # they arrive with no service. Their object name is the service
                    # name (plus a ReplicaSet hash the resolver already strips),
                    # which is otherwise thrown away — leaving a rollout event
                    # attributed to nothing at all.
                    service=(event.service
                             or self.resolver.from_pod(event.pod)
                             or self.resolver.from_pod(event.involved_name)),
                    pod=event.pod,
                    namespace=event.namespace,
                    first_seen=effective_onset,
                    last_seen=event.last_timestamp,
                    pre_existing=pre_existing,
                    magnitude=Magnitude(baseline=baseline_count, incident=event.count,
                                        unit="occurrences"),
                    description=(f"Kubernetes reported {event.reason} x{event.count} "
                                 f"({blurb}): {event.message[:200]}{note}"),
                    evidence_ids=[event.id],
                    detail={"reason": event.reason, "type": event.type,
                            "pre_existing": pre_existing,
                            "first_seen_actual": true_onset.isoformat() if true_onset else None},
                ))
        return signals
