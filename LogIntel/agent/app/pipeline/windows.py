from __future__ import annotations

import logging
import statistics
from datetime import timedelta

from app.config import settings
from app.models.analysis import InvestigationWindows
from app.models.domain import TimeWindow, ensure_utc
from app.models.evidence import LogBucket
from app.models.plan import InvestigationPlan
from app.tools.logs import LogTool

logger = logging.getLogger(__name__)

ERROR_LEVELS = ("ERROR", "FATAL", "CRITICAL")


class OnsetResult:
    __slots__ = ("index", "detected", "before_window", "threshold", "median", "mad", "reason")

    def __init__(self, *, index: int | None, detected: bool, before_window: bool,
                 threshold: float, median: float, mad: float, reason: str) -> None:
        self.index = index
        self.detected = detected
        self.before_window = before_window
        self.threshold = threshold
        self.median = median
        self.mad = mad
        self.reason = reason


def detect_onset(
    buckets: list[LogBucket],
    *,
    mad_multiplier: float | None = None,
    min_absolute: int | None = None,
    sustain_buckets: int = 3,
    sustain_required: int = 2,
) -> OnsetResult:
    """Finds where the error rate departs from its own normal level.

    Median and MAD rather than mean and standard deviation: error counts are
    spiky by nature, and a single burst inflates a standard deviation enough to
    hide the very thing being looked for.

    A candidate onset must also be *sustained* — at least `sustain_required` of
    the next `sustain_buckets` buckets stay elevated. Without that, one stray
    error in an otherwise quiet window becomes an "incident".
    """
    mad_multiplier = settings.onset_mad_multiplier if mad_multiplier is None else mad_multiplier
    min_absolute = settings.onset_min_absolute if min_absolute is None else min_absolute

    counts = [float(bucket.errors) for bucket in buckets]
    if not counts:
        return OnsetResult(index=None, detected=False, before_window=False,
                           threshold=0.0, median=0.0, mad=0.0, reason="no_buckets")

    ordered = sorted(counts)

    # The baseline is estimated from the *quiet* portion of the range, not from
    # all of it. When an incident fills half the window it drags the median up
    # with it, and a threshold derived from that median sits above the very
    # spike it is supposed to find.
    quiet = ordered[: max(3, int(len(ordered) * 0.6))]
    median = statistics.median(quiet)
    mad = statistics.median([abs(value - median) for value in quiet])

    # Three floors, whichever is highest:
    #  - median + k*MAD catches a departure from a noisy-but-steady baseline
    #  - 2x median catches growth where MAD happens to be tiny
    #  - min_absolute stops "1 error where there were 0" counting as an incident
    threshold = max(median + mad_multiplier * mad, 2.0 * median, float(min_absolute))

    # Never quiet at any point in the range. There is no change point to find
    # here because the change happened before anything we can see — which is a
    # different answer from "nothing happened", and must not be confused with it.
    if min(counts) >= max(2 * min_absolute, 2):
        return OnsetResult(index=0, detected=True, before_window=True,
                           threshold=threshold, median=median, mad=mad,
                           reason="never_quiet_in_range")

    for index, value in enumerate(counts):
        if value < threshold:
            continue
        follow = counts[index + 1: index + 1 + sustain_buckets]
        sustained = sum(1 for v in follow if v >= threshold)
        # Near the end of the range there is no room left to sustain; accept it
        # rather than miss an incident that just started.
        if len(follow) < sustain_required or sustained >= sustain_required:
            return OnsetResult(
                index=index,
                detected=True,
                before_window=(index == 0),
                threshold=threshold, median=median, mad=mad,
                reason="elevated_at_first_bucket" if index == 0 else "sustained_departure",
            )

    return OnsetResult(index=None, detected=False, before_window=False,
                       threshold=threshold, median=median, mad=mad,
                       reason="no_sustained_departure")


class WindowResolver:
    def __init__(self, log_tool: LogTool) -> None:
        self._logs = log_tool

    async def resolve(self, plan: InvestigationPlan) -> tuple[InvestigationWindows, list[LogBucket]]:
        requested = plan.requested_window

        # Look further back than asked so an incident that started just before
        # the requested window is still found, and so there is somewhere quiet to
        # take a baseline from.
        lookback = min(
            requested.duration * settings.onset_lookback_multiplier,
            timedelta(hours=settings.onset_max_lookback_hours),
        )
        search = TimeWindow(start=requested.end - lookback, end=requested.end, label="search")

        interval = f"{settings.onset_bucket_seconds}s"
        buckets = await self._logs.histogram(plan, search, interval=interval)
        onset_result = detect_onset(buckets)

        pre_roll = timedelta(seconds=settings.incident_pre_roll_seconds)
        min_baseline = timedelta(minutes=settings.min_baseline_minutes)

        if not onset_result.detected or onset_result.index is None:
            # Nothing stood out. Analyse exactly what was asked for, and compare
            # it against the equivalent stretch immediately before.
            incident = requested
            baseline = incident.shifted_back(timedelta(0), max(incident.duration, min_baseline))
            baseline = TimeWindow(start=max(baseline.start, search.start),
                                  end=incident.start, label="baseline")
            windows = InvestigationWindows(
                requested=requested,
                incident=TimeWindow(start=incident.start, end=incident.end, label="incident"),
                baseline=baseline if baseline.seconds > 60 else None,
                onset=None,
                onset_detected=False,
                onset_before_window=False,
                method=f"no_onset ({onset_result.reason}; threshold {onset_result.threshold:.1f}/min)",
            )
            return windows, buckets

        onset = ensure_utc(buckets[onset_result.index].timestamp)

        if onset_result.before_window:
            # Elevated from the very first bucket we can see. The true start is
            # earlier than anything available, so say so rather than presenting
            # the edge of the search range as the onset.
            incident = TimeWindow(start=max(requested.start, search.start),
                                  end=requested.end, label="incident")
            windows = InvestigationWindows(
                requested=requested, incident=incident, baseline=None,
                onset=onset, onset_detected=True, onset_before_window=True,
                method=(f"errors already elevated {lookback.total_seconds() / 3600:.1f}h ago; "
                        f"no quiet baseline available in range"),
            )
            return windows, buckets

        incident_start = max(onset - pre_roll, search.start)
        incident = TimeWindow(start=incident_start, end=requested.end, label="incident")

        baseline_end = incident_start
        baseline_length = min(max(incident.duration, min_baseline), timedelta(hours=1))
        baseline_start = max(baseline_end - baseline_length, search.start)
        baseline = (
            TimeWindow(start=baseline_start, end=baseline_end, label="baseline")
            if (baseline_end - baseline_start) > timedelta(minutes=2)
            else None
        )

        windows = InvestigationWindows(
            requested=requested, incident=incident, baseline=baseline,
            onset=onset, onset_detected=True, onset_before_window=False,
            method=(f"error rate crossed {onset_result.threshold:.1f}/min "
                    f"(median {onset_result.median:.1f}, MAD {onset_result.mad:.1f}) "
                    f"and stayed there"),
        )
        logger.info(
            "Windows resolved: onset=%s incident=%s baseline=%s",
            onset.isoformat(), windows.incident, windows.baseline,
        )
        return windows, buckets
