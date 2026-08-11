from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.react import ReActAgent
from app.agents.orchestrator import OrchestratorAgent
from app.api.routes import router
from app.config import settings
from app.llm.ollama import OllamaClient
from app.pipeline.run import InvestigationPipeline
from app.registry.systems import SystemRegistry
from app.sources.incidents import IncidentControllerClient
from app.sources.opensearch import OpenSearchClient
from app.sources.prometheus import PrometheusClient
from app.store.investigations import InvestigationStore
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("logintel")


@dataclass
class Dependencies:
    opensearch: OpenSearchClient
    prometheus: PrometheusClient
    llm: OllamaClient
    registry: SystemRegistry
    store: InvestigationStore
    pipeline: InvestigationPipeline
    incidents: IncidentControllerClient


@asynccontextmanager
async def lifespan(app: FastAPI):
    opensearch = OpenSearchClient()
    prometheus = PrometheusClient()
    llm = OllamaClient()
    incidents = IncidentControllerClient()

    log_tool = LogTool(opensearch)
    registry = SystemRegistry(opensearch)

    app.state.deps = Dependencies(
        opensearch=opensearch,
        prometheus=prometheus,
        llm=llm,
        registry=registry,
        store=InvestigationStore(opensearch),
        incidents=incidents,
        pipeline=InvestigationPipeline(
            log_tool=log_tool,
            event_tool=EventTool(opensearch),
            metric_tool=MetricTool(prometheus),
            orchestrator=OrchestratorAgent(llm),
            react_agent=ReActAgent(llm),
            registry=registry,
            prometheus=prometheus,
        ),
    )

    # The agent owns the index schema. Applying the templates at startup means
    # the mapping assumptions the queries rely on are established by the same
    # component that relies on them.
    try:
        await opensearch.ensure_templates()
        for problem in await opensearch.check_mapping_conflicts():
            logger.warning("MAPPING CONFLICT: %s", problem)
    except Exception as exc:
        logger.warning("Could not apply index templates (%s); "
                       "check OpenSearch and restart", exc)

    try:
        systems = await registry.all()
        logger.info("Ready. Known systems: %s",
                    ", ".join(f"{s.id} ({len(s.services)} services)" for s in systems) or "none yet")
    except Exception as exc:
        logger.warning("Registry empty at startup (%s). It will fill in once logs arrive.", exc)

    logger.info("OpenSearch %s | Prometheus %s | Ollama %s model=%s num_ctx=%d",
                opensearch.describe(), prometheus.base_url, llm.base_url, llm.model, llm.num_ctx)

    yield

    await opensearch.close()
    await prometheus.close()
    await llm.close()
    await incidents.close()


app = FastAPI(
    title="LogIntel",
    description="Deterministic evidence extraction with a narrowly-scoped LLM on top.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def root() -> dict:
    return {
        "service": "LogIntel",
        "docs": "/docs",
        "health": "/api/health",
        "systems": "/api/systems",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
