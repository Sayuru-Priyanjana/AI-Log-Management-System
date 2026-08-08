import json
from app.llm.interface import LLMInterface
from app.models.investigation import InvestigationRequest, InvestigationPlan
from app.prompts.orchestrator import ORCHESTRATOR_SYSTEM_PROMPT

class OrchestratorAgent:
    def __init__(self, llm: LLMInterface):
        self.llm = llm

    async def create_plan(self, request: InvestigationRequest) -> InvestigationPlan:
        user_prompt = f"""
        System ID: {request.system_id}
        System Name: {request.system_name}
        Environment: {request.environment}
        
        Investigation Question: {request.question}
        
        Return the JSON plan.
        """
        
        try:
            response_text = await self.llm.generate(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                json_format=True
            )
            
            # Basic sanitization in case the LLM wrapped it in markdown
            response_text = response_text.strip()
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
                
            print(f"RAW LLM: {response_text}")
            raw_data = json.loads(response_text)
            
            # Enforce authoritative system details
            raw_data["system_id"] = request.system_id
            raw_data["environment"] = request.environment
            
            # Validate with Pydantic
            plan = InvestigationPlan(**raw_data)
            return plan
            
        except json.JSONDecodeError:
            raise ValueError("The LLM did not return a valid JSON format.")
        except Exception as e:
            raise ValueError(f"Failed to create investigation plan: {str(e)}")
