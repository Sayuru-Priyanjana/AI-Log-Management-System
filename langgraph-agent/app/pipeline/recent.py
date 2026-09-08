"""
What the system is doing *now*, alongside what it was doing in the window asked
about.

Every answer used to be about one period: the one in the question. That is the
right thing to analyse and, on its own, an unusable thing to read. Asked "what
was the root cause of the 5xx between 09:00 and 10:00?", the agent explained the
5xx between 09:00 and 10:00 — and the reader still could not tell whether the
system was serving traffic at that moment or still down. The two questions people
actually ask together, "what happened" and "is it still happening", were being
answered one at a time.

So a fixed recent window is measured on every root-cause and health-check run,
whatever period was asked about, and both are reported. The length is
configurable (`recent_status_minutes`, editable from the configuration page)
because "recent" means five minutes to someone watching a deploy and an hour to
someone reading a morning summary.

Three properties keep this cheap enough to do unconditionally:

* it is deterministic — one log aggregation and two instant metric queries, no
  model call, so it costs no tokens and adds no reasoning steps;
* it never fails the run. Every source here is optional, and a probe that cannot
  reach Prometheus reports which half it managed rather than taking the
  investigation down with it;
* the verdict comes from the same rules the signal engine uses, so "healthy" here
  and "no signals" there cannot disagree.

The verdict is deliberately not "few errors means healthy". A crashlooping
service serves almost no traffic and therefore emits almost no errors: the rate
check passes while the system is plainly broken. Pod readiness is checked too,
and an unready pod outranks a quiet error count.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.models.analysis import RecentStatus
from app.models.domain import TimeWindow, utcnow
from app.models.plan import InvestigationPlan
from app.sources.prometheus import label_selector
from app.tools.logs import LogTool

logger = logging.getLogger(__name__)

# Errors per minute at which the recent window stops looking quiet. A healthy
# shopdemo emits roughly 4 errors/min all by itself — each service has a base
# error rate and it compounds across three tiers — so any "quiet" bar has to
# clear that floor or every run reports a degraded system.
DEGRADED_ERRORS_PER_MIN = 6.0
CRITICAL_ERRORS_PER_MIN = 20.0


class RecentStatusProbe:
    """Measures the last N minutes, independently of the investigated window."""

    def __init__(self, log_tool: LogTool, prometheus=None) -> None:
        self._logs = log_tool
        self._prometheus = prometheus

    async def measure(self, plan: InvestigationPlan, *,
                      minutes: int | None = None) -> RecentStatus:
        minutes = int(minutes or settings.recent_status_minutes)
        # Anchored to now rather than to the end of the requested window: the
        # whole point is to say what is true at the moment the answer is read. A
        # question about yesterday afternoon still gets today's status.
        window = TimeWindow.last(f"{minutes}m", now=utcnow(), label="recent")
        status = RecentStatus(window=window, minutes=minutes)

        snapshot, workloads = await asyncio.gather(
            self._logs.snapshot(plan, window,
                                top_services=settings.episode_top_services,
                                top_messages=settings.episode_top_errors),
            self._workloads(plan),
            return_exceptions=True,
        )

        gaps: list[str] = []

        if isinstance(snapshot, BaseException) or getattr(snapshot, "status", "") != "ok":
            reason = (str(snapshot) if isinstance(snapshot, BaseException)
                      else snapshot.reason or "the log query did not return")
            gaps.append(f"logs unavailable ({reason})")
        else:
            status.total_documents = snapshot.total_documents
            status.errors = snapshot.errors
            status.warnings = snapshot.warnings
            status.errors_per_min = round(snapshot.errors / max(window.minutes, 1.0), 2)
            status.errors_by_service = dict(
                sorted(snapshot.errors_by_service.items(),
                       key=lambda item: -item[1])
            )
            status.top_errors = snapshot.top_errors[:settings.episode_top_errors * 2]

        if isinstance(workloads, BaseException):
            gaps.append(f"pod state unavailable ({workloads})")
        elif workloads is None:
            gaps.append("no metrics source is configured, so pod readiness was not checked")
        else:
            status.unready_pods, status.restarting_pods = workloads

        status.unavailable = "; ".join(gaps) or None
        self._verdict(status, logs_seen=not any(g.startswith("logs") for g in gaps))
        return status

    # ------------------------------------------------------------- workloads
    async def _workloads(self, plan: InvestigationPlan) -> tuple[list[str], list[str]] | None:
        """Pods not Ready now, and pods that restarted inside the recent window.

        Both are instant queries rather than ranges: "is it ready" is a question
        about this moment, and `changes()` over the window collapses the restart
        history to one number per pod without pulling back a series.

        **Terminal pods are excluded, and this is a correction rather than a
        filter.** `kube_pod_status_ready{condition="true"}` is 0 for every pod
        that has finished, because a pod that has exited is not Ready — which is
        a tautology, not a finding. Without the exclusion a cluster reported 17
        pods "not Ready right now" and was called CRITICAL while every workload
        was healthy: all seventeen were completed one-shot pods — Helm install
        jobs, CronJob runs, a handful of hand-run `curl` pods — some of them
        finished twenty days earlier. The reader was told the system was down
        because a `curl` had succeeded three weeks ago.

        Nothing real is lost by dropping them. A pod in Succeeded or Failed is
        one Kubernetes has stopped trying to run, so its readiness says nothing
        about whether traffic is being served. Everything that does is still
        caught: a crashlooping pod stays in Running and reports not Ready, an
        unschedulable one stays in Pending and reports not Ready, restarts are
        counted separately below, and the signal engine covers the investigated
        window with its own CRASHLOOP, OOM_KILL and READINESS_FAILURE detection —
        which has excluded succeeded pods from the same metric all along. This
        probe was simply written without that knowledge.
        """
        if self._prometheus is None:
            return None

        selector = label_selector(namespace=plan.namespaces or None)
        scope = f"{{{selector}}}" if selector else ""
        window = f"{max(int(settings.recent_status_minutes), 1)}m"
        comma = "," if selector else ""

        unready_expr = (
            f'max by (pod) (kube_pod_status_ready{{{selector}{comma}condition="true"}}) == 0'
            f' unless on (pod) '
            f'max by (pod) (kube_pod_status_phase{{{selector}{comma}'
            f'phase=~"Succeeded|Failed"}}) == 1'
        )
        restart_expr = (f"max by (pod) (changes("
                        f"kube_pod_container_status_restarts_total{scope}[{window}])) > 0")

        unready_raw, restart_raw = await asyncio.gather(
            self._prometheus.query(unready_expr),
            self._prometheus.query(restart_expr),
            return_exceptions=True,
        )

        def pods(raw) -> list[str]:
            if isinstance(raw, BaseException):
                logger.debug("Recent workload query failed: %s", raw)
                return []
            return sorted({item.get("metric", {}).get("pod", "")
                           for item in raw if item.get("metric", {}).get("pod")})

        if isinstance(unready_raw, BaseException) and isinstance(restart_raw, BaseException):
            raise unready_raw
        return pods(unready_raw), pods(restart_raw)

    # --------------------------------------------------------------- verdict
    @staticmethod
    def _verdict(status: RecentStatus, *, logs_seen: bool) -> None:
        """Healthy / degraded / critical, and the reason in one sentence.

        Ordered so the strongest evidence wins. An unready pod is a fact about
        the workload; an error rate is a fact about what the workload had time to
        say before it died, which is why a crashloop can look calm.
        """
        reasons: list[str] = []
        level = "healthy"

        if status.unready_pods:
            level = "critical"
            reasons.append(f"{len(status.unready_pods)} pod(s) are not Ready right now "
                           f"({', '.join(status.unready_pods[:3])})")
        if status.restarting_pods:
            level = "critical" if level == "critical" else "degraded"
            reasons.append(f"{len(status.restarting_pods)} pod(s) restarted in the last "
                           f"{status.minutes} minutes")

        if logs_seen:
            if status.errors_per_min >= CRITICAL_ERRORS_PER_MIN:
                level = "critical"
                reasons.append(f"errors are running at {status.errors_per_min:.1f}/min")
            elif status.errors_per_min >= DEGRADED_ERRORS_PER_MIN:
                if level == "healthy":
                    level = "degraded"
                reasons.append(f"errors are running at {status.errors_per_min:.1f}/min")
            elif status.total_documents == 0:
                # No logs at all is not quiet. Either nothing is running or
                # nothing is shipping, and both are worse than a few errors.
                level = "degraded" if level == "healthy" else level
                reasons.append("no log lines arrived at all in this period")
            else:
                reasons.append(f"errors are running at {status.errors_per_min:.1f}/min, "
                               f"which is normal for this system")
        elif level == "healthy":
            # Nothing was measurable. Reporting that as healthy would be the one
            # mistake worth avoiding here.
            level = "unknown"
            reasons.append("nothing could be measured")

        status.status = level
        status.status_reason = "; ".join(reasons)
        status.summary = (
            f"In the last {status.minutes} minutes the system is {level.upper()}: "
            f"{status.status_reason}."
        )
