import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime
from app.tools.metrics import MetricsTool
from app.prometheus.client import PrometheusClient
from app.models.investigation import InvestigationPlan, TimeRange

@pytest.fixture
def mock_prom_client():
    client = MagicMock(spec=PrometheusClient)
    client.query_range = AsyncMock()
    return client

@pytest.fixture
def metrics_tool(mock_prom_client):
    return MetricsTool(mock_prom_client)

@pytest.mark.asyncio
async def test_metrics_tool_execution(metrics_tool, mock_prom_client):
    plan = InvestigationPlan(
        intent="incident_investigation",
        system_id="test-system",
        environment="prod",
        service="test-svc",
        time_range=TimeRange(type="relative", duration="30m"),
        required_data=["metrics"],
        investigation_goal="Test metrics tool"
    )
    
    mock_prom_client.query_range.return_value = {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"pod": "test-pod-1"},
                "values": [
                    [1672574400.0, "1.5"],
                    [1672574460.0, "2.0"]
                ]
            }
        ]
    }
    
    evidence, queries = await metrics_tool.execute(plan)
    
    assert len(evidence) == 4 # CPU, Memory, Restarts, Ready
    assert "pod_cpu_usage" in queries
    
    # Check that summary is calculated
    cpu_evidence = next(e for e in evidence if e.metric_name == "pod_cpu_usage")
    assert cpu_evidence.status == "success"
    assert len(cpu_evidence.samples) == 2
    assert cpu_evidence.summary is not None
    assert cpu_evidence.summary.average == 1.75
    assert cpu_evidence.summary.maximum == 2.0
    assert cpu_evidence.summary.minimum == 1.5

@pytest.mark.asyncio
async def test_metrics_tool_empty_result(metrics_tool, mock_prom_client):
    plan = InvestigationPlan(
        intent="incident_investigation",
        system_id="test-system",
        environment="prod",
        service="test-svc",
        time_range=TimeRange(type="relative", duration="30m"),
        required_data=["metrics"],
        investigation_goal="Test metrics tool empty"
    )
    
    mock_prom_client.query_range.return_value = {
        "resultType": "matrix",
        "result": []
    }
    
    evidence, queries = await metrics_tool.execute(plan)
    
    for e in evidence:
        assert e.status == "unavailable"
        assert e.reason == "No data found in Prometheus for this metric."
