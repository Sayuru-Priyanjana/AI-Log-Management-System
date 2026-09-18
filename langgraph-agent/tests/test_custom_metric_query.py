from __future__ import annotations

import pytest

from app.agents.tool_bindings import ToolBindings
from app.models.evidence import EvidenceBundle
from tests.conftest import T0


class PrometheusStub:
    def __init__(self):
        self.calls = []

    @staticmethod
    def step_for(window):
        return "30s"

    async def query_range(self, expression, window, step):
        self.calls.append((expression, window, step))
        return [{"metric": {"system_id": "shopdemo", "namespace": "shopdemo",
                            "service": "checkout-api"},
                 "values": [[T0.timestamp(), "3"], [T0.timestamp() + 30, "4"]]}]

    @staticmethod
    def to_points(series):
        from app.sources.prometheus import PrometheusClient
        return PrometheusClient.to_points(series)


@pytest.mark.asyncio
async def test_custom_metric_query_is_built_with_exact_system_and_incident_window(plan, windows):
    evidence = EvidenceBundle()
    prometheus = PrometheusStub()
    bindings = ToolBindings(plan, windows, evidence, prometheus_client=prometheus)

    result = await bindings.execute("query_prometheus", {
        "metric_name": "jvm_gc_pause_seconds_count", "operation": "rate",
        "service_name": "checkout-api", "group_by": "service",
    })
    expression, window, step = prometheus.calls[0]
    assert 'system_id="shopdemo"' in expression
    assert 'namespace="shopdemo"' in expression
    assert 'service="checkout-api"' in expression
    assert expression.startswith("sum by (service) (rate(jvm_gc_pause_seconds_count{")
    assert window is windows.incident
    assert step == "30s"
    assert result.evidence_ids[0] in {series.id for series in evidence.metrics.series}

    rejected = await bindings.execute("query_prometheus", {
        "metric_name": 'up{system_id=~".*"}',
    })
    assert "Invalid metric name" in rejected.text
    assert len(prometheus.calls) == 1
