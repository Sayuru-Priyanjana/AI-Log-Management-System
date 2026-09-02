"""
State gauges must not manufacture onsets at the window edge.

Four of the metrics behind signals are not events. `pod_oom_terminated` reads
kube_pod_container_status_last_terminated_reason, which stays at 1 for as long as
the pod's most recent termination was an OOM — hours or days after the kill.
`pod_ready`, `pod_pending` and `target_up` have the same shape.

Asking when such a series "first crossed" inside the window returns the window's
own start whenever the condition predates it. That is the most damaging error
available here, because the hypothesis engine ranks candidates on causal
precedence: an artefact pinned to the window edge outranks every real signal.

Observed in production on a 28-hour investigation opening at 00:05 — OOM_KILL was
reported with onset 00:05:00, the loop took it as the earliest event, named it the
root cause, and invented a dependency to explain how it caused a service that does
not call it. MEMORY_PRESSURE on the same pod was reported at 02:25, after the kill
it supposedly caused: the ordering that gives the artefact away.
"""
from __future__ import annotations

import pytest

from app.models.analysis import InvestigationWindows
from app.models.domain import TimeWindow
from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, MetricEvidence
from app.models.signals import SignalType
from app.pipeline.hypotheses import HypothesisEngine
from app.pipeline.signals import SignalEngine, _state_onset
from tests.conftest import T0, at, series


def windows() -> InvestigationWindows:
    return InvestigationWindows(
        requested=TimeWindow(start=T0, end=at(1800)),
        incident=TimeWindow(start=T0, end=at(1800), label="incident"),
        baseline=TimeWindow(start=T0, end=at(600), label="baseline"),
        onset=T0, onset_detected=True, method="test",
    )


def detect(metrics):
    bundle = EvidenceBundle(logs=LogEvidence(), events=EventEvidence(), metrics=metrics)
    return SignalEngine(known_services=["prometheus"]).detect(None, windows(), bundle)


def find(signals, kind):
    return next((s for s in signals if s.type is kind), None)


# --------------------------------------------------------------- the helper
def test_a_condition_true_from_the_first_sample_is_pre_existing():
    s = series("pod_oom_terminated", [1, 1, 1], labels={"pod": "p"}, unit="bool")
    onset, pre_existing = _state_onset(s, lambda v: v >= 1)
    assert pre_existing is True
    assert onset == s.points[0].timestamp


def test_a_condition_that_becomes_true_inside_the_window_is_not():
    s = series("pod_oom_terminated", [0, 0, 1, 1], labels={"pod": "p"}, unit="bool")
    onset, pre_existing = _state_onset(s, lambda v: v >= 1)
    assert pre_existing is False
    assert onset == s.points[2].timestamp, "onset is the transition, not the window start"


def test_an_empty_series_yields_nothing_rather_than_guessing():
    from app.models.evidence import MetricSeries
    s = MetricSeries(id="met:pod_oom_terminated:p", metric="pod_oom_terminated",
                     unit="bool", labels={"pod": "p"}, points=[])
    assert _state_onset(s, lambda v: v >= 1) == (None, False)


# ------------------------------------------------------------ per signal type
@pytest.mark.parametrize("metric,kind,values", [
    ("pod_oom_terminated", SignalType.OOM_KILL, [1, 1, 1]),
    ("pod_ready", SignalType.READINESS_FAILURE, [0, 0, 0]),
    ("pod_pending", SignalType.SCHEDULING_FAILURE, [1, 1, 1]),
    ("target_up", SignalType.DEPENDENCY_UNAVAILABLE, [0, 0, 0]),
])
def test_a_state_already_true_at_the_window_edge_is_flagged(metric, kind, values):
    metrics = MetricEvidence(series=[
        series(metric, values, labels={"pod": "prometheus-79946bc97f-mp7sv",
                                       "app": "prometheus"}, unit="bool"),
    ])
    signal = find(detect(metrics), kind)
    assert signal is not None, f"{kind.value} should still be reported"
    assert signal.pre_existing is True, \
        "a condition true from the first sample did not start inside the window"


@pytest.mark.parametrize("metric,kind,values", [
    ("pod_oom_terminated", SignalType.OOM_KILL, [0, 0, 1, 1]),
    ("pod_ready", SignalType.READINESS_FAILURE, [1, 1, 0, 0]),
    ("pod_pending", SignalType.SCHEDULING_FAILURE, [0, 0, 1, 1]),
    ("target_up", SignalType.DEPENDENCY_UNAVAILABLE, [1, 1, 0, 0]),
])
def test_a_state_that_changes_inside_the_window_keeps_its_real_onset(metric, kind, values):
    """The fix must not blunt genuine detections — a pod that was healthy and then
    was not is exactly what this system exists to catch."""
    s = series(metric, values, labels={"pod": "prometheus-79946bc97f-mp7sv",
                                       "app": "prometheus"}, unit="bool")
    signal = find(detect(MetricEvidence(series=[s])), kind)
    assert signal is not None
    assert signal.pre_existing is False
    assert signal.first_seen == s.points[2].timestamp


def test_the_oom_description_warns_that_the_timestamp_is_not_the_kill_time():
    """The model reads the description. Left unqualified it reads "OOM_KILL at
    00:05" as an event, which is what produced the wrong answer."""
    metrics = MetricEvidence(series=[
        series("pod_oom_terminated", [1, 1, 1],
               labels={"pod": "prometheus-79946bc97f-mp7sv"}, unit="bool"),
    ])
    oom = find(detect(metrics), SignalType.OOM_KILL)
    assert "not observed" in oom.description
    assert "Do not treat this timestamp as the onset" in oom.description


def test_a_pre_existing_state_is_not_reported_at_full_severity():
    """Severity drives ranking and reader attention. A condition whose start was
    never seen is weaker evidence than one caught happening."""
    live = find(detect(MetricEvidence(series=[
        series("pod_oom_terminated", [0, 0, 1], labels={"pod": "p"}, unit="bool")])),
        SignalType.OOM_KILL)
    stale = find(detect(MetricEvidence(series=[
        series("pod_oom_terminated", [1, 1, 1], labels={"pod": "p"}, unit="bool")])),
        SignalType.OOM_KILL)
    assert stale.severity.rank < live.severity.rank


# ------------------------------------------------------- the consequence
def test_a_pre_existing_state_cannot_win_causal_precedence():
    """The whole point. `_of()` in the hypothesis engine excludes pre-existing
    signals, so flagging them is what stops a window-edge artefact outranking a
    real failure purely for appearing to be the oldest thing in the run."""
    metrics = MetricEvidence(series=[
        # Already OOM-flagged at the window edge — the artefact.
        series("pod_oom_terminated", [1, 1, 1, 1],
               labels={"pod": "prometheus-79946bc97f-mp7sv"}, unit="bool"),
        # A genuine failure that starts later, inside the window.
        series("target_up", [1, 1, 0, 0],
               labels={"app": "doe-result-service", "pod": "doe-result-service-x"},
               unit="bool"),
    ])
    signals = detect(metrics)
    candidates = HypothesisEngine().generate(None, windows(), signals,
                                             EvidenceBundle(logs=LogEvidence(),
                                                            events=EventEvidence(),
                                                            metrics=metrics))
    top = candidates[0]
    assert top.service != "prometheus", (
        "a pre-existing state pinned to the window edge must not be ranked the "
        "root cause over a failure actually observed starting"
    )
