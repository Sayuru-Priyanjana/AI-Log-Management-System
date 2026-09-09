"""
The period asked about, covered end to end.

Onset detection answers "where did this start" and answers it with one moment.
That is the right anchor for a window whose ratios have to mean something, and
it was the whole answer to "what went wrong in the last six hours" — so a range
holding three separate failures was reported as one, and the reader was never
told the other two existed.

These pin the sweep that closes that gap: every elevated stretch inside the
requested range is found, one of them is marked as the one analysed in depth,
and neither the finding nor the marking may quietly change what the onset
detector does.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.domain import TimeWindow
from app.pipeline.windows import detect_episodes, detect_onset, quiet_level
from tests.conftest import T0, at, buckets


def window_over(counts: list[int], *, step: int = 60) -> TimeWindow:
    return TimeWindow(start=T0, end=at(len(counts) * step))


QUIET = [3, 2, 4, 3, 2]


def test_three_separate_failures_are_reported_as_three_issues():
    """The defect this exists for.

    A six-hour question containing three distinct failures came back describing
    the first one. The other two were inside the range, inside the data, and
    absent from the answer.
    """
    counts = (QUIET * 6
              + [30, 45, 40, 38, 22]
              + QUIET * 6
              + [18, 25, 20, 19, 24]
              + QUIET * 6
              + [55, 60, 58, 62, 51])
    episodes, note = detect_episodes(buckets(counts), within=window_over(counts))

    assert len(episodes) == 3, note
    assert [e.start for e in episodes] == sorted(e.start for e in episodes), \
        "issues are reported in the order they happened, not by severity"
    assert episodes[-1].ongoing is True, \
        "the last stretch runs to the end of the range, so it had not resolved"
    assert all(e.elevation and e.elevation > 2 for e in episodes)


def test_a_calm_minute_inside_a_failure_does_not_split_it_in_two():
    """One bar for entering and leaving would report one incident as four.

    Real failures dip. The exit bar is deliberately lower than the entry bar, and
    a run only ends after several consecutive quiet buckets.
    """
    counts = QUIET * 4 + [30, 28, 5, 31, 27, 4, 33, 29, 30] + QUIET * 4
    episodes, note = detect_episodes(buckets(counts), within=window_over(counts))

    assert len(episodes) == 1, f"{note}; a dip is not a boundary"
    assert episodes[0].minutes >= 9


def test_a_quiet_range_reports_nothing_and_says_what_it_measured():
    """"Nothing crossed" and "we did not look" are different answers.

    An unexplained empty list sent an earlier version of the reasoning loop back
    to re-search the same term at five log levels in turn.
    """
    counts = QUIET * 12
    episodes, note = detect_episodes(buckets(counts), within=window_over(counts))

    assert episodes == []
    assert "swept" in note and "errors/min" in note, note


def test_a_single_elevated_bucket_is_noise_rather_than_an_issue():
    counts = QUIET * 6 + [40] + QUIET * 6
    episodes, _ = detect_episodes(buckets(counts), within=window_over(counts))
    assert episodes == []


def test_the_cap_keeps_the_biggest_issues_and_reports_them_in_time_order():
    """When more stretches are found than fit, the small ones are what go.

    Truncating by position instead would drop whichever happened to be last,
    which on a range that ends badly is the one the reader most needs.
    """
    counts: list[int] = []
    for size in (60, 8, 55, 9, 50, 10, 45, 11):
        counts += QUIET * 4 + [size, size, size]
    counts += QUIET * 4

    episodes, note = detect_episodes(buckets(counts), within=window_over(counts), limit=3)

    assert len(episodes) == 3, note
    assert [e.start for e in episodes] == sorted(e.start for e in episodes)
    assert min(e.peak_errors_per_min for e in episodes) >= 45, \
        "the three loudest stretches are the ones kept"
    assert "left out" in note


def test_an_episode_covers_its_last_bucket_rather_than_ending_at_its_label():
    """A bucket labels the start of its interval.

    Ending the episode at that label reports a two-minute failure as one minute
    long and doubles every per-minute rate derived from it.
    """
    counts = QUIET * 5 + [40, 40, 40] + QUIET * 5
    episodes, _ = detect_episodes(buckets(counts), within=window_over(counts))

    assert len(episodes) == 1
    assert episodes[0].end - episodes[0].start == timedelta(minutes=3)
    assert episodes[0].mean_errors_per_min == pytest.approx(40.0)


def test_the_bars_come_from_the_whole_histogram_not_the_requested_slice():
    """A range entirely inside an incident must not judge itself normal.

    Deriving the threshold from the slice alone lets a stretch running at forty
    errors a minute set its own bar at forty and report itself quiet — which is
    precisely the case a question about "the last ten minutes" of an outage
    produces.
    """
    counts = QUIET * 20 + [40] * 10
    all_buckets = buckets(counts)
    inside_the_incident = TimeWindow(start=at(100 * 60), end=at(len(counts) * 60))

    episodes, note = detect_episodes(all_buckets, within=inside_the_incident)

    assert len(episodes) == 1, note
    assert episodes[0].ongoing is True


def test_the_sweep_does_not_disturb_onset_detection():
    """The two answer different questions and must keep doing so.

    Onset detection places the window whose baseline ratios everything else
    depends on. The sweep is a description of the range around it; if adding one
    moved the other, every stored investigation would become incomparable.
    """
    counts = QUIET * 6 + [30, 45, 40, 38, 22] + QUIET * 6 + [55, 60, 58, 62, 51]
    result = detect_onset(buckets(counts))

    assert result.detected is True
    assert result.reason == "start of the episode still in progress", \
        "a range ending elevated is still anchored to the episode in progress"


def test_the_quiet_level_is_unmoved_by_an_episode_filling_half_the_range():
    """A median climbs with the incident; a low quantile does not.

    Measured live at 24 errors/min against a true quiet level of 5, which made a
    stretch running at six times normal read as normal.
    """
    counts = QUIET * 10 + [40] * 25
    assert quiet_level(buckets(counts)) <= 4.0


@pytest.mark.asyncio
async def test_the_resolver_marks_which_issue_the_analysis_is_actually_about(plan):
    """Exactly one episode is primary, and it is the one the incident covers.

    Without the marking a reader cannot tell which of the listed issues has
    signals, a baseline and a call graph behind it, and which were only counted.
    """
    from app.pipeline.windows import WindowResolver

    counts = QUIET * 6 + [30, 45, 40, 38, 22] + QUIET * 6 + [55, 60, 58, 62, 51]

    class FakeLogs:
        async def histogram(self, _plan, window, interval="60s"):
            return buckets(counts)

    resolved_plan = plan.model_copy(update={
        "requested_window": TimeWindow(start=T0, end=at(len(counts) * 60),
                                       label="requested"),
    })
    windows, _ = await WindowResolver(FakeLogs()).resolve(resolved_plan)

    assert len(windows.episodes) == 2
    primary = [e for e in windows.episodes if e.primary]
    assert len(primary) == 1, "exactly one stretch is the one analysed in depth"
    assert windows.scanned is not None
    assert windows.scanned.start == resolved_plan.requested_window.start
    assert windows.scanned.end == resolved_plan.requested_window.end
    assert windows.secondary_episodes and len(windows.secondary_episodes) == 1


@pytest.mark.asyncio
async def test_a_request_for_records_queries_the_period_it_named(plan):
    """"List the 5xx in the last six hours" is not a root-cause question.

    Narrowing to the onset is what keeps a root-cause answer's ratios
    comparable. Applied to retrieval it produces a short list and a wrong count,
    both presented as complete — the user named the range, and there is no
    baseline ratio here to protect by shrinking it.
    """
    from app.models.plan import Intent
    from app.pipeline.windows import WindowResolver

    counts = QUIET * 20 + [40, 45, 38, 41, 44]

    class FakeLogs:
        async def histogram(self, _plan, window, interval="60s"):
            return buckets(counts)

    requested = TimeWindow(start=T0, end=at(len(counts) * 60), label="requested")
    listing = plan.model_copy(update={"intent": Intent.DATA_EXTRACTION,
                                      "requested_window": requested})
    windows, _ = await WindowResolver(FakeLogs()).resolve(listing)

    assert windows.incident.start == requested.start
    assert windows.incident.end == requested.end
    assert windows.onset is not None, "the onset is still found and still reported"
    assert "whole of it was queried" in windows.method
    # A baseline is still placed, so any signal detected alongside the records
    # stays a comparison rather than a bare number.
    assert windows.baseline is not None
    assert windows.baseline.end <= windows.onset


@pytest.mark.asyncio
async def test_a_root_cause_question_still_narrows_to_its_onset(plan):
    """The counterpart. Widening a root-cause window to the whole range dilutes
    the spike being explained: twenty minutes of failure averaged over six hours
    stops clearing any threshold."""
    from app.pipeline.windows import WindowResolver

    counts = QUIET * 20 + [40, 45, 38, 41, 44]

    class FakeLogs:
        async def histogram(self, _plan, window, interval="60s"):
            return buckets(counts)

    requested = TimeWindow(start=T0, end=at(len(counts) * 60), label="requested")
    windows, _ = await WindowResolver(
        FakeLogs()).resolve(plan.model_copy(update={"requested_window": requested}))

    assert windows.incident.start > requested.start
    assert windows.incident.duration < requested.duration
    # …and the part of the range it does not cover is still described, which is
    # what stops the narrowing hiding anything.
    assert windows.scanned == TimeWindow(start=requested.start, end=requested.end,
                                         label="scanned")
