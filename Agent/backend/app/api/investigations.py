from fastapi import APIRouter, HTTPException, Depends
from app.models.investigation import InvestigationRequest, InvestigationPlan
from app.agents.orchestrator import OrchestratorAgent
from app.llm.ollama import OllamaProvider
from app.dispatcher import InvestigationDispatcher
from app.correlation.engine import CorrelationEngine
from app.opensearch.client import OpenSearchClient
from app.prometheus.client import PrometheusClient
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

def get_orchestrator() -> OrchestratorAgent:
    llm_provider = OllamaProvider()
    return OrchestratorAgent(llm=llm_provider)

def get_opensearch_client() -> OpenSearchClient:
    return OpenSearchClient()

def get_prometheus_client() -> PrometheusClient:
    return PrometheusClient()

def get_dispatcher(
    os_client: OpenSearchClient = Depends(get_opensearch_client),
    prom_client: PrometheusClient = Depends(get_prometheus_client)
) -> InvestigationDispatcher:
    return InvestigationDispatcher(os_client, prom_client)

def get_correlation_engine() -> CorrelationEngine:
    return CorrelationEngine()


@router.post("/investigations/plan")
async def create_investigation_plan(
    request: InvestigationRequest,
    orchestrator: OrchestratorAgent = Depends(get_orchestrator)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Investigation question cannot be empty.")
        
    try:
        logger.info("Investigation plan generation started")
        logger.info(f"System: {request.system_id}")
        logger.info(f"Environment: {request.environment}")
        
        plan = await orchestrator.create_plan(request)
        
        logger.info(f"Service: {plan.service}")
        logger.info(f"Time range: {plan.time_range.duration or plan.time_range.start}")
        logger.info(f"Required data: {', '.join(plan.required_data)}")
        
        return {"plan": plan.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Investigation plan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/investigations/evidence")
async def gather_investigation_evidence(
    plan: InvestigationPlan,
    dispatcher: InvestigationDispatcher = Depends(get_dispatcher)
):
    try:
        logger.info("Investigation evidence collection started")
        evidence = await dispatcher.dispatch(plan)
        logger.info("Investigation evidence collection completed")
        
        return {"evidence": evidence.model_dump()}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Investigation evidence failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/investigations/run")
async def run_full_investigation(
    request: InvestigationRequest,
    orchestrator: OrchestratorAgent = Depends(get_orchestrator),
    dispatcher: InvestigationDispatcher = Depends(get_dispatcher),
    engine: CorrelationEngine = Depends(get_correlation_engine)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Investigation question cannot be empty.")
        
    try:
        # 1. Plan
        logger.info("Investigation run: planning phase started")
        plan = await orchestrator.create_plan(request)
        
        # 2. Evidence
        logger.info("Investigation run: evidence collection phase started")
        evidence = await dispatcher.dispatch(plan)
        
        # 3. Correlate
        logger.info("Investigation run: correlation phase started")
        correlation = await engine.correlate(plan, evidence)
        logger.info("Investigation run: complete")
        
        return {
            "plan": plan.model_dump(),
            "evidence": evidence.model_dump(),
            "correlation": correlation.model_dump()
        }
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Investigation run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
