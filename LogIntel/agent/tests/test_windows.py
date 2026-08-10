from __future__ import annotations

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
