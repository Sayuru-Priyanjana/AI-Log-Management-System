from __future__ import annotations

from datetime import datetime

from app.models.analysis import InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.signals import Signal, SignalType

# The timeline is built here, not by the model. It is a statement of fact about
# ordering, and ordering is exactly the kind of thing a small model gets subtly
# wrong while sounding certain.


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
            f"{signal.first_seen:%H:%M:%S}Z  {signal.type.value} on {scope}{magnitude}",
        ))

    # The first appearance of a brand-new error is often the earliest concrete
    # trace of the fault, before any threshold has had time to trip.
    for pattern in evidence.logs.patterns:
        if pattern.is_new and pattern.level in ("ERROR", "FATAL", "CRITICAL") and pattern.first_seen:
            entries.append((
                pattern.first_seen,
                f"{pattern.first_seen:%H:%M:%S}Z  first occurrence of a new error in "
                f"{pattern.service}: \"{pattern.example[:120]}\"",
            ))

    for event in evidence.events.events:
        if event.severity == "info" or not event.onset:
            continue
        entries.append((
            event.onset,
            f"{event.onset:%H:%M:%S}Z  Kubernetes {event.reason} on "
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
        deduped.insert(0, f"{windows.onset:%H:%M:%S}Z  error rate departed from its baseline "
                          f"({windows.method})")

    return deduped[:limit]
