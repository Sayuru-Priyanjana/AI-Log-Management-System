import pytest
from app.agents.orchestrator import OrchestratorAgent
from app.llm.interface import LLMInterface
from app.models.investigation import InvestigationRequest

class FakeLLM(LLMInterface):
    def __init__(self, json_response: str):
        self.json_response = json_response

    async def generate(self, system_prompt: str, user_prompt: str, json_format: bool = False) -> str:
        return self.json_response

@pytest.mark.asyncio
async def test_valid_llm_output_becomes_plan():
    llm = FakeLLM("""
    {
      "intent": "incident_investigation",
      "system_id": "ecommerce-platform",
      "environment": "production",
      "service": "payment-api",
      "time_range": {
        "type": "relative",
        "duration": "30m"
      },
      "required_data": ["application_logs", "kubernetes_events", "metrics"],
      "investigation_goal": "Determine why payment-api is failing."
    }
    """)
    orchestrator = OrchestratorAgent(llm)
    req = InvestigationRequest(
        system_id="ecommerce-platform", 
        system_name="E-Commerce Platform", 
        environment="production", 
        question="Why is payment-api failing?"
    )
    plan = await orchestrator.create_plan(req)
    
    assert plan.intent == "incident_investigation"
    assert plan.service == "payment-api"
    assert len(plan.required_data) == 3

@pytest.mark.asyncio
async def test_invalid_json_is_rejected():
    llm = FakeLLM("This is not JSON")
    orchestrator = OrchestratorAgent(llm)
    req = InvestigationRequest(
        system_id="ecommerce-platform", 
        system_name="E-Commerce Platform", 
        environment="production", 
        question="Why is payment-api failing?"
    )
    
    with pytest.raises(ValueError, match="The LLM did not return a valid JSON format"):
        await orchestrator.create_plan(req)

@pytest.mark.asyncio
async def test_invalid_required_data_values_rejected():
    llm = FakeLLM("""
    {
      "intent": "incident_investigation",
      "system_id": "ecommerce-platform",
      "environment": "production",
      "service": "payment-api",
      "time_range": {
        "type": "relative",
        "duration": "30m"
      },
      "required_data": ["made_up_data_source"],
      "investigation_goal": "Determine why payment-api is failing."
    }
    """)
    orchestrator = OrchestratorAgent(llm)
    req = InvestigationRequest(
        system_id="ecommerce-platform", 
        system_name="E-Commerce Platform", 
        environment="production", 
        question="Why is payment-api failing?"
    )
    
    with pytest.raises(ValueError, match="validation error"):
        await orchestrator.create_plan(req)

@pytest.mark.asyncio
async def test_selected_system_id_cannot_be_changed():
    # LLM tries to change the system to "inventory-platform"
    llm = FakeLLM("""
    {
      "intent": "incident_investigation",
      "system_id": "inventory-platform",
      "environment": "staging",
      "service": "payment-api",
      "time_range": {
        "type": "relative",
        "duration": "30m"
      },
      "required_data": ["application_logs"],
      "investigation_goal": "Determine why payment-api is failing."
    }
    """)
    orchestrator = OrchestratorAgent(llm)
    req = InvestigationRequest(
        system_id="ecommerce-platform", 
        system_name="E-Commerce Platform", 
        environment="production", 
        question="Why is payment-api failing?"
    )
    
    plan = await orchestrator.create_plan(req)
    
    # The orchestrator MUST enforce the user's system_id and environment
    assert plan.system_id == "ecommerce-platform"
    assert plan.environment == "production"
