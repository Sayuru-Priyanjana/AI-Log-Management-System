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


def _nearest_bucket(buckets: list[LogBucket], moment: datetime) -> int:
    """The histogram bucket closest to a time found by another detector."""
    if not buckets:
        return 0
    return min(range(len(buckets)),
               key=lambda i: abs((ensure_utc(buckets[i].timestamp) - moment).total_seconds()))


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


def detect_step_change(points: list[tuple[datetime, float]], *,
                       multiplier: float = 2.0, min_side: int = 4,
                       floor: float = 0.0) -> tuple[datetime | None, float, float]:
    """Finds where a continuous metric steps up and stays up.

    Returns (onset, before_level, after_level). Levels are medians, so a couple
    of spikes cannot manufacture a step change and a couple of dips cannot hide
    one.

    This exists because onset detection used to look only at error counts, and a
    whole class of incident produces no errors at all. A dependency that answers
    every request but takes 1.5s to do it is invisible to an error histogram, so
    the window got anchored to some unrelated error blip, the baseline was drawn
    from the wrong period, and the slowdown never surfaced.
    """
    if len(points) < min_side * 2:
        return None, 0.0, 0.0

    values = [value for _, value in points]
    best: tuple[datetime | None, float, float, float] = (None, 0.0, 0.0, float("-inf"))

    for index in range(min_side, len(values) - min_side + 1):
        before = statistics.median(values[:index])
        after = statistics.median(values[index:])
        if after < floor:
            continue
        # A ratio against a near-zero baseline is meaningless, so require an
        # absolute gap as well as a proportional one.
        ratio = float("inf") if before <= 0 else after / before
        if ratio < multiplier or (after - before) < floor:
            continue

        # Located by the difference of MEANS, reported by medians. The mean
        # difference peaks exactly at a clean step, which the median ratio does
        # not: it saturates, so several candidate splits tie and the first one
        # wins arbitrarily — placing the onset well before the actual change.
        # Medians still gate and describe it, so isolated spikes cannot qualify.
        separation = (statistics.fmean(values[index:]) - statistics.fmean(values[:index]))
        if separation > best[3]:
            best = (points[index][0], before, after, separation)

    return best[0], best[1], best[2]


class WindowResolver:
    def __init__(self, log_tool: LogTool, prometheus=None) -> None:
        self._logs = log_tool
        self._prometheus = prometheus

    async def _latency_onset(self, plan: InvestigationPlan,
                             search: TimeWindow) -> tuple[datetime | None, str]:
        """When request latency last stepped up, across any service in scope."""
        if self._prometheus is None:
            return None, ""

        namespaces = plan.namespaces or []
        selector = (f'namespace="{namespaces[0]}"' if len(namespaces) == 1
                    else 'namespace=~"' + "|".join(namespaces) + '"' if namespaces
                    else 'namespace!=""')
        expression = (
            "histogram_quantile(0.95, sum by (service, le) "
            f"(rate(http_request_duration_seconds_bucket{{{selector}}}[2m])))"
        )
        client = self._prometheus
        try:
            series = await client.query_range(
                expression, search, client.step_for(search, target_points=90)
            )
        except Exception as exc:      # metrics are an enhancement here, not a requirement
            logger.debug("Latency onset detection unavailable: %s", exc)
            return None, ""

        latest: tuple[datetime | None, str] = (None, "")
        for item in series:
            points = client.to_points(item)
            onset, before, after = detect_step_change(
                points,
                multiplier=settings.latency_degradation_multiplier,
                floor=settings.latency_min_seconds,
            )
            if onset and (latest[0] is None or onset > latest[0]):
                service = item.get("metric", {}).get("service", "a service")
                latest = (onset, f"p95 latency for {service} stepped from "
                                 f"{before:.2f}s to {after:.2f}s and stayed there")
        return latest

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

        # Errors are one symptom among several. Latency is checked too, and the
        # *later* of the two wins: a question about the system now should be
        # answered about the regime the system is in now, not about an older
        # episode that happens to be the first thing in range.
        latency_onset, latency_note = await self._latency_onset(plan, search)
        error_onset = (ensure_utc(buckets[onset_result.index].timestamp)
                       if onset_result.detected and onset_result.index is not None else None)
        onset_source = "error rate"

        if latency_onset and (error_onset is None or latency_onset > error_onset):
            onset_source = "latency"
            onset_result = OnsetResult(
                index=_nearest_bucket(buckets, latency_onset),
                detected=True,
                before_window=False,
                threshold=onset_result.threshold,
                median=onset_result.median,
                mad=onset_result.mad,
                reason=latency_note,
            )

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

        if onset_source == "latency":
            method = onset_result.reason
        else:
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
