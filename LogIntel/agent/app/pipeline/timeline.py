from __future__ import annotations

from datetime import datetime

from app.models.analysis import InvestigationWindows
from app.models.answer import TimelineEntry
from app.models.evidence import EvidenceBundle
from app.models.signals import Signal, SignalType
from app.util.timefmt import clock

# The timeline is built here, not by the model. It is a statement of fact about
# ordering, and ordering is exactly the kind of thing a small model gets subtly
# wrong while sounding certain.

ERROR_LEVELS = ("ERROR", "FATAL", "CRITICAL")


def build_evidence_timeline(windows: InvestigationWindows, signals: list[Signal],
                            evidence: EvidenceBundle,
                            limit: int = 60) -> list[TimelineEntry]:
    """Everything the investigation actually looked at, in order, folded.

    One row per distinct thing, not per occurrence. The log tool has already
    collapsed messages to templates, and the event collector preserves
    Kubernetes' own repeat counts, so the folding is real rather than a display
    trick: 451 identical "DependencyUnreachable" lines are one entry that
    happened 451 times, with the span it covered.

    Metrics are included only where they moved. A flat series is not an event,
    and listing all 52 of them would bury the handful that changed.
    """
    entries: list[TimelineEntry] = []
    signal_targets = {s.service for s in signals if s.service}

    # `is_new` means baseline_count == 0, which is also what a *missing* baseline
    # produces — so without this check every routine INFO line gets labelled
    # "never seen before". Comparison claims are only made when there was
    # something to compare against.
    has_baseline = windows.baseline is not None and evidence.logs.baseline_documents > 0

    # -- the moment the window turned, as an anchor ------------------------
    if windows.onset and windows.onset_detected and not windows.onset_before_window:
        entries.append(TimelineEntry(
            id="marker:onset", kind="marker", first_seen=windows.onset,
            title="Incident window begins here",
            detail=windows.method, level="onset",
            notable=True, notable_reason="This is where the system's behaviour changed.",
        ))

    # -- logs, already folded to templates ---------------------------------
    for pattern in evidence.logs.patterns:
        if not pattern.first_seen:
            continue
        notable, reason = False, ""
        if pattern.level in ("FATAL", "CRITICAL"):
            notable, reason = True, "Fatal-level message."
        elif has_baseline and pattern.is_new and pattern.level in ERROR_LEVELS:
            notable, reason = True, "This error does not appear in the baseline window at all."
        elif has_baseline and pattern.level in ERROR_LEVELS and (pattern.growth or 0) > 1.5:
            notable = True
            reason = f"{pattern.growth:.1f}x more frequent than in the baseline window."
        elif not has_baseline and pattern.level in ERROR_LEVELS:
            notable = True
            reason = ("An error, though with no baseline window there is nothing to "
                      "say whether it is unusual.")

        entries.append(TimelineEntry(
            id=pattern.id, kind="log", first_seen=pattern.first_seen,
            last_seen=pattern.last_seen,
            title=pattern.example[:220],
            detail=pattern.template[:220] if pattern.template != pattern.example else "",
            service=pattern.service, level=pattern.level,
            occurrences=pattern.count,
            # None means "not comparable", which is different from zero.
            baseline_occurrences=pattern.baseline_count if has_baseline else None,
            notable=notable, notable_reason=reason,
        ))

    # -- Kubernetes events, with their own repeat counts -------------------
    for event in evidence.events.events:
        if not event.onset:
            continue
        notable = event.severity in ("warning", "critical")
        entries.append(TimelineEntry(
            id=event.id, kind="event", first_seen=event.onset,
            last_seen=event.last_timestamp,
            title=f"{event.reason} on {event.pod or event.involved_name or 'cluster'}",
            detail=event.message[:220],
            service=event.service, level=event.type,
            occurrences=event.count,
            baseline_occurrences=evidence.events.baseline_reasons.get(event.reason),
            notable=notable,
            notable_reason=("Kubernetes reported this as a problem."
                            if notable else ""),
        ))

    # -- metrics that moved -------------------------------------------------
    for series in evidence.metrics.series:
        if not series.points:
            continue

        ratio = series.ratio_to_baseline()
        in_window = False
        if ratio is None:
            # No baseline to compare against — but a metric that swung inside the
            # window is still a real event, and dropping it left the timeline
            # with no metrics at all on exactly the runs where evidence was
            # thinnest. Fall back to comparing the window against itself.
            low, high = series.incident.minimum, series.incident.maximum
            if low is None or high is None or low <= 0:
                continue
            ratio, in_window = high / low, True
        if 0.66 < ratio < 1.5:
            continue
        # Place it where it was most extreme — the closest thing a continuous
        # series has to a moment.
        peak = (max(series.points, key=lambda p: p.value) if ratio >= 1.5
                else min(series.points, key=lambda p: p.value))
        # "fell 0.0x" is not English. A rise reads as a multiple, a fall as the
        # fraction that remains.
        against = "within the window" if in_window else "baseline"
        if ratio >= 1.5:
            movement = f"rose {ratio:.1f}x"
        elif ratio < 0.05:
            movement = "fell to zero"
        else:
            movement = f"fell to {ratio:.0%} of {against}"
        scope = series.pod or series.service or "—"
        baseline = (series.baseline.average
                    if series.baseline and series.baseline.average is not None else None)

        # Notability comes from the size of the movement, not from whether some
        # other detector happened to fire on the same service. A 12x traffic rise
        # is worth the reader's attention on its own; requiring a corroborating
        # signal meant a genuine surge could sit in the timeline unmarked.
        if ratio >= 3 or ratio <= 0.33:
            notable = True
            reason = (f"A large change: {movement}"
                      + (", measured within the window because no baseline was "
                         "available to compare against." if in_window else "."))
        elif series.service in signal_targets:
            notable = True
            reason = "A signal also fired on this service."
        else:
            notable, reason = False, ""

        entries.append(TimelineEntry(
            id=series.id, kind="metric", first_seen=peak.timestamp,
            title=f"{series.metric} {movement} on {scope}",
            detail=(f"peak {peak.value:.4g} {series.unit}"
                    + (f", baseline {baseline:.4g}" if baseline is not None else "")
                    + (", no baseline available" if in_window else "")),
            service=series.service, level="metric",
            notable=notable, notable_reason=reason,
        ))

    entries.sort(key=lambda e: e.first_seen)

    # Notable entries are never dropped by the cap — the cap exists to stop a
    # busy window flooding the view, not to hide the interesting part of it.
    if len(entries) > limit:
        notable = [e for e in entries if e.notable]
        ordinary = [e for e in entries if not e.notable][: max(0, limit - len(notable))]
        entries = sorted(notable + ordinary, key=lambda e: e.first_seen)

    return entries


def build_timeline(windows: InvestigationWindows, signals: list[Signal],
                   evidence: EvidenceBundle, limit: int = 12) -> list[str]:
    entries: list[tuple[datetime, str]] = []

    for signal in signals:
        if not signal.first_seen:
            continue
        scope = signal.service or signal.pod or "system"
        magnitude = f" — {signal.magnitude.describe()}" if signal.magnitude else ""
        entries.append((
            signal.first_seen,
            f"{clock(signal.first_seen)}  {signal.type.value} on {scope}{magnitude}",
        ))

    # The first appearance of a brand-new error is often the earliest concrete
    # trace of the fault, before any threshold has had time to trip.
    for pattern in evidence.logs.patterns:
        if pattern.is_new and pattern.level in ("ERROR", "FATAL", "CRITICAL") and pattern.first_seen:
            entries.append((
                pattern.first_seen,
                f"{clock(pattern.first_seen)}  first occurrence of a new error in "
                f"{pattern.service}: \"{pattern.example[:120]}\"",
            ))

    for event in evidence.events.events:
        if event.severity == "info" or not event.onset:
            continue
        entries.append((
            event.onset,
            f"{clock(event.onset)}  Kubernetes {event.reason} on "
            f"{event.pod or event.involved_name} (x{event.count})",
        ))

    entries.sort(key=lambda item: item[0])

    deduped: list[str] = []
    seen: set[str] = set()
    for _, text in entries:
        key = text[10:]     # ignore the timestamp when deduplicating
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)

    if windows.onset and windows.onset_detected and not windows.onset_before_window:
        deduped.insert(0, f"{clock(windows.onset)}  error rate departed from its baseline "
                          f"({windows.method})")

    return deduped[:limit]
