"""
The present tense, measured alongside the period asked about.

Every answer used to be about one window: the one in the question. Correct, and
on its own unusable — asked "what caused the 5xx between 09:00 and 10:00?" the
agent explained the 5xx between 09:00 and 10:00, and the reader still could not
tell whether the system was serving traffic at the moment they were reading.

These pin the probe that closes that, and in particular the verdict rules. The
most important of them is the one that is easiest to get backwards: a low error
rate is not health. A crashlooping service serves almost no traffic and so emits
almost no errors, and a check that only counts errors passes while the system is
plainly down.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.models.evidence import LogSnapshot
from app.pipeline.recent import RecentStatusProbe


class FakeLogs:
    """A log tool that returns one prepared snapshot, and records the window."""

    def __init__(self, snapshot: LogSnapshot | Exception) -> None:
        self._snapshot = snapshot
        self.asked_for = None

    async def snapshot(self, _plan, window, **_kwargs):
        self.asked_for = window
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot.model_copy(update={"window": window})


class FakePrometheus:
    """Instant queries only, matched by the metric name in the expression."""

    def __init__(self, unready: list[str] | None = None,
                 restarted: list[str] | None = None,
                 fail: bool = False) -> None:
        self._unready = unready or []
        self._restarted = restarted or []
        self._fail = fail

    async def query(self, expression: str, moment=None):
        if self._fail:
            raise RuntimeError("prometheus is down")
        pods = self._unready if "status_ready" in expression else self._restarted
        return [{"metric": {"pod": pod}, "value": [0, "1"]} for pod in pods]


def snapshot(*, errors: int, total: int = 1000, warnings: int = 0,
             by_service: dict[str, int] | None = None) -> LogSnapshot:
    from app.models.domain import TimeWindow, utcnow
    from datetime import timedelta
    return LogSnapshot(
        window=TimeWindow(start=utcnow() - timedelta(minutes=30), end=utcnow()),
        total_documents=total,
        by_level={"ERROR": errors, "WARN": warnings, "INFO": total - errors - warnings},
        errors_by_service=by_service or {},
    )


@pytest.mark.asyncio
async def test_a_calm_system_reads_healthy_and_says_what_it_measured(plan):
    logs = FakeLogs(snapshot(errors=30, total=5000))       # 1/min over 30 minutes
    status = await RecentStatusProbe(logs, FakePrometheus()).measure(plan)

    assert status.status == "healthy"
    assert status.errors_per_min == pytest.approx(1.0, abs=0.1)
    assert "normal for this system" in status.status_reason
    assert status.unavailable is None


@pytest.mark.asyncio
async def test_a_crashlooping_service_is_not_called_healthy_for_being_quiet(plan):
    """The rule that matters most here.

    A pod that cannot start serves no traffic and logs almost nothing, so the
    error rate is *lower* than normal while the system is broken. Readiness is
    checked precisely because the rate check passes in exactly this case.
    """
    logs = FakeLogs(snapshot(errors=3, total=120))          # 0.1 errors/min
    prometheus = FakePrometheus(unready=["payment-api-7d9f-abc12"])
    status = await RecentStatusProbe(logs, prometheus).measure(plan)

    assert status.status == "critical", "a not-Ready pod outranks a quiet error count"
    assert status.unready_pods == ["payment-api-7d9f-abc12"]
    assert "not Ready" in status.status_reason


@pytest.mark.asyncio
async def test_restarts_inside_the_window_degrade_an_otherwise_calm_system(plan):
    logs = FakeLogs(snapshot(errors=30, total=5000))
    prometheus = FakePrometheus(restarted=["cart-api-1", "cart-api-2"])
    status = await RecentStatusProbe(logs, prometheus).measure(plan)

    assert status.status == "degraded"
    assert status.restarting_pods == ["cart-api-1", "cart-api-2"]


@pytest.mark.asyncio
async def test_a_high_error_rate_is_critical_on_its_own(plan):
    logs = FakeLogs(snapshot(errors=900, total=9000,
                             by_service={"checkout-api": 700, "payment-db": 200}))
    status = await RecentStatusProbe(logs, FakePrometheus()).measure(plan)

    assert status.status == "critical"
    assert status.errors_per_min == pytest.approx(30.0, abs=0.5)
    assert list(status.errors_by_service) == ["checkout-api", "payment-db"], \
        "worst service first, so a reader does not have to sort them"


@pytest.mark.asyncio
async def test_silence_is_reported_as_degraded_rather_than_healthy(plan):
    """No logs at all is not quiet.

    Either nothing is running or nothing is shipping, and both are worse than a
    handful of errors — but an error-count check reads zero as perfect.
    """
    logs = FakeLogs(snapshot(errors=0, total=0))
    status = await RecentStatusProbe(logs, FakePrometheus()).measure(plan)

    assert status.status == "degraded"
    assert "no log lines" in status.status_reason


@pytest.mark.asyncio
async def test_nothing_measurable_is_unknown_rather_than_healthy(plan):
    """The one mistake worth avoiding: reporting a failed probe as a clean bill."""
    logs = FakeLogs(RuntimeError("opensearch refused the query"))
    status = await RecentStatusProbe(logs, None).measure(plan)

    assert status.status == "unknown"
    assert status.unavailable and "opensearch refused" in status.unavailable


@pytest.mark.asyncio
async def test_losing_the_metrics_source_still_reports_the_logs(plan):
    """Half a measurement, clearly labelled, beats no measurement.

    The probe runs on every investigation, so a Prometheus outage must not cost
    the reader the error rate as well.
    """
    logs = FakeLogs(snapshot(errors=600, total=4000))
    status = await RecentStatusProbe(logs, FakePrometheus(fail=True)).measure(plan)

    assert status.errors_per_min == pytest.approx(20.0, abs=0.5)
    assert status.status == "critical"
    assert status.unavailable and "pod state unavailable" in status.unavailable


@pytest.mark.asyncio
async def test_the_window_is_anchored_to_now_not_to_the_question(plan):
    """A question about yesterday afternoon still gets today's status.

    Anchoring to the requested window's end would make this a second copy of the
    analysis rather than an answer to "is it still happening".
    """
    from app.models.domain import utcnow

    logs = FakeLogs(snapshot(errors=30))
    await RecentStatusProbe(logs, None).measure(plan)

    asked = logs.asked_for
    assert abs((asked.end - utcnow()).total_seconds()) < 5
    assert asked.minutes == pytest.approx(settings.recent_status_minutes, abs=0.1)
    assert asked.end > plan.requested_window.end


@pytest.mark.asyncio
async def test_the_window_length_is_configurable(plan, monkeypatch):
    """"Recent" means five minutes while watching a deploy and an hour when
    reading a morning summary, so it is a setting rather than a constant."""
    monkeypatch.setattr(settings, "recent_status_minutes", 5)
    logs = FakeLogs(snapshot(errors=30))
    status = await RecentStatusProbe(logs, None).measure(plan)

    assert status.minutes == 5
    assert logs.asked_for.minutes == pytest.approx(5, abs=0.1)
    # 30 errors over 5 minutes is 6/min, which crosses the degraded bar that the
    # same 30 errors over 30 minutes did not. The rate is what is judged, not
    # the count.
    assert status.status == "degraded"


def test_the_configured_window_is_validated_before_it_takes_effect():
    from app.store.runtime_config import validate

    assert validate("recent_status_minutes", "45") == 45
    with pytest.raises(ValueError):
        validate("recent_status_minutes", "0")
    with pytest.raises(ValueError):
        # Beyond a day this stops being "now" and becomes a second investigation
        # running on every question.
        validate("recent_status_minutes", "2000")


@pytest.mark.asyncio
async def test_a_pod_that_finished_is_not_reported_as_not_ready(plan):
    """The false CRITICAL this exists to prevent.

    `kube_pod_status_ready{condition="true"}` is 0 for every pod that has exited,
    because a finished pod is not Ready — a tautology, not a finding. Measured on
    the live cluster, the unguarded query returned seventeen pods and every one
    was a completed one-shot: Helm install jobs, CronJob runs, hand-run `curl`
    pods, some finished twenty days earlier. The agent reported "14 pods are not
    Ready right now" and called a healthy system CRITICAL.

    The exclusion has to be in the query rather than in a post-filter on names:
    the probe only ever sees what Prometheus returns, so a pod dropped here is
    one it never had to guess about.
    """
    asked = []

    class Recording:
        async def query(self, expression, moment=None):
            asked.append(expression)
            return []

    await RecentStatusProbe(FakeLogs(snapshot(errors=30)), Recording()).measure(plan)

    readiness = next(e for e in asked if "kube_pod_status_ready" in e)
    assert "unless" in readiness, "terminal pods must be excluded in PromQL"
    assert 'phase=~"Succeeded|Failed"' in readiness, (
        "both terminal phases: Kubernetes has stopped trying to run either, so "
        "neither one's readiness says anything about serving traffic")


@pytest.mark.asyncio
async def test_a_genuinely_unready_pod_is_still_caught(plan):
    """The other half of "without trade-off".

    A crashlooping pod stays in the Running phase and reports not Ready, so the
    exclusion above never reaches it — and this is the case the whole check
    exists for, because such a pod serves no traffic while emitting almost no
    errors.
    """
    logs = FakeLogs(snapshot(errors=3, total=120))          # 0.1 errors/min: quiet
    prometheus = FakePrometheus(unready=["checkout-api-7d9f-abc12"])
    status = await RecentStatusProbe(logs, prometheus).measure(plan)

    assert status.unready_pods == ["checkout-api-7d9f-abc12"]
    assert status.status == "critical"
