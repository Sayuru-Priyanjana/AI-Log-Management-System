import pytest
import pytest_asyncio
from app.models.investigation import InvestigationPlan, TimeRange
from app.dispatcher import InvestigationDispatcher
from app.tools.application_logs import ApplicationLogTool
from unittest.mock import AsyncMock

class MockOpenSearchClient:
    async def search(self, index, query, size=None):
        return {"hits": {"hits": []}}

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
async def test_dispatcher_success(plan):
    client = MockOpenSearchClient()
    dispatcher = InvestigationDispatcher(client)
    
    # Mock tools
    app_log_mock = AsyncMock(return_value=[])
    k8s_event_mock = AsyncMock(return_value=[])
    
    dispatcher.tools["application_logs"].execute = app_log_mock
    dispatcher.tools["kubernetes_events"].execute = k8s_event_mock
    
    evidence = await dispatcher.dispatch(plan)
    
    app_log_mock.assert_called_once_with(plan)
    k8s_event_mock.assert_called_once_with(plan)
    
    assert evidence.status["application_logs"] == "success"
    assert evidence.status["kubernetes_events"] == "success"
    assert evidence.application_logs == []
    assert evidence.kubernetes_events == []

@pytest.mark.asyncio
async def test_dispatcher_partial_failure(plan):
    client = MockOpenSearchClient()
    dispatcher = InvestigationDispatcher(client)
    
    app_log_mock = AsyncMock(return_value=[])
    k8s_event_mock = AsyncMock(side_effect=Exception("Connection Error"))
    
    dispatcher.tools["application_logs"].execute = app_log_mock
    dispatcher.tools["kubernetes_events"].execute = k8s_event_mock
    
    evidence = await dispatcher.dispatch(plan)
    
    assert evidence.status["application_logs"] == "success"
    assert "error: Connection Error" in evidence.status["kubernetes_events"]
    assert evidence.application_logs == []
