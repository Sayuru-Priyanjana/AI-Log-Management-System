from __future__ import annotations

import logging
import math
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
    min_elevation: float | None = None,
    sustain_buckets: int = 3,
    sustain_required: int = 2,
    quiet_gap: int = 4,
) -> OnsetResult:
    """Finds where the error rate departs from its own normal level.

    Median and MAD rather than mean and standard deviation: error counts are
    spiky by nature, and a single burst inflates a standard deviation enough to
    hide the very thing being looked for.

    A crossing has to clear three bars to be accepted:

    1. exceed the threshold, which is floored at sqrt(median) so ordinary count
       noise does not qualify;
    2. be *sustained* — at least `sustain_required` of the next `sustain_buckets`
       stay elevated, so one stray error is not an incident;
    3. actually separate two regimes — the stretch after it must be
       `min_elevation` times busier than the stretch before it.

    A crossing that fails the last test is skipped and the scan continues, because
    a noisy blip early in the range must not hide a real failure later in it.

    When the range ends elevated the search runs *backwards* instead, to find
    where the episode still in progress began. Scanning forward would return the
    oldest departure anywhere in range — which, after an earlier incident has
    come and gone, is the wrong one.
    """
    mad_multiplier = settings.onset_mad_multiplier if mad_multiplier is None else mad_multiplier
    min_absolute = settings.onset_min_absolute if min_absolute is None else min_absolute
    min_elevation = settings.onset_min_elevation if min_elevation is None else min_elevation

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

    # These are event counts, so their natural spread is roughly sqrt(mean) even
    # when nothing is wrong. MAD alone badly understates that: a service sitting
    # at 3 errors/min has a MAD near 1, which puts the threshold at 7 — a level
    # ordinary Poisson noise crosses for several minutes at a time. Taking
    # sqrt(median) as a floor keeps routine variance below the line.
    spread = max(mad, math.sqrt(max(median, 1.0)))

    # Three floors, whichever is highest:
    #  - median + k*spread catches a departure from a noisy-but-steady baseline
    #  - 2x median catches growth where the spread happens to be tiny
    #  - min_absolute stops "1 error where there were 0" counting as an incident
    threshold = max(median + mad_multiplier * spread, 2.0 * median, float(min_absolute))

    # Never quiet at any point in the range. There is no change point to find
    # here because the change happened before anything we can see — which is a
    # different answer from "nothing happened", and must not be confused with it.
    if min(counts) >= max(2 * min_absolute, 2):
        return OnsetResult(index=0, detected=True, before_window=True,
                           threshold=threshold, median=median, mad=mad,
                           reason="never_quiet_in_range")

    # If the range *ends* elevated, the interesting episode is the one still
    # happening. Walk back to where it began rather than scanning forward and
    # locking on to some older, already-resolved incident — "what is wrong now"
    # should not return the start of something that finished half an hour ago,
    # nor lump two separate incidents into one window.
    tail = counts[-min(3, len(counts)):]
    if tail and statistics.mean(tail) >= threshold:
        start = len(counts) - 1
        gap = 0
        peak = max(tail)
        for index in range(len(counts) - 1, -1, -1):
            # The bar to stay "inside the episode" scales with how big the
            # episode is. A fixed bar fails badly here: this baseline's noise
            # peaks reach 11, so anything near the detection threshold chains
            # straight back through ordinary variance and swallows the whole
            # range. A fifth of the episode's own peak separates the two cleanly.
            peak = max(peak, counts[index])
            inside = max(threshold, peak * 0.2)
            if counts[index] >= inside:
                start, gap = index, 0
            else:
                gap += 1
                if gap >= quiet_gap:
                    break

        # Same regime test the forward scan applies: the episode has to be
        # materially busier than what came before it.
        elevation = _elevation(counts, start)
        if elevation is None or elevation >= min_elevation:
            return OnsetResult(
                index=start,
                detected=True,
                before_window=(start == 0),
                threshold=threshold, median=median, mad=mad,
                reason=("elevated_at_first_bucket" if start == 0
                        else "start of the episode still in progress"),
            )

    rejected: list[str] = []
    for index, value in enumerate(counts):
        if value < threshold:
            continue
        follow = counts[index + 1: index + 1 + sustain_buckets]
        sustained = sum(1 for v in follow if v >= threshold)
        # Near the end of the range there is no room left to sustain; accept it
        # rather than miss an incident that just started.
        if not (len(follow) < sustain_required or sustained >= sustain_required):
            continue

        elevation = _elevation(counts, index)
        if elevation is not None and elevation < min_elevation:
            # A blip, not a change of regime. Keep scanning — an early noisy
            # crossing must not mask a genuine failure later in the range.
            rejected.append(f"bucket {index} ({elevation:.1f}x)")
            continue

        return OnsetResult(
            index=index,
            detected=True,
            before_window=(index == 0),
            threshold=threshold, median=median, mad=mad,
            reason="elevated_at_first_bucket" if index == 0 else "sustained_departure",
        )

    reason = "no_sustained_departure"
    if rejected:
        reason = (f"no sustained departure; {len(rejected)} crossing(s) were only brief "
                  f"blips ({', '.join(rejected[:3])})")
    return OnsetResult(index=None, detected=False, before_window=False,
                       threshold=threshold, median=median, mad=mad, reason=reason)


def _elevation(counts: list[float], index: int, min_side: int = 5) -> float | None:
    """How many times busier the post-onset stretch is than the pre-onset one.

    Returns None when either side is too short to compare — a judgement made on
    two buckets is not worth acting on, and refusing to judge is safer than
    rejecting a real incident that only just started.
    """
    before, after = counts[:index], counts[index:]
    if len(before) < min_side or len(after) < min_side:
        return None
    before_rate = statistics.mean(before)
    after_rate = statistics.mean(after)
    if before_rate <= 0:
        # Nothing at all beforehand: any sustained activity is a genuine change.
        return float("inf") if after_rate > 0 else None
    return after_rate / before_rate


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

        # The search deliberately looks back several times further than asked, to
        # catch an onset just outside the window and to find somewhere quiet for
        # a baseline. The *answer*, though, stays inside the question: the window
        # analysed never starts more than the pre-roll before the period asked
        # about. Allowing more than that meant a 7-minute question could analyse
        # 14 minutes and pull in a separate, earlier failure — whose symptoms
        # then appeared to precede the real cause, and the verifier duly
        # (and correctly, given what it was shown) flagged the answer as
        # effect-before-cause. The onset itself is still reported truthfully, and
        # flagged as beginning outside the range.
        earliest_allowed = requested.start - pre_roll
        clamped = onset - pre_roll < earliest_allowed
        incident_start = max(onset - pre_roll, earliest_allowed, search.start)
        incident = TimeWindow(start=incident_start, end=requested.end, label="incident")

        baseline_end = incident_start
        baseline_length = min(max(incident.duration, min_baseline), timedelta(hours=1))
        baseline_start = max(baseline_end - baseline_length, search.start)
        baseline = (
            TimeWindow(start=baseline_start, end=baseline_end, label="baseline")
            if (baseline_end - baseline_start) > timedelta(minutes=2)
            else None
        )

        method = (f"error rate crossed {onset_result.threshold:.1f}/min "
                  f"(median {onset_result.median:.1f}, MAD {onset_result.mad:.1f}) "
                  f"and stayed there")
        if clamped:
            method += (f"; the departure began at {onset:%H:%M:%S}Z, before the period asked "
                       f"about, so the window analysed starts at the edge of that period and "
                       f"the earlier part of the incident was not examined")

        windows = InvestigationWindows(
            requested=requested, incident=incident, baseline=baseline,
            onset=onset, onset_detected=True, onset_before_window=clamped,
            method=method,
        )
        logger.info(
            "Windows resolved: onset=%s incident=%s baseline=%s",
            onset.isoformat(), windows.incident, windows.baseline,
        )
        return windows, buckets
