import pytest
from datetime import datetime, timezone
import pytest_asyncio
from app.models.investigation import InvestigationPlan, TimeRange
from app.tools.application_logs import ApplicationLogTool
from app.tools.kubernetes_events import KubernetesEventTool
from unittest.mock import AsyncMock

class MockOpenSearchClient:
    async def search(self, index, query, size=None):
        return {"hits": {"hits": []}}

@pytest.fixture
def os_client():
    client = MockOpenSearchClient()
    client.search = AsyncMock(return_value={"hits": {"hits": []}})
    return client

@pytest.fixture
def plan():
    return InvestigationPlan(
        intent="incident_investigation",
        system_id="test-system",
        environment="production",
        service="test-service",
        time_range=TimeRange(type="relative", duration="60m"),
        required_data=["application_logs", "kubernetes_events"],
        investigation_goal="test"
    )

@pytest.mark.asyncio
async def test_application_log_tool_query(os_client, plan):
    tool = ApplicationLogTool(os_client)
    tool.index = "test-index"
    
    await tool.execute(plan)
    
    os_client.search.assert_called_once()
    kwargs = os_client.search.call_args.kwargs
    assert kwargs["index"] == "test-index"
    query = kwargs["query"]["query"]["bool"]["filter"]
    
    # Check if system.id, environment, and service filters are in query
    terms = [q.get("term", {}) for q in query if "term" in q]
    assert {"system.id.keyword": "test-system"} in terms
    assert {"environment.keyword": "production"} in terms
    assert {"service.name.keyword": "test-service"} in terms

@pytest.mark.asyncio
async def test_kubernetes_event_tool_query(os_client, plan):
    tool = KubernetesEventTool(os_client)
    tool.index = "test-k8s-index"
    
    await tool.execute(plan)
    
    os_client.search.assert_called_once()
    kwargs = os_client.search.call_args.kwargs
    assert kwargs["index"] == "test-k8s-index"
    query = kwargs["query"]["query"]["bool"]["filter"]
    
    # Service filter should be a wildcard for k8s pod
    wildcards = [q.get("wildcard", {}) for q in query if "wildcard" in q]
    assert {"kubernetes.pod.name.keyword": "*test-service*"} in wildcards

def test_time_resolution():
    tool = ApplicationLogTool(MockOpenSearchClient())
    
    # Test relative 30m
    start, end = tool.resolve_time_range(TimeRange(type="relative", duration="30m"))
    diff = (end - start).total_seconds()
    assert 1799 < diff < 1801 # ~1800 seconds = 30 min
    
    # Test absolute time
    t_start = "2026-08-08T12:00:00Z"
    t_end = "2026-08-08T14:00:00Z"
    start, end = tool.resolve_time_range(TimeRange(type="absolute", start=t_start, end=t_end))
    assert start.isoformat() == "2026-08-08T12:00:00+00:00"
    assert end.isoformat() == "2026-08-08T14:00:00+00:00"
