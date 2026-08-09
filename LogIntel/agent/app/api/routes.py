from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings
from app.models.analysis import InvestigationResult
from app.models.plan import InvestigationRequest

logger = logging.getLogger(__name__)
router = APIRouter()


def deps(request: Request):
    return request.app.state.deps


@router.get("/health")
async def health(request: Request) -> dict:
    """Reports each dependency separately.

    A single overall boolean makes a half-connected setup look like a broken
    agent. Each component says what is wrong and where.
    """
    container = deps(request)
    report: dict = {"status": "ok", "components": {}}

    try:
        info = await container.opensearch.ping()
        report["components"]["opensearch"] = {
            "status": "ok",
            "url": container.opensearch.describe(),
            "version": info.get("version", {}).get("number"),
        }
        conflicts = await container.opensearch.check_mapping_conflicts()
        if conflicts:
            report["components"]["opensearch"] = {
                **report["components"]["opensearch"],
                "status": "degraded",
                "problems": conflicts,
            }
    except Exception as exc:
        report["components"]["opensearch"] = {
            "status": "unreachable", "url": container.opensearch.describe(), "error": str(exc)
        }

    try:
        ready = await container.prometheus.ready()
        report["components"]["prometheus"] = {
            "status": "ok" if ready else "unreachable",
            "url": container.prometheus.base_url,
        }
    except Exception as exc:
        report["components"]["prometheus"] = {"status": "unreachable", "error": str(exc)}

    try:
        available = await container.llm.available()
        report["components"]["ollama"] = {
            "status": "ok" if available else "degraded",
            "url": container.llm.base_url,
            "model": container.llm.model,
            "num_ctx": container.llm.num_ctx,
            "models_present": await container.llm.list_models(),
        }
        if not available:
            report["components"]["ollama"]["hint"] = (
                f"model '{container.llm.model}' is not pulled, or Ollama is not listening on "
                f"{container.llm.base_url} (set OLLAMA_HOST=0.0.0.0 on the Windows host)"
            )
    except Exception as exc:
        report["components"]["ollama"] = {"status": "unreachable", "error": str(exc)}

    try:
        reachable = await container.incidents.reachable()
        report["components"]["incident_controller"] = {
            "status": "ok" if reachable else "unreachable",
            "url": container.incidents.base_url,
        }
        if not reachable:
            report["components"]["incident_controller"]["hint"] = (
                "the testbed VM's incident injector is not answering — "
                "check 'vagrant status' in testbed/"
            )
    except Exception as exc:
        report["components"]["incident_controller"] = {"status": "unreachable", "error": str(exc)}

    try:
        systems = await container.registry.all()
        report["components"]["registry"] = {
            "status": "ok" if systems else "empty",
            "systems": [
                {"id": s.id, "name": s.name, "services": len(s.services),
                 "environments": s.environments}
                for s in systems
            ],
        }
    except Exception as exc:
        report["components"]["registry"] = {"status": "unreachable", "error": str(exc)}

    statuses = {c.get("status") for c in report["components"].values()}
    if "unreachable" in statuses:
        report["status"] = "degraded"
    elif {"degraded", "empty"} & statuses:
        report["status"] = "partial"
    return report


@router.get("/systems")
async def list_systems(request: Request) -> dict:
    container = deps(request)
    try:
        systems = await container.registry.all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"registry unavailable: {exc}") from exc
    return {"systems": [system.model_dump(mode="json") for system in systems]}


@router.post("/systems/refresh")
async def refresh_systems(request: Request) -> dict:
    container = deps(request)
    discovered = await container.registry.refresh()
    return {"refreshed": len(discovered), "systems": sorted(discovered)}


@router.get("/incidents")
async def list_incidents(request: Request) -> dict:
    """Proxies the testbed's incident catalogue.

    The browser never talks to the VM directly — the agent already has network
    access to it, so this keeps the UI to a single origin and means the incident
    controller's reachability shows up in /api/health like everything else.
    """
    container = deps(request)
    try:
        return await container.incidents.catalogue()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/incidents/{scenario_id}/start")
async def start_incident(scenario_id: str, request: Request) -> dict:
    container = deps(request)
    try:
        return await container.incidents.start(scenario_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/incidents/{scenario_id}/stop")
async def stop_incident(scenario_id: str, request: Request) -> dict:
    container = deps(request)
    try:
        return await container.incidents.stop(scenario_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/incidents/reset-all")
async def reset_incidents(request: Request) -> dict:
    container = deps(request)
    try:
        return await container.incidents.reset_all()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/investigations")
async def investigate(payload: InvestigationRequest, request: Request) -> StreamingResponse:
    container = deps(request)
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    async def stream():
        try:
            async for event in container.pipeline.run(payload):
                if event.stage == "result":
                    result = InvestigationResult(**event.data)
                    stored = await container.store.save(result)
                    event.data["persisted"] = stored
                yield json.dumps({"stage": event.stage, "data": event.data}, default=str) + "\n"
        except LookupError as exc:
            yield json.dumps({"stage": "error", "data": {"detail": str(exc), "kind": "unknown_system"}}) + "\n"
        except Exception as exc:
            logger.exception("Investigation failed")
            yield json.dumps({"stage": "error", "data": {"detail": str(exc),
                                                         "kind": type(exc).__name__}}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.get("/investigations")
async def recent_investigations(request: Request, limit: int = 20,
                                system_id: str | None = None) -> dict:
    container = deps(request)
    return {"investigations": await container.store.recent(limit=limit, system_id=system_id)}


@router.get("/investigations/{investigation_id}")
async def get_investigation(investigation_id: str, request: Request) -> dict:
    container = deps(request)
    stored = await container.store.get(investigation_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"no investigation '{investigation_id}'")
    return stored


@router.get("/config")
async def effective_config() -> dict:
    """The thresholds actually in force, so a surprising result can be traced to
    a setting rather than guessed at."""
    return {
        "windows": {
            "onset_bucket_seconds": settings.onset_bucket_seconds,
            "onset_mad_multiplier": settings.onset_mad_multiplier,
            "onset_min_absolute": settings.onset_min_absolute,
            "min_baseline_minutes": settings.min_baseline_minutes,
        },
        "thresholds": {
            "error_rate_spike_multiplier": settings.error_rate_spike_multiplier,
            "latency_degradation_multiplier": settings.latency_degradation_multiplier,
            "http_5xx_ratio_threshold": settings.http_5xx_ratio_threshold,
            "cpu_saturation_ratio": settings.cpu_saturation_ratio,
            "cpu_throttle_ratio": settings.cpu_throttle_ratio,
            "memory_pressure_ratio": settings.memory_pressure_ratio,
            "traffic_surge_multiplier": settings.traffic_surge_multiplier,
        },
        "llm": {
            "model": settings.ollama_model,
            "num_ctx": settings.ollama_num_ctx,
            "temperature": settings.ollama_temperature,
        },
        "budgets": {
            "max_log_patterns": settings.max_log_patterns,
            "max_events": settings.max_events,
            "max_prompt_patterns": settings.max_prompt_patterns,
            "max_prompt_events": settings.max_prompt_events,
        },
    }
