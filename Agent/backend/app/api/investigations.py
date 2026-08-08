from fastapi import APIRouter, HTTPException, Depends
from app.models.investigation import InvestigationRequest, InvestigationPlan
from app.agents.orchestrator import OrchestratorAgent
from app.llm.ollama import OllamaProvider

router = APIRouter()

def get_orchestrator() -> OrchestratorAgent:
    # Dependency injection for the Orchestrator
    llm_provider = OllamaProvider()
    return OrchestratorAgent(llm=llm_provider)

@router.post("/investigations")
async def create_investigation(
    request: InvestigationRequest,
    orchestrator: OrchestratorAgent = Depends(get_orchestrator)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Investigation question cannot be empty.")
        
    try:
        plan = await orchestrator.create_plan(request)
        return {"plan": plan.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
