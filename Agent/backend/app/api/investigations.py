from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
import json
from app.models.investigation import InvestigationRequest, InvestigationPlan
from app.agents.orchestrator import OrchestratorAgent
from app.agents.analysis import AnalysisAgent
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

def get_analysis_agent() -> AnalysisAgent:
    llm_provider = OllamaProvider()
    return AnalysisAgent(llm=llm_provider)


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
    engine: CorrelationEngine = Depends(get_correlation_engine),
    analysis_agent: AnalysisAgent = Depends(get_analysis_agent)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Investigation question cannot be empty.")
        
    async def generate():
        try:
            # 1. Plan
            logger.info("Investigation run: planning phase started")
            plan = await orchestrator.create_plan(request)
            yield json.dumps({"step": "orchestrator", "data": plan.model_dump(mode='json')}) + "\n"
            
            # 2. Evidence
            logger.info("Investigation run: evidence collection phase started")
            evidence = await dispatcher.dispatch(plan)
            yield json.dumps({"step": "dispatcher", "data": evidence.model_dump(mode='json')}) + "\n"
            
            # 3. Correlate
            logger.info("Investigation run: correlation phase started")
            correlation = await engine.correlate(plan, evidence)
            yield json.dumps({"step": "correlation", "data": correlation.model_dump(mode='json')}) + "\n"
            
            # 4. Analyze
            logger.info("Investigation run: analysis phase started")
            analysis, prompt = await analysis_agent.analyze(plan, correlation)
            logger.info("Investigation run: complete")
            yield json.dumps({
                "step": "analysis", 
                "data": analysis.model_dump(mode='json'),
                "prompt": prompt
            }) + "\n"
            
        except ValueError as e:
            logger.error(f"Investigation run validation failed: {e}")
            yield json.dumps({"step": "error", "message": str(e)}) + "\n"
        except Exception as e:
            logger.error(f"Investigation run failed: {e}")
            yield json.dumps({"step": "error", "message": str(e)}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")
