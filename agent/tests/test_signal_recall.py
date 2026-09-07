"""
Recall bugs in the detection layer.

Signal recall is the leading indicator for this whole system: if the engine never
detects a condition, no amount of prompt or agent work can make the answer name
it. Each test here pins a specific way detection was losing real conditions —
every one of them silent, producing an absent signal that read as an absent
problem rather than as a failure to look.
"""
from __future__ import annotations

import pytest

from app.models.analysis import InvestigationWindows
from app.models.domain import TimeWindow
from app.models.evidence import (
    EventEvidence,
    EvidenceBundle,
    LogEvidence,
    LogPattern,
    MetricEvidence,
)
from app.pipeline.signals import SignalEngine
from app.models.signals import SignalType
from tests.conftest import T0, at, series


def windows_with_baseline() -> InvestigationWindows:
    """A 10-minute incident against a 10-minute baseline, so rates are per-minute
    numbers that are easy to reason about."""
    return InvestigationWindows(
        requested=TimeWindow(start=T0, end=at(1200)),
        incident=TimeWindow(start=at(600), end=at(1200), label="incident"),
        baseline=TimeWindow(start=T0, end=at(600), label="baseline"),
        onset=at(600), onset_detected=True, method="test",
    )


def detect(logs=None, metrics=None, services=None):
    bundle = EvidenceBundle(logs=logs or LogEvidence(), events=EventEvidence(),
                            metrics=metrics or MetricEvidence())
    return SignalEngine(known_services=services or []).detect(
        None, windows_with_baseline(), bundle)


def kinds(signals) -> set[str]:
    return {s.type.value for s in signals}


# ---------------------------------------------------------------------------
# 1. Error rate was measured from a truncated list
# ---------------------------------------------------------------------------
def test_the_error_rate_uses_exact_counts_not_the_displayed_patterns():
    """`patterns` is ranked and capped at max_log_patterns for display. Summing
    it to get a rate undercounts any service with more distinct error templates
    than the cap allows: here 3 shown patterns carry 30 errors while the service
    actually logged 600, and only the true figure crosses the threshold."""
    logs = LogEvidence(
        patterns=[
            LogPattern(id=f"pat:payment-api:{i}", template=f"t{i}", example=f"e{i}",
                       level="ERROR", service="payment-api", count=10,
                       baseline_count=1, first_seen=at(660))
            for i in range(3)
        ],
        # What the index actually holds: 600 errors in the incident, 6 before.
        by_service_level={"payment-api": {"ERROR": 600, "INFO": 40}},
        baseline_by_service_level={"payment-api": {"ERROR": 6, "INFO": 900}},
    )
    signals = detect(logs=logs)

    spikes = [s for s in signals if s.type is SignalType.ERROR_RATE_SPIKE]
    assert spikes, "600 errors against a baseline of 6 must fire a spike"
    assert spikes[0].magnitude.incident == pytest.approx(60.0), \
        "rate should come from 600 errors over 10 minutes, not the 30 on display"


def test_a_service_below_threshold_still_does_not_fire():
    """The fix must not turn the threshold off."""
    logs = LogEvidence(
        patterns=[],
        by_service_level={"payment-api": {"ERROR": 100}},
        baseline_by_service_level={"payment-api": {"ERROR": 90}},
    )
    assert not [s for s in detect(logs=logs)
                if s.type is SignalType.ERROR_RATE_SPIKE]


def test_stored_evidence_without_the_aggregation_still_works():
    """Investigations stored before this aggregation existed are replayed through
    the same engine. Losing detection on them would break the history."""
    logs = LogEvidence(patterns=[
        LogPattern(id="pat:a", template="t", example="e", level="ERROR",
                   service="payment-api", count=600, baseline_count=6,
                   first_seen=at(660)),
    ])
    assert SignalType.ERROR_RATE_SPIKE in {s.type for s in detect(logs=logs)}


# ---------------------------------------------------------------------------
# 2. is_new fired on patterns the baseline aggregation simply did not return
# ---------------------------------------------------------------------------
def test_an_unverified_absence_is_not_reported_as_a_new_error():
    """The baseline's message aggregation is capped per service. A pattern that
    ranked below the cut comes back with no bucket and count 0 — identical, from
    here, to one that genuinely never occurred. Announcing it as brand new fires
    a HIGH-severity signal on a line that may have run all day."""
    logs = LogEvidence(patterns=[
        LogPattern(id="pat:payment-api:x", template="t", example="e",
                   level="ERROR", service="payment-api", count=50,
                   baseline_count=0, baseline_verified=False, first_seen=at(660)),
    ])
    assert SignalType.NEW_ERROR_PATTERN not in kinds(detect(logs=logs))


def test_a_verified_absence_still_reports_a_new_error():
    """The signal must survive the fix — a genuinely new error is the single
    highest-value cheap finding in log analysis."""
    logs = LogEvidence(patterns=[
        LogPattern(id="pat:payment-api:x", template="t", example="e",
                   level="ERROR", service="payment-api", count=50,
                   baseline_count=0, baseline_verified=True, first_seen=at(660)),
    ])
    assert SignalType.NEW_ERROR_PATTERN in kinds(detect(logs=logs))


def test_is_new_is_false_when_the_baseline_was_never_established():
    pattern = LogPattern(id="p", template="t", example="e", level="ERROR",
                         service="s", count=10, baseline_count=0,
                         baseline_verified=False)
    assert pattern.is_new is False
    pattern.baseline_verified = True
    assert pattern.is_new is True


# ---------------------------------------------------------------------------
# 3. TRAFFIC_SURGE averaged a surge away
# ---------------------------------------------------------------------------
def test_a_surge_occupying_part_of_the_window_is_detected():
    """The window deliberately starts before the onset, so a window average
    mixes quiet minutes with busy ones. Measured live, a 6.8x surge averaged out
    to 1.97x and sat under the 2.5x bar while the dashboard showed it plainly.

    These numbers are that case: quiet at 4 req/s with the surge to 27 occupying
    the tail of the window. The window mean is 7.3 — a 1.8x ratio, under the 2.5x
    bar, so the old formula detected nothing — while the peak is 6.8x. The
    neighbouring 5xx and dependency checks already use the peak for exactly this
    reason; traffic was the one that never got the fix.
    """
    metrics = MetricEvidence(series=[
        series("http_request_rate", [4, 4, 4, 4, 4, 4, 27],
               labels={"service": "checkout-api"}, baseline=[4, 4, 4], unit="req/s"),
    ])
    signals = detect(metrics=metrics)
    assert SignalType.TRAFFIC_SURGE in kinds(signals)

    surge = next(s for s in signals if s.type is SignalType.TRAFFIC_SURGE)
    assert surge.magnitude.ratio == pytest.approx(6.75, abs=0.1), \
        "the reported multiple should be the peak, not the diluted window mean"


def test_ordinary_variation_is_not_a_surge():
    metrics = MetricEvidence(series=[
        series("http_request_rate", [4, 5, 4, 6, 5],
               labels={"service": "checkout-api"}, baseline=[4, 5, 4], unit="req/s"),
    ])
    assert SignalType.TRAFFIC_SURGE not in kinds(detect(metrics=metrics))


def test_a_collapse_is_still_judged_on_the_average():
    """Traffic stopping is a sustained state. Judging it on the peak would let a
    single busy sample at the start of the window hide a service that then went
    silent for the rest of it."""
    metrics = MetricEvidence(series=[
        series("http_request_rate", [10, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
               labels={"service": "checkout-api"}, baseline=[10, 10, 10], unit="req/s"),
    ])
    assert SignalType.TRAFFIC_COLLAPSE in kinds(detect(metrics=metrics))


def test_the_surge_magnitude_and_ratio_agree():
    """A peak-derived multiple printed beside a window average gives the reader
    two numbers that do not divide into each other."""
    metrics = MetricEvidence(series=[
        series("http_request_rate", [4, 4, 4, 4, 27, 27, 27],
               labels={"service": "checkout-api"}, baseline=[4, 4, 4], unit="req/s"),
    ])
    surge = next(s for s in detect(metrics=metrics)
                 if s.type is SignalType.TRAFFIC_SURGE)
    assert surge.magnitude.incident == pytest.approx(27.0)
    assert surge.magnitude.incident / surge.magnitude.baseline == \
        pytest.approx(surge.magnitude.ratio)


# ---------------------------------------------------------------------------
# 4. A failed metric join disappeared without trace
# ---------------------------------------------------------------------------
def test_an_unpairable_ratio_is_logged_rather_than_vanishing(caplog):
    """A ratio needs both halves and the join is exact. When an exporter labels
    the two sides differently every pair is lost, the signal never fires, and
    nothing anywhere records that it could not be computed — an absent signal
    reading as an absent problem."""
    metrics = MetricEvidence(series=[
        series("http_error_rate", [5, 5, 5], labels={"service": "payment-api"},
               baseline=[0, 0, 0], unit="req/s"),
        # Same metric, but labelled by pod rather than service: no join key.
        series("http_request_rate", [10, 10, 10], labels={"pod": "payment-api-abc12"},
               baseline=[10, 10, 10], unit="req/s"),
    ])
    with caplog.at_level("WARNING"):
        signals = detect(metrics=metrics)

    assert SignalType.HTTP_5XX_BURST not in kinds(signals)
    warned = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("HTTP_5XX_BURST" in m for m in warned), \
        "a ratio that could not be computed must not pass silently"
    assert any("will not fire" in m for m in warned), \
        "the log must say the signal is suppressed, not merely that a join failed"


def test_a_correctly_labelled_pair_still_fires():
    metrics = MetricEvidence(series=[
        series("http_error_rate", [5, 5, 5], labels={"service": "payment-api"},
               baseline=[0, 0, 0], unit="req/s"),
        series("http_request_rate", [10, 10, 10], labels={"service": "payment-api"},
               baseline=[10, 10, 10], unit="req/s"),
    ])
    assert SignalType.HTTP_5XX_BURST in kinds(detect(metrics=metrics))
