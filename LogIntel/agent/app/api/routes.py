from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.tool_bindings import ToolBindings
from app.config import settings
from app.llm.factory import (
    describe_endpoint, describe_model, describe_provider,
)
from app.models.analysis import InvestigationResult, InvestigationWindows
from app.models.plan import InvestigationPlan, InvestigationRequest
from app.pipeline.signals import SignalEngine

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

    # Reported under one key whichever backend is configured, so the UI does not
    # have to know which provider is in use to show whether the model is reachable.
    try:
        provider = describe_provider(container.llm)
        available = await container.llm.available()
        component = {
            "status": "ok" if available else "degraded",
            "provider": provider,
            "url": describe_endpoint(container.llm),
            "model": describe_model(container.llm),
        }
        if provider == "ollama":
            component["num_ctx"] = container.llm.num_ctx
            component["models_present"] = await container.llm.list_models()
            if not available:
                component["hint"] = (
                    f"model '{container.llm.model}' is not pulled, or Ollama is not listening "
                    f"on {container.llm.base_url}. Ollama is an external service: start it with "
                    f"OLLAMA_HOST=0.0.0.0 so this agent can reach it."
                )
        elif not available:
            component["hint"] = (
                f"the {provider} endpoint did not authenticate — check LLM_API_KEY, "
                f"LLM_BASE_URL and that LLM_MODEL exists on that account"
            )
        report["components"]["model"] = component
    except Exception as exc:
        report["components"]["model"] = {"status": "unreachable", "error": str(exc)}

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

@router.delete("/investigations/{investigation_id}")
async def delete_investigation(investigation_id: str, request: Request) -> dict:
    container = deps(request)
    deleted = await container.store.delete(investigation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"no investigation '{investigation_id}' or could not delete")
    return {"success": True}


class RunToolRequest(BaseModel):
    tool: str
    tool_input: dict = Field(default_factory=dict)


@router.post("/investigations/{investigation_id}/run-tool")
async def run_tool(investigation_id: str, payload: RunToolRequest,
                   request: Request) -> dict:
    """Runs one tool against a finished investigation's evidence.

    This is what makes a suggested next step actionable. It rebuilds the evidence
    from the plan and windows the investigation stored, so the answer comes back
    in a second or two with no model call and no re-reasoning — the reader is
    looking at the same window the conclusion was drawn from, which is the whole
    point of following up on it.
    """
    container = deps(request)
    stored = await container.store.get(investigation_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=f"no investigation '{investigation_id}'")

    if payload.tool not in {spec.name for spec in ToolBindings.SPECS}:
        raise HTTPException(
            status_code=400,
            detail=f"unknown tool '{payload.tool}'. Available: "
                   f"{', '.join(sorted(s.name for s in ToolBindings.SPECS))}",
        )

    try:
        plan = InvestigationPlan(**stored["plan"])
        windows = InvestigationWindows(**stored["windows"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422,
                            detail=f"stored investigation is not replayable: {exc}") from exc

    pipeline = container.pipeline
    evidence = await pipeline._collect(plan, windows, [])       # noqa: SLF001
    signals = SignalEngine(known_services=[]).detect(plan, windows, evidence)
    candidates = pipeline.hypotheses.generate(plan, windows, signals, evidence)

    bindings = ToolBindings(plan, windows, evidence, signals, candidates)
    result = bindings.execute(payload.tool, payload.tool_input)

    return {
        "investigation_id": investigation_id,
        "tool": payload.tool,
        "tool_input": payload.tool_input,
        "observation": result.text,
        "evidence_ids": result.evidence_ids,
        "table": result.table,
        "window": windows.incident.model_dump(mode="json"),
    }


@router.get("/config")
async def effective_config() -> dict:
    """The thresholds actually in force, so a surprising result can be traced to
    a setting rather than guessed at."""
    return {
        "windows": {
            "onset_bucket_seconds": settings.onset_bucket_seconds,
            "onset_mad_multiplier": settings.onset_mad_multiplier,
            "onset_min_absolute": settings.onset_min_absolute,
            "onset_min_elevation": settings.onset_min_elevation,
            "onset_lookback_multiplier": settings.onset_lookback_multiplier,
            "min_baseline_minutes": settings.min_baseline_minutes,
            "incident_pre_roll_seconds": settings.incident_pre_roll_seconds,
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
            "provider": settings.llm_provider,
            "model": settings.llm_model or settings.ollama_model,
            "temperature": settings.llm_temperature,
            "num_ctx": settings.ollama_num_ctx,
        },
        "budgets": {
            "max_log_patterns": settings.max_log_patterns,
            "max_events": settings.max_events,
            "max_prompt_patterns": settings.max_prompt_patterns,
            "max_prompt_events": settings.max_prompt_events,
        },
    }
