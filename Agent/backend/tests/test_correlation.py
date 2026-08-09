import pytest
from datetime import datetime, timezone, timedelta
from app.correlation.normalizer import EvidenceNormalizer
from app.correlation.timeline import TimelineBuilder
from app.correlation.matcher import EvidenceMatcher
from app.correlation.signals import SignalDetector
from app.correlation.engine import CorrelationEngine
from app.models.evidence import ApplicationLogEvidence, KubernetesEventEvidence, MetricEvidence, MetricSample, MetricSummary
from app.models.investigation import InvestigationPlan, TimeRange

@pytest.fixture
def sample_logs():
    return [
        ApplicationLogEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 1, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            service_name="payment-api",
            pod_name="payment-api-abc",
            level="ERROR",
            message="Database connection timeout",
            event_category="database",
            event_action="query"
        ),
        ApplicationLogEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 2, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            service_name="payment-api",
            pod_name="payment-api-abc",
            level="ERROR",
            message="Database connection timeout"
        ),
        ApplicationLogEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 3, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            service_name="payment-api",
            pod_name="payment-api-abc",
            level="ERROR",
            message="Database connection timeout"
        ),
        ApplicationLogEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 4, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            service_name="payment-api",
            pod_name="payment-api-abc",
            level="ERROR",
            message="Database connection timeout"
        ),
        ApplicationLogEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 5, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            service_name="payment-api",
            pod_name="payment-api-abc",
            level="ERROR",
            message="Database connection timeout"
        )
    ]

@pytest.fixture
def sample_events():
    return [
        KubernetesEventEvidence(
            timestamp=datetime(2026, 8, 9, 10, 5, 8, tzinfo=timezone.utc),
            system_id="ecommerce-platform",
            environment="production",
            namespace="payment",
            pod_name="payment-api-abc",
            event_type="Warning",
            action="BackOff",
            reason="BackOff",
            message="Back-off restarting failed container"
        )
    ]

@pytest.fixture
def sample_metrics():
    return [
        MetricEvidence(
            metric_name="pod_cpu_usage",
            metric_type="gauge",
            unit="percent",
            status="success",
            labels={"pod": "payment-api-abc", "namespace": "payment"},
            samples=[
                MetricSample(timestamp=datetime(2026, 8, 9, 10, 0, 0, tzinfo=timezone.utc), value=40.0),
                MetricSample(timestamp=datetime(2026, 8, 9, 10, 1, 0, tzinfo=timezone.utc), value=42.0),
                MetricSample(timestamp=datetime(2026, 8, 9, 10, 4, 0, tzinfo=timezone.utc), value=92.0),
                MetricSample(timestamp=datetime(2026, 8, 9, 10, 5, 10, tzinfo=timezone.utc), value=95.0)
            ],
            summary=MetricSummary(average=67.25, maximum=95.0, minimum=40.0)
        )
    ]


def test_normalization(sample_logs, sample_events, sample_metrics):
    timeline = EvidenceNormalizer.normalize_all(
        sample_logs, sample_events, sample_metrics,
        "ecommerce-platform", "production"
    )
    
    assert len(timeline) == 5 + 1 + 4
    
    # Check log
    log_evidence = [e for e in timeline if e.source_type == "application_log"][0]
    assert log_evidence.severity == "ERROR"
    assert log_evidence.service_name == "payment-api"
    
    # Check event
    event_evidence = [e for e in timeline if e.source_type == "kubernetes_event"][0]
    assert event_evidence.severity == "HIGH"
    assert event_evidence.pod_name == "payment-api-abc"
    
    # Check metric
    metric_evidence = [e for e in timeline if e.source_type == "metric"][0]
    assert metric_evidence.metric_name == "pod_cpu_usage"
    assert metric_evidence.metric_value == 40.0


def test_timeline_sorting(sample_logs):
    # Mess up the order
    messed_up = [sample_logs[2], sample_logs[0], sample_logs[1]]
    timeline = EvidenceNormalizer.normalize_all(messed_up, [], [], "ecommerce-platform", "production")
    
    sorted_timeline = TimelineBuilder.build_timeline(timeline)
    
    assert sorted_timeline[0].timestamp <= sorted_timeline[1].timestamp
    assert sorted_timeline[1].timestamp <= sorted_timeline[2].timestamp


def test_matcher_infrastructure_and_temporal(sample_logs, sample_events):
    timeline = EvidenceNormalizer.normalize_all(
        [sample_logs[0]], sample_events, [],
        "ecommerce-platform", "production"
    )
    
    sorted_timeline = TimelineBuilder.build_timeline(timeline)
    relationships = EvidenceMatcher.find_relationships(sorted_timeline)
    
    assert len(relationships) == 1
    rel = relationships[0]
    
    # Should have matched on pod_name
    assert "same_pod" in rel.reasons
    assert rel.score >= 5.0
    
    # Should have matched temporally (10:05:01 and 10:05:08 -> 7 seconds apart)
    assert rel.time_delta_seconds == 7
    assert "within_15_seconds" in rel.reasons


def test_signal_detector(sample_logs, sample_events, sample_metrics):
    timeline = EvidenceNormalizer.normalize_all(
        sample_logs, sample_events, sample_metrics,
        "ecommerce-platform", "production"
    )
    sorted_timeline = TimelineBuilder.build_timeline(timeline)
    
    signals = SignalDetector.detect_signals(sorted_timeline, sample_metrics)
    
    # Should detect: ERROR_BURST, KUBERNETES_BACKOFF, CPU_SPIKE
    signal_types = [s.type for s in signals]
    assert "ERROR_BURST" in signal_types
    assert "KUBERNETES_BACKOFF" in signal_types
    assert "CPU_SPIKE" in signal_types
    
    burst_signal = [s for s in signals if s.type == "ERROR_BURST"][0]
    assert burst_signal.count == 5
    assert burst_signal.service == "payment-api"
    
    cpu_signal = [s for s in signals if s.type == "CPU_SPIKE"][0]
    assert cpu_signal.peak == 95.0


@pytest.mark.asyncio
async def test_correlation_engine(sample_logs, sample_events, sample_metrics):
    from app.models.investigation import InvestigationPlan, TimeRange
    from app.models.evidence import InvestigationEvidence
    
    engine = CorrelationEngine()
    
    plan = InvestigationPlan(
        intent="TEST",
        system_id="ecommerce-platform",
        environment="production",
        time_range=TimeRange(type="relative", duration="1h"),
        required_data=["application_logs", "kubernetes_events", "metrics"],
        investigation_goal="Test correlation"
    )
    
    evidence = InvestigationEvidence(
        application_logs=sample_logs,
        kubernetes_events=sample_events,
        metrics=sample_metrics
    )
    
    correlated = await engine.correlate(plan, evidence)
    
    assert len(correlated.timeline) == 10
    assert len(correlated.signals) == 3
    assert len(correlated.relationships) > 0
    assert len(correlated.groups) == 1
    
    assert correlated.statistics["total_logs"] == 5
    assert correlated.statistics["total_signals_detected"] == 3
