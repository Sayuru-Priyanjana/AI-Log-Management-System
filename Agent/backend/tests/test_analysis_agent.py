import pytest
import json
from app.agents.analysis.agent import AnalysisAgent
from app.llm.interface import LLMInterface
from app.models.investigation import InvestigationPlan, TimeRange
from app.correlation.models import CorrelatedEvidence
from app.agents.analysis.models import InvestigationAnalysis

class MockLLM(LLMInterface):
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0

    async def generate(self, system_prompt: str, user_prompt: str, json_format: bool = False) -> str:
        if self.call_count >= len(self.responses):
            raise Exception("Mock LLM ran out of responses")
        
        response = self.responses[self.call_count]
        self.call_count += 1
        
        if isinstance(response, Exception):
            raise response
        return response

@pytest.fixture
def sample_plan():
    return InvestigationPlan(
        intent="investigate_failure",
        system_id="test-system",
        environment="test-env",
        time_range=TimeRange(type="relative", duration="15m"),
        required_data=["application_logs"],
        investigation_goal="Find the error"
    )

@pytest.fixture
def sample_evidence():
    return CorrelatedEvidence(
        timeline=[],
        relationships=[],
        groups=[],
        signals=[],
        statistics={},
        investigation_window=TimeRange(type="relative", duration="15m")
    )

@pytest.mark.asyncio
async def test_agent_success(sample_plan, sample_evidence):
    valid_json = {
      "incident_detected": True,
      "severity": "high",
      "summary": "Database connectivity failure.",
      "incident_timeline": ["Event 1"],
      "likely_causes": [],
      "contributing_factors": [],
      "supporting_evidence": [],
      "conflicting_evidence": [],
      "missing_evidence": [],
      "recommended_next_steps": [],
      "overall_confidence": 0.9
    }
    mock_llm = MockLLM([json.dumps(valid_json)])
    agent = AnalysisAgent(llm=mock_llm)
    
    analysis, prompt = await agent.analyze(sample_plan, sample_evidence)
    assert isinstance(analysis, InvestigationAnalysis)
    assert analysis.severity == "high"

@pytest.mark.asyncio
async def test_agent_retry_success(sample_plan, sample_evidence):
    valid_json = {
      "incident_detected": True,
      "severity": "low",
      "summary": "Test",
      "incident_timeline": [],
      "likely_causes": [],
      "contributing_factors": [],
      "supporting_evidence": [],
      "conflicting_evidence": [],
      "missing_evidence": [],
      "recommended_next_steps": [],
      "overall_confidence": 0.5
    }
    # First response invalid, second response valid
    mock_llm = MockLLM(["invalid json", json.dumps(valid_json)])
    agent = AnalysisAgent(llm=mock_llm)
    
    analysis, prompt = await agent.analyze(sample_plan, sample_evidence)
    assert isinstance(analysis, InvestigationAnalysis)
    assert mock_llm.call_count == 2

@pytest.mark.asyncio
async def test_agent_max_retries_exceeded(sample_plan, sample_evidence):
    # Returns invalid JSON 3 times (initial + 2 retries)
    mock_llm = MockLLM(["invalid json", "still invalid", "nope"])
    agent = AnalysisAgent(llm=mock_llm)
    
    with pytest.raises(RuntimeError, match="Max retries exceeded"):
        await agent.analyze(sample_plan, sample_evidence)
    assert mock_llm.call_count == 3
