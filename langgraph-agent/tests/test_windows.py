from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.pipeline.windows import detect_onset
from tests.conftest import buckets


def test_quiet_window_has_no_onset():
    result = detect_onset(buckets([0, 0, 0, 0, 0, 0, 0, 0]))
    assert result.detected is False


def test_single_stray_error_is_not_an_incident():
    # One blip in an otherwise silent window must not become an "incident";
    # the sustain requirement is what prevents that.
    result = detect_onset(buckets([0, 0, 0, 9, 0, 0, 0, 0]))
    assert result.detected is False


def test_sustained_burst_is_detected_at_its_start():
    counts = [0, 0, 1, 0, 1, 40, 45, 38, 41, 44]
    result = detect_onset(counts and buckets(counts))
    assert result.detected is True
    assert result.index == 5, "onset should be the first elevated bucket, not the peak"


def test_low_volume_noise_stays_below_the_absolute_floor():
    # 1-2 errors a minute is not an incident even though it is above the median.
    result = detect_onset(buckets([0, 0, 1, 0, 2, 1, 2, 1, 2, 1]), min_absolute=3)
    assert result.detected is False


def test_incident_already_running_is_flagged_rather_than_mislocated():
    result = detect_onset(buckets([50, 52, 48, 51, 49, 50]))
    assert result.detected is True
    assert result.before_window is True, (
        "elevated from the first bucket means the onset is outside the range examined"
    )


def test_gradual_ramp_is_caught_before_the_peak():
    result = detect_onset(buckets([1, 1, 1, 1, 1, 12, 20, 33, 40, 45]))
    assert result.detected is True
    assert result.index == 5


@pytest.mark.asyncio
async def test_an_already_broken_system_still_gets_a_comparison_window():
    """Regression from a live run. Errors had been elevated for hours, so no
    quiet baseline existed and the resolver returned none — which silently
    disabled every baseline-relative signal. A 12x traffic surge went undetected
    and the evidence timeline showed no metrics at all.

    An imperfect comparison window still catches a change that happened *inside*
    the range; if both stretches are equally bad the ratios sit near 1 and
    nothing fires, which is the correct answer.
    """
    from datetime import timedelta

    from app.models.domain import TimeWindow
    from app.models.plan import Intent, InvestigationPlan
    from app.pipeline.windows import WindowResolver

    class AlwaysElevated:
        async def histogram(self, plan, window, interval="60s"):
            # every bucket busy: no quiet stretch anywhere in range
            return buckets([40] * 60)

    now = datetime(2026, 8, 12, 5, 0, tzinfo=timezone.utc)
    plan = InvestigationPlan(
        intent=Intent.INCIDENT_INVESTIGATION, system_id="shopdemo",
        system_name="S", environment="staging", namespaces=["shopdemo"],
        requested_window=TimeWindow(start=now - timedelta(hours=1), end=now),
        tools=["logs"], goal="what is wrong",
    )

    windows, _ = await WindowResolver(AlwaysElevated()).resolve(plan)

    assert windows.onset_before_window is True
    assert windows.baseline is not None, "a degraded baseline beats none at all"
    assert windows.baseline_quality == "degraded"
    assert windows.baseline.end <= windows.incident.start, "must not overlap"
    assert "may itself have been unhealthy" in windows.method


def test_a_latency_step_is_found_where_an_error_histogram_sees_nothing():
    """The failure this exists to prevent, observed on the live testbed.

    payment-db was given a 1.5s delay. It answered every request successfully, so
    the error histogram was flat and onset detection anchored to an unrelated
    error blip 80 minutes earlier. The window came out 62 minutes long for a
    3-minute-old incident, the baseline landed inside it, and the slowdown was
    diluted to invisibility. The agent reported "root cause not identified".
    """
    from datetime import timedelta

    from app.pipeline.windows import detect_step_change

    start = datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc)
    points = ([(start + timedelta(minutes=i), 0.015) for i in range(20)]
              + [(start + timedelta(minutes=20 + i), 1.52) for i in range(10)])

    onset, before, after = detect_step_change(points, multiplier=2.0, floor=0.25)

    assert onset == start + timedelta(minutes=20)
    assert before == pytest.approx(0.015, abs=0.005)
    assert after == pytest.approx(1.52, abs=0.01)


def test_latency_noise_is_not_mistaken_for_a_step():
    from datetime import timedelta

    from app.pipeline.windows import detect_step_change

    start = datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc)
    # occasional spikes on a flat floor — the shape a shared host produces
    values = [0.02, 0.02, 2.4, 0.02, 0.03, 0.02, 2.4, 0.02, 0.02, 0.03,
              0.02, 0.02, 0.02, 2.4, 0.02, 0.02, 0.03, 0.02, 0.02, 0.02]
    points = [(start + timedelta(minutes=i), v) for i, v in enumerate(values)]

    onset, _, _ = detect_step_change(points, multiplier=2.0, floor=0.25)
    assert onset is None, "median-based comparison should ignore isolated spikes"


def test_a_step_below_the_floor_is_ignored():
    from datetime import timedelta

    from app.pipeline.windows import detect_step_change

    start = datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc)
    # 5ms to 15ms is a 3x rise, but nobody cares and it is not an incident
    points = ([(start + timedelta(minutes=i), 0.005) for i in range(10)]
              + [(start + timedelta(minutes=10 + i), 0.015) for i in range(10)])

    onset, _, _ = detect_step_change(points, multiplier=2.0, floor=0.25)
    assert onset is None


def test_empty_input_is_handled():
    result = detect_onset([])
    assert result.detected is False
    assert result.reason == "no_buckets"


def test_ordinary_poisson_noise_is_not_an_incident():
    """Regression: this exact series came off the running testbed while nothing
    was wrong, and the original MAD-only threshold declared a 96-minute incident.

    A service sitting at ~3 errors/min has a MAD near 1, which put the threshold
    at 7 — a level routine count noise crosses for four minutes at a stretch.
    Using sqrt(median) as a floor for the spread keeps it below the line.
    """
    counts = [2, 4, 6, 4, 3, 3, 0, 4, 6, 15, 4, 7, 2, 6, 6, 2, 0, 13, 5, 5, 13, 5, 4, 5,
              5, 6, 11, 7, 8, 7, 8, 3, 4, 1, 0, 8, 5, 6, 8, 9, 6, 1, 5, 6, 0, 0, 6, 11,
              9, 8, 12, 4, 1, 2, 2, 11, 14, 5, 1, 4, 4, 4, 2, 6, 5, 9, 3, 3, 4, 3, 6, 9]
    result = detect_onset(buckets(counts))
    assert result.detected is False, (
        f"noise at ~3/min should not be an incident (threshold was {result.threshold})"
    )


def test_a_real_incident_still_stands_out_above_the_same_noise():
    """The counterpart to the test above: the sqrt floor must not blind it to a
    genuine failure. A dependency outage takes error rates to tens per minute."""
    counts = [3, 5, 2, 4, 6, 3, 4, 5, 3, 2, 4, 3,
              68, 74, 71, 80, 77, 69, 73, 75]
    result = detect_onset(buckets(counts))
    assert result.detected is True
    assert result.index == 12


def test_an_early_blip_does_not_mask_a_real_failure_later():
    """Regression, caught against the live testbed while payment-db was scaled
    to zero: an early noisy crossing was rejected as a blip and the scan then
    gave up, so the actual outage — 101 errors in the final bucket — was never
    even considered. Rejecting a candidate has to mean 'keep looking', not stop.
    """
    counts = [0, 2, 2, 6, 1, 7, 8, 3, 11, 8, 3, 0, 1, 2, 4, 8, 7, 3, 7, 4, 4, 2,
              4, 8, 4, 0, 5, 3, 1, 1, 3, 4, 3, 4, 4, 3, 4, 5, 3, 7, 2, 2, 2, 6,
              3, 0, 8, 9, 2, 4, 6, 8, 5, 1, 2, 10, 10, 5, 0, 4, 101]
    result = detect_onset(buckets(counts))
    assert result.detected is True
    assert result.index == 60, "should skip the early blip and find the real onset"


def test_the_current_episode_wins_over_an_older_resolved_one():
    """Two incidents in range: one that came and went, and one happening now.

    Scanning forward returns the oldest departure anywhere in the range, which
    after a previous incident has resolved is the wrong one — and it drags the
    incident window across both, so every signal gets measured against a
    baseline containing the earlier failure. Someone asking "what is wrong now"
    should get the episode still in progress.
    """
    counts = ([3, 4, 2, 3] + [70, 65, 72, 68]          # older incident, resolved
              + [3, 2, 4, 3, 2, 3, 4, 2]                # quiet again
              + [80, 85, 79, 88, 82])                   # the one happening now
    result = detect_onset(buckets(counts))
    assert result.detected is True
    assert result.index == 16, "should locate the start of the current episode"


def test_a_dip_inside_an_incident_does_not_split_it():
    # Incidents fluctuate; a brief dip must not be read as the incident ending
    # and a new one starting, which would truncate the window.
    counts = [3, 4, 2, 3, 2, 3] + [70, 65, 8, 72, 68, 74]
    result = detect_onset(buckets(counts))
    assert result.detected is True
    assert result.index == 6, "the dip at index 8 should not become the onset"


def test_a_blip_with_nothing_after_it_is_still_rejected():
    # Same shape as above but the tail never actually fails: no onset at all.
    counts = [0, 2, 2, 6, 1, 7, 8, 3, 11, 8, 3, 0, 1, 2, 4, 8, 7, 3, 7, 4, 4, 2,
              4, 8, 4, 0, 5, 3, 1, 1, 3, 4, 3, 4, 4, 3, 4, 5, 3, 7, 2, 2, 2, 6,
              3, 0, 8, 9, 2, 4, 6, 8, 5, 1, 2, 3, 4, 5, 0, 4, 2]
    result = detect_onset(buckets(counts))
    assert result.detected is False
    assert "blip" in result.reason


@pytest.mark.asyncio
async def test_the_baseline_ends_at_the_onset_not_at_the_edge_of_the_question():
    """Measured on the live testbed. A traffic surge began at 05:12; the question
    asked about 05:22 onwards. The window analysed was clamped to the question —
    correctly — but the baseline was then taken as "the stretch before the window
    analysed", which put eight surging minutes inside it. Request rate read 26.2
    against a baseline of 11.4: a 2.3x ratio, just under the 2.5x bar, so
    TRAFFIC_SURGE never fired on a surge plainly visible in the dashboard.

    What to examine and what to compare it against are two different questions.
    """
    from datetime import timedelta

    from app.models.domain import TimeWindow
    from app.models.plan import Intent, InvestigationPlan
    from app.pipeline.windows import WindowResolver

    now = datetime(2026, 8, 12, 5, 42, tzinfo=timezone.utc)
    requested = TimeWindow(start=now - timedelta(minutes=20), end=now)

    class SurgeAt0512:
        """Quiet until 05:12, elevated from then on. The search looks back
        further than the question, so the onset is visible but outside it."""

        async def histogram(self, plan, window, interval="60s"):
            from app.models.evidence import LogBucket

            onset_at = datetime(2026, 8, 12, 5, 12, tzinfo=timezone.utc)
            minutes = int((window.end - window.start).total_seconds() // 60)
            out = []
            for i in range(minutes):
                stamp = window.start + timedelta(minutes=i)
                count = 40 if stamp >= onset_at else 1
                out.append(LogBucket(timestamp=stamp, total=count,
                                     by_level={"ERROR": count}))
            return out

    plan = InvestigationPlan(
        intent=Intent.INCIDENT_INVESTIGATION, system_id="shopdemo",
        system_name="S", environment="staging", namespaces=["shopdemo"],
        requested_window=requested, tools=["logs"], goal="what is wrong",
    )

    windows, _ = await WindowResolver(SurgeAt0512()).resolve(plan)

    assert windows.onset_detected is True
    assert windows.onset_before_window is True, "the onset precedes the question"
    assert windows.baseline is not None
    # the whole point: not one second of the baseline may postdate the onset
    assert windows.baseline.end <= windows.onset, (
        f"baseline ends {windows.baseline.end} but the incident started "
        f"{windows.onset} — it is being compared against itself"
    )
    assert windows.baseline.seconds > 120, "and it must still be long enough to use"


def test_the_baseline_skips_an_earlier_episode_instead_of_averaging_it_in():
    """Measured on the live testbed, and the reason a real surge went unreported.

    Load ran at 15 rps until 04:55, dropped to 2, and rose to 15 again at 05:12.
    Request rate was 3.9 req/s while quiet and 26.7 req/s during the surge — 6.8x.
    The resolver took the fixed-length stretch immediately before the onset,
    which was two thirds the *earlier* surge, and measured 13.5 req/s. The
    reported ratio was 1.97x, under the 2.5x bar, so TRAFFIC_SURGE never fired.

    Position alone is not enough: the stretch has to be quiet as well as prior.
    """
    from datetime import timedelta

    from app.pipeline.windows import place_baseline
    from app.models.evidence import LogBucket

    base = datetime(2026, 8, 12, 4, 26, tzinfo=timezone.utc)
    # 15 quiet minutes, 12 busy (the earlier episode), 13 quiet, then the onset.
    counts = [0] * 15 + [39, 29, 30, 54, 39, 14, 27, 24, 14, 29, 26, 14] + [4, 5, 5, 1, 5, 3, 2, 7, 6, 4, 5, 7, 6]
    series = [
        LogBucket(timestamp=base + timedelta(minutes=i), total=c, by_level={"ERROR": c})
        for i, c in enumerate(counts)
    ]
    onset = base + timedelta(minutes=len(counts))

    window, quiet = place_baseline(
        series, latest_end=onset, length=timedelta(minutes=22),
        earliest=base, quiet_threshold=10.0, min_length=timedelta(minutes=10),
    )

    assert window is not None
    assert quiet is True, "a quiet stretch exists in range and must be found"
    busy_start = base + timedelta(minutes=15)
    busy_end = base + timedelta(minutes=27)
    overlap = (min(window.end, busy_end) - max(window.start, busy_start))
    assert overlap <= timedelta(minutes=4), (
        f"baseline {window} overlaps the earlier episode by {overlap}"
    )


def test_a_system_busy_everywhere_still_gets_the_calmest_window_marked_degraded():
    """No quiet stretch exists, so honesty is the deliverable: return the calmest
    placement and say the ratios are lower bounds. Returning nothing instead
    switches off every baseline-relative signal there is."""
    from datetime import timedelta

    from app.pipeline.windows import place_baseline
    from app.models.evidence import LogBucket

    base = datetime(2026, 8, 12, 4, 0, tzinfo=timezone.utc)
    counts = [30] * 20 + [45] * 20
    series = [
        LogBucket(timestamp=base + timedelta(minutes=i), total=c, by_level={"ERROR": c})
        for i, c in enumerate(counts)
    ]

    window, quiet = place_baseline(
        series, latest_end=base + timedelta(minutes=40),
        length=timedelta(minutes=15), earliest=base, quiet_threshold=10.0,
    )

    assert window is not None, "a degraded comparison beats none at all"
    assert quiet is False
    assert window.start < base + timedelta(minutes=20), "the calmer half is the earlier one"
