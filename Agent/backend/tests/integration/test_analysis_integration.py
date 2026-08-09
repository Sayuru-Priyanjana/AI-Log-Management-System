import pytest
import httpx
from app.agents.analysis.agent import AnalysisAgent
from app.llm.ollama import OllamaProvider
from app.models.investigation import InvestigationPlan, TimeRange
from app.correlation.models import CorrelatedEvidence, TimelineEvidence
from datetime import datetime
from app.config import settings

def is_ollama_running():
    try:
        response = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=2.0)
        if response.status_code == 200:
            models = [m["name"] for m in response.json().get("models", [])]
            return settings.OLLAMA_MODEL in models or f"{settings.OLLAMA_MODEL}:latest" in models
        return False
    except:
        return False

@pytest.mark.asyncio
@pytest.mark.skipif(not is_ollama_running(), reason="Ollama or required model is not available")
async def test_analysis_with_ollama():
    llm = OllamaProvider()
    agent = AnalysisAgent(llm=llm)
    
    plan = InvestigationPlan(
        intent="investigate_failure",
        system_id="ecommerce-platform",
        environment="production",
        service="payment-api",
        time_range=TimeRange(type="relative", duration="15m"),
        required_data=["application_logs", "kubernetes_events"],
        investigation_goal="Determine the cause of payment API failures."
    )
    
    evidence = CorrelatedEvidence(
        timeline=[
            TimelineEvidence(
                id="log-1",
                timestamp=datetime.utcnow(),
                source_type="application_log",
                system_id="ecommerce-platform",
                environment="production",
                title="Database timeout",
                message="Connection to database failed after 5000ms"
            )
        ],
        relationships=[],
        groups=[],
        signals=[],
        statistics={},
        investigation_window=TimeRange(type="relative", duration="15m")
    )
    
    analysis, prompt = await agent.analyze(plan, evidence)
    
    assert analysis.incident_detected is not None
    assert analysis.severity in ["low", "medium", "high", "critical", "unknown"]
    assert isinstance(analysis.likely_causes, list)
    assert analysis.overall_confidence > 0.0
