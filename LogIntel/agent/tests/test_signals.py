from __future__ import annotations

from app.models.evidence import EvidenceBundle, EventEvidence, LogEvidence, MetricEvidence
from app.models.signals import SignalType
from app.pipeline.signals import ServiceResolver, SignalEngine
from tests.conftest import event, pattern, series

SERVICES = ["checkout-api", "payment-api", "payment-db", "loadgen"]


def engine() -> SignalEngine:
    return SignalEngine(known_services=SERVICES)


def bundle(*, metrics=None, patterns=None, events=None) -> EvidenceBundle:
    return EvidenceBundle(
        logs=LogEvidence(patterns=patterns or [], totals_by_level={"ERROR": 100}),
        events=EventEvidence(events=events or []),
        metrics=MetricEvidence(series=metrics or []),
    )


# --------------------------------------------------------------------------
# Units. These are the cases a percentage-against-raw-value threshold gets wrong.
# --------------------------------------------------------------------------
def test_cpu_is_measured_against_its_limit_not_a_bare_number(plan, windows):
    # 0.28 cores against a 0.3 core limit is saturation, even though 0.28 would
    # never exceed a threshold of "80".
    metrics = [
        series("cpu_usage_cores", [0.05, 0.12, 0.28, 0.29],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="cores"),
        series("cpu_limit_cores", [0.3, 0.3, 0.3, 0.3],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="cores"),
    ]
    signals = engine().detect(plan, windows, bundle(metrics=metrics))
    assert any(s.type is SignalType.CPU_SATURATION for s in signals)


def test_idle_cpu_produces_no_saturation_signal(plan, windows):
    metrics = [
        series("cpu_usage_cores", [0.01, 0.02, 0.01],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="cores"),
        series("cpu_limit_cores", [0.3, 0.3, 0.3],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="cores"),
    ]
    signals = engine().detect(plan, windows, bundle(metrics=metrics))
    assert not any(s.type is SignalType.CPU_SATURATION for s in signals)


def test_memory_in_bytes_does_not_trip_on_magnitude_alone(plan, windows):
    # 45 MB is a huge number and a small fraction of a 256 MB limit. Comparing
    # the raw byte count to a threshold would fire here; comparing the ratio does not.
    metrics = [
        series("memory_working_set_bytes", [45_000_000, 46_000_000],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="bytes"),
        series("memory_limit_bytes", [268_435_456, 268_435_456],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="bytes"),
    ]
    signals = engine().detect(plan, windows, bundle(metrics=metrics))
    assert not any(s.type is SignalType.MEMORY_PRESSURE for s in signals)


def test_memory_close_to_its_limit_does_trip(plan, windows):
    metrics = [
        series("memory_working_set_bytes", [100_000_000, 250_000_000],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="bytes"),
        series("memory_limit_bytes", [268_435_456, 268_435_456],
               labels={"pod": "payment-api-abc-12345", "container": "app"}, unit="bytes"),
    ]
    signals = engine().detect(plan, windows, bundle(metrics=metrics))
    pressure = [s for s in signals if s.type is SignalType.MEMORY_PRESSURE]
    assert pressure and pressure[0].service == "payment-api"


# --------------------------------------------------------------------------
# Events. These are the cases a reason->action mapping table gets wrong.
# --------------------------------------------------------------------------
def test_unmapped_event_reasons_still_produce_signals(plan, windows):
    # 'Unhealthy' is not in any canonical reason->action table, and it is also
    # the most common warning in a real cluster.
    signals = engine().detect(plan, windows, bundle(
        events=[event("Unhealthy", pod="payment-api-abc-12345", count=42)]
    ))
    assert any(s.type is SignalType.READINESS_FAILURE for s in signals)


def test_backoff_is_recognised_as_a_crashloop(plan, windows):
    signals = engine().detect(plan, windows, bundle(
        events=[event("BackOff", pod="payment-api-abc-12345", count=7)]
    ))
    assert any(s.type is SignalType.CRASHLOOP for s in signals)


def test_event_onset_uses_first_timestamp_not_last(plan, windows):
    # A recurring event's document timestamp is its most recent firing. Using it
    # would place a long-running condition after everything it caused.
    signals = engine().detect(plan, windows, bundle(
        events=[event("OOMKilling", pod="payment-api-abc-12345", count=3, first=630)]
    ))
    oom = [s for s in signals if s.type is SignalType.OOM_KILL]
    assert oom and oom[0].first_seen.second == 30 and oom[0].first_seen.minute == 10


# --------------------------------------------------------------------------
# Baseline-relative log signals
# --------------------------------------------------------------------------
def test_new_error_pattern_is_detected(plan, windows):
    patterns = [pattern("Database connection timeout", service="payment-api",
                        count=120, baseline_count=0)]
    signals = engine().detect(plan, windows, bundle(patterns=patterns))
    assert any(s.type is SignalType.NEW_ERROR_PATTERN for s in signals)


def test_steady_background_errors_are_not_a_spike(plan, windows):
    # Same rate in both windows: present, but not news.
    patterns = [pattern("Payment failed randomly", service="payment-api",
                        count=20, baseline_count=20)]
    signals = engine().detect(plan, windows, bundle(patterns=patterns))
    assert not any(s.type is SignalType.ERROR_RATE_SPIKE for s in signals)
    assert not any(s.type is SignalType.NEW_ERROR_PATTERN for s in signals)


def test_dependency_attribution_names_the_dependency_not_the_caller(plan, windows):
    metrics = [
        series("dependency_failure_rate", [2.0, 2.1],
               labels={"service": "payment-api", "dependency": "payment-db"}, unit="req/s"),
        series("dependency_request_rate", [2.0, 2.1],
               labels={"service": "payment-api", "dependency": "payment-db"}, unit="req/s"),
    ]
    signals = engine().detect(plan, windows, bundle(metrics=metrics))
    outage = [s for s in signals if s.type is SignalType.DEPENDENCY_UNAVAILABLE]
    assert outage, "100% dependency failure should read as unavailable"
    assert outage[0].service == "payment-db", "the failing component is the answer, not its caller"


def test_signals_are_ordered_by_onset(plan, windows):
    signals = engine().detect(plan, windows, bundle(events=[
        event("BackOff", pod="payment-api-abc-12345", first=900),
        event("OOMKilling", pod="payment-api-abc-12345", first=700),
    ]))
    stamps = [s.first_seen for s in signals if s.first_seen]
    assert stamps == sorted(stamps)


# --------------------------------------------------------------------------
def test_service_resolver_strips_replicaset_hashes():
    resolver = ServiceResolver(SERVICES)
    # Known services match by prefix, which is the path that matters in practice.
    assert resolver.from_pod("payment-api-69d7b68776-mqxxd") == "payment-api"
    assert resolver.from_pod("checkout-api-5f4b8c9d7-qz4xk") == "checkout-api"
    # Unknown workloads fall back to the regex, which uses Kubernetes' own
    # vowel-free suffix alphabet so a real word is not mistaken for a hash.
    assert resolver.from_pod("some-unknown-thing-7d9f6b8c55-kx2qp") == "some-unknown-thing"
    # Nothing that looks like a generated suffix, so nothing is stripped. A
    # bare pod really can be called this, and guessing would rename it.
    assert resolver.from_pod("standalone-debug-pod") == "standalone-debug-pod"
    assert resolver.from_pod("unknown-workload-abc12") == "unknown-workload-abc12"
