from __future__ import annotations

from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, MetricEvidence
from app.models.signals import Severity, Signal, SignalType
from app.pipeline.timeline import build_evidence_timeline
from tests.conftest import at, event, pattern, series


def bundle(*, patterns=None, events=None, metrics=None, baseline_reasons=None,
           baseline_documents=90000):
    return EvidenceBundle(
        logs=LogEvidence(patterns=patterns or [], total_documents=42221,
                         baseline_documents=baseline_documents),
        events=EventEvidence(events=events or [],
                             baseline_reasons=baseline_reasons or {}),
        metrics=MetricEvidence(series=metrics or []),
    )


def test_repeated_log_lines_become_one_entry_with_a_count(windows):
    """The core requirement: 451 identical lines are one row that happened 451
    times, not 451 rows. The log tool already folds by message template, so the
    count is real rather than a display trick."""
    entries = build_evidence_timeline(windows, [], bundle(patterns=[
        pattern("Upstream dependency payment-db failed: DependencyUnreachable",
                service="payment-api", count=451, baseline_count=0),
    ]))

    logs = [e for e in entries if e.kind == "log"]
    assert len(logs) == 1, "one entry per distinct message, not per occurrence"
    assert logs[0].occurrences == 451
    assert logs[0].is_repeated
    assert logs[0].first_seen and logs[0].last_seen, "the span it covered"


def test_a_brand_new_error_is_highlighted_and_says_why(windows):
    entries = build_evidence_timeline(windows, [], bundle(patterns=[
        pattern("Upstream dependency payment-db failed", service="payment-api",
                count=451, baseline_count=0),
    ]))
    entry = next(e for e in entries if e.kind == "log")
    assert entry.notable
    assert "baseline" in entry.notable_reason
    assert entry.baseline_occurrences == 0


def test_a_routine_repeated_message_is_not_highlighted(windows):
    entries = build_evidence_timeline(windows, [], bundle(patterns=[
        pattern("Payment processed successfully", service="payment-api",
                count=9000, baseline_count=8800, level="INFO"),
    ]))
    entry = next(e for e in entries if e.kind == "log")
    assert entry.occurrences == 9000
    assert not entry.notable, "high volume alone is not interesting"


def test_without_a_baseline_nothing_is_called_new(windows):
    """Caught on live output: every routine INFO line was labelled "never seen
    before" at ×23,390, because `is_new` means baseline_count == 0 — which is
    also what a *missing* baseline produces. A comparison claim needs something
    to compare against."""
    windows.baseline = None
    entries = build_evidence_timeline(windows, [], bundle(
        patterns=[
            pattern("payment-db request completed", service="payment-db",
                    count=23390, baseline_count=0, level="INFO"),
            pattern("payment-api failed to process the request",
                    service="payment-api", count=108, baseline_count=0),
        ],
        baseline_documents=0,
    ))

    routine = next(e for e in entries if "completed" in e.title)
    assert not routine.notable, "routine traffic is not a finding"
    assert routine.baseline_occurrences is None, "unknown, not zero"

    failure = next(e for e in entries if "failed" in e.title)
    assert failure.notable, "an error is still worth showing"
    assert "no baseline" in failure.notable_reason, "but the claim must be honest"


def test_kubernetes_repeat_counts_are_preserved(windows):
    entries = build_evidence_timeline(windows, [], bundle(
        events=[event("Unhealthy", pod="payment-db-abc-12345", count=510, first=640)],
        baseline_reasons={"Unhealthy": 3},
    ))
    entry = next(e for e in entries if e.kind == "event")
    assert entry.occurrences == 510
    assert entry.baseline_occurrences == 3
    assert entry.notable, "a warning-level event is worth attention"


def test_entries_are_ordered_by_when_they_first_happened(windows):
    entries = build_evidence_timeline(windows, [], bundle(
        patterns=[
            pattern("later error", service="payment-api", count=5,
                    baseline_count=0, first=900),
            pattern("earlier error", service="payment-db", count=5,
                    baseline_count=0, first=650),
        ],
        events=[event("BackOff", pod="payment-api-abc-12345", count=4, first=800)],
    ))
    stamps = [e.first_seen for e in entries]
    assert stamps == sorted(stamps)


def test_only_metrics_that_moved_appear(windows):
    metrics = [
        series("cpu_usage_cores", [0.05, 0.05, 0.05], labels={"pod": "a", "container": "app"},
               baseline=[0.05, 0.05, 0.05], unit="cores"),
        series("memory_working_set_bytes", [4.0e8, 4.2e8, 4.4e8],
               labels={"pod": "b", "container": "app"},
               baseline=[1.0e8, 1.0e8, 1.0e8], unit="bytes"),
    ]
    entries = build_evidence_timeline(windows, [], bundle(metrics=metrics))
    metric_entries = [e for e in entries if e.kind == "metric"]
    assert len(metric_entries) == 1, "a flat series is not an event"
    assert "rose" in metric_entries[0].title


def test_the_window_onset_appears_as_an_anchor(windows):
    entries = build_evidence_timeline(windows, [], bundle(patterns=[
        pattern("boom", service="payment-api", count=5, baseline_count=0),
    ]))
    marker = next((e for e in entries if e.kind == "marker"), None)
    assert marker is not None
    assert marker.notable
    assert marker.first_seen == windows.onset


def test_notable_entries_survive_the_cap(windows):
    """A busy window must not push the interesting rows off the end."""
    noise = [pattern(f"routine message {i}", service="payment-api", count=10,
                     baseline_count=10, level="INFO", first=600 + i)
             for i in range(80)]
    important = pattern("Database connection refused", service="payment-db",
                        count=40, baseline_count=0, first=1200)

    entries = build_evidence_timeline(windows, [], bundle(patterns=noise + [important]),
                                      limit=20)

    assert len(entries) <= 21     # the cap, plus the onset marker
    assert any("connection refused" in e.title for e in entries)


def test_a_metric_on_a_service_with_a_signal_is_highlighted(windows):
    signal = Signal(id="sig:MEMORY_PRESSURE:x", type=SignalType.MEMORY_PRESSURE,
                    severity=Severity.HIGH, service="payment-api", first_seen=at(600),
                    description="memory high")
    metrics = [series("memory_working_set_bytes", [4.0e8, 4.4e8],
                      labels={"service": "payment-api"},
                      baseline=[1.0e8, 1.0e8], unit="bytes")]
    entries = build_evidence_timeline(windows, [signal], bundle(metrics=metrics))
    entry = next(e for e in entries if e.kind == "metric")
    assert entry.notable
    assert "signal fired" in entry.notable_reason
