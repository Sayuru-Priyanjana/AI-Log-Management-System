from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.react import ReActAgent
from app.agents.orchestrator import OrchestratorAgent
from app.api.routes import router
from app.api.settings_routes import router as settings_router
from app.api.system_settings_routes import router as system_settings_router
from app.config import settings
from app.llm.base import LLMClient
from app.llm.factory import (
    build_llm, describe_endpoint, describe_model, describe_provider,
)
from app.pipeline.run import InvestigationPipeline
from app.registry.systems import SystemRegistry
from app.sources.opensearch import OpenSearchClient
from app.sources.prometheus import PrometheusClient
from app.store.investigations import InvestigationStore
from app.store.runtime_config import RuntimeConfig
from app.store.system_settings import SystemSettingsStore
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool
from app.util.timefmt import label as zone_label

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("logintel")


@dataclass
class Dependencies:
    opensearch: OpenSearchClient
    llm: LLMClient
    registry: SystemRegistry
    store: InvestigationStore
    pipeline: InvestigationPipeline
    prometheus: PrometheusClient
    config: RuntimeConfig
    system_settings: SystemSettingsStore


def build_dependencies(config: RuntimeConfig) -> Dependencies:
    """Constructs every client from whatever the settings currently say.

    Separated from startup so the configuration page can call it again. Changing
    where OpenSearch lives has to rebuild the client that points at it — the
    alternative is a restart, which is a poor answer to "I typed the wrong URL".
    """
    opensearch = OpenSearchClient()
    llm = build_llm()
    prometheus = PrometheusClient(settings.prometheus_url)
    registry = SystemRegistry(opensearch)
    system_settings = SystemSettingsStore(opensearch)

    return Dependencies(
        opensearch=opensearch,
        llm=llm,
        registry=registry,
        store=InvestigationStore(opensearch),
        prometheus=prometheus,
        config=config,
        system_settings=system_settings,
        pipeline=InvestigationPipeline(
            log_tool=LogTool(opensearch),
            event_tool=EventTool(opensearch),
            orchestrator=OrchestratorAgent(llm),
            react_agent=ReActAgent(llm),
            registry=registry,
            system_settings=system_settings,
            prometheus_client=prometheus,
        ),
    )


async def close_dependencies(deps: Dependencies) -> None:
    for client in (deps.opensearch, deps.llm, deps.prometheus):
        try:
            await client.close()
        except Exception as exc:            # noqa: BLE001 - shutdown is best-effort
            logger.debug("Error closing %s: %s", type(client).__name__, exc)


async def rebuild_dependencies(app: FastAPI) -> None:
    """Swaps in fresh clients, then closes the old ones.

    In that order: a request already streaming holds its own references, so
    closing first would abort an investigation mid-flight to apply a setting
    that has nothing to do with it.
    """
    previous: Dependencies = app.state.deps
    fresh = build_dependencies(previous.config)
    previous.config.rebind(fresh.opensearch)
    app.state.deps = fresh
    await close_dependencies(previous)
    logger.info("Rebuilt clients after a settings change")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Saved overrides are read through a throwaway client and applied to the
    # settings *before* the real ones are built, so the first OpenSearchClient
    # already points wherever the configuration page last said.
    bootstrap = OpenSearchClient()
    config = RuntimeConfig(bootstrap)
    await config.load()
    await bootstrap.close()

    app.state.deps = build_dependencies(config)
    deps = app.state.deps
    opensearch, llm, registry = (
        deps.opensearch, deps.llm, deps.registry)
    config.rebind(opensearch)

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

    logger.info("OpenSearch %s | model %s via %s at %s | times shown in %s",
                opensearch.describe(), describe_model(llm),
                describe_provider(llm), describe_endpoint(llm), zone_label())

    yield

    await close_dependencies(app.state.deps)


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
app.include_router(settings_router, prefix="/api")
app.include_router(system_settings_router, prefix="/api")


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
