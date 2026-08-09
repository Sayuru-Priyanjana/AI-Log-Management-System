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
