from __future__ import annotations

import asyncio
import json
import re
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi_cache.decorator import cache
from pydantic import BaseModel, Field

from app.agents.tool_bindings import ToolBindings
from app.config import settings
from app.llm.factory import (
    describe_endpoint, describe_model, describe_provider,
)
from datetime import datetime, timezone
from app.models.domain import TimeWindow
from app.models.analysis import InvestigationResult, InvestigationWindows
from app.models.plan import InvestigationPlan, InvestigationRequest
from app.pipeline.signals import SignalEngine

logger = logging.getLogger(__name__)
router = APIRouter()


def deps(request: Request):
    return request.app.state.deps


async def check_opensearch(container, report):
    try:
        info = await asyncio.wait_for(container.opensearch.ping(), timeout=3.0)
        report["components"]["opensearch"] = {
            "status": "ok",
            "url": container.opensearch.describe(),
            "version": info.get("version", {}).get("number"),
        }
        conflicts = await asyncio.wait_for(container.opensearch.check_mapping_conflicts(), timeout=3.0)
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


async def check_model(container, report):
    try:
        provider = describe_provider(container.llm)
        available = await asyncio.wait_for(container.llm.available(), timeout=5.0)
        component = {
            "status": "ok" if available else "degraded",
            "provider": provider,
            "url": describe_endpoint(container.llm),
            "model": describe_model(container.llm),
        }
        if provider == "ollama":
            component["num_ctx"] = container.llm.num_ctx
            component["models_present"] = await asyncio.wait_for(container.llm.list_models(), timeout=5.0)
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

async def check_prometheus(container, report):
    try:
        reachable = await asyncio.wait_for(container.prometheus.ready(), timeout=3.0)
        report["components"]["prometheus"] = {
            "status": "ok" if reachable else "unreachable",
            "url": container.prometheus.base_url,
        }
        if not reachable:
            report["components"]["prometheus"]["hint"] = (
                "the central Prometheus server is not answering — "
                "check its status or update the URL in configuration."
            )
    except Exception as exc:
        report["components"]["prometheus"] = {"status": "unreachable", "error": str(exc)}

async def check_registry(container, report):
    try:
        systems = await asyncio.wait_for(container.registry.all(), timeout=3.0)
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

@router.get("/health")
async def health(request: Request) -> dict:
    """Reports each dependency separately."""
    container = deps(request)
    report: dict = {"status": "ok", "components": {}}

    await asyncio.gather(
        check_opensearch(container, report),
        check_model(container, report),
        check_prometheus(container, report),
        check_registry(container, report)
    )

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

    from app.tools.metrics import MetricTool
    try:
        pipeline = container.pipeline
        evidence = await pipeline._collect(plan, windows, [], MetricTool(pipeline.prometheus_client))       # noqa: SLF001
        signals = SignalEngine(known_services=[]).detect(plan, windows, evidence)
        candidates = pipeline.hypotheses.generate(plan, windows, signals, evidence)

        # The log tool is passed so a replayed next step can use the live query
        # tools too — a suggestion like "look at what payment-db logged at 14:29"
        # is only actionable if the button behind it can actually go and look.
        bindings = ToolBindings(plan, windows, evidence, signals, candidates,
                                log_tool=pipeline.logs)
        result = await bindings.execute(payload.tool, payload.tool_input)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

@router.get("/systems/{system_id}/metrics/requests")
@cache(expire=300)
async def get_system_metrics_requests(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    window = TimeWindow(start=datetime.fromtimestamp(start, tz=timezone.utc), end=datetime.fromtimestamp(end, tz=timezone.utc))
    step = container.prometheus.step_for(window)
    expression = f'sum by (container) (rate(container_cpu_usage_seconds_total{{system_id="{system_id}", container!="", container!="POD"}}[2m]))'
    raw_series = await container.prometheus.query_range(expression, window, step)
    
    data_by_time = {}
    for series in raw_series:
        svc = series.get("metric", {}).get("container", "unknown")
        points = container.prometheus.to_points(series)
        for pt_time, val in points:
            ts = int(pt_time.timestamp()) * 1000
            if ts not in data_by_time:
                data_by_time[ts] = {"time": ts}
            data_by_time[ts][svc] = round(val, 2)
            
    sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
    return sorted_data

@router.get("/systems/{system_id}/metrics/ram")
@cache(expire=300)
async def get_system_metrics_ram(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    window = TimeWindow(start=datetime.fromtimestamp(start, tz=timezone.utc), end=datetime.fromtimestamp(end, tz=timezone.utc))
    step = container.prometheus.step_for(window)
    expression = f'sum by (container) (container_memory_working_set_bytes{{system_id="{system_id}", container!="", container!="POD"}})'
    raw_series = await container.prometheus.query_range(expression, window, step)
    
    data_by_time = {}
    for series in raw_series:
        svc = series.get("metric", {}).get("container", "unknown")
        points = container.prometheus.to_points(series)
        for pt_time, val in points:
            ts = int(pt_time.timestamp()) * 1000
            if ts not in data_by_time:
                data_by_time[ts] = {"time": ts}
            # Convert bytes to MB for easier reading on the chart
            data_by_time[ts][svc] = round(val / (1024 * 1024), 2)
            
    sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
    return sorted_data

@router.get("/systems/{system_id}/metrics/logs")
@cache(expire=300)
async def get_system_metrics_logs(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    
    range_hours = (end - start) / 3600
    if range_hours <= 1:
        interval = "1m"
    elif range_hours <= 6:
        interval = "5m"
    elif range_hours <= 24:
        interval = "15m"
    elif range_hours <= 72:
        interval = "1h"
    else:
        interval = "4h"

    query = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}},
                    {"range": {"@timestamp": {"gte": start * 1000, "lte": end * 1000, "format": "epoch_millis"}}}
                ]
            }
        },
        "aggs": {
            "services": {
                "terms": {"field": "service.name", "size": 1000},
                "aggs": {
                    "logs_over_time": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                            "extended_bounds": {"min": start * 1000, "max": end * 1000}
                        }
                    }
                }
            }
        }
    }
    
    try:
        result = await container.opensearch.search(settings.opensearch_log_index, query)
        
        data_by_time = {}
        buckets = result.get("aggregations", {}).get("services", {}).get("buckets", [])
        for bucket in buckets:
            svc = bucket.get("key", "unknown")
            time_buckets = bucket.get("logs_over_time", {}).get("buckets", [])
            for tb in time_buckets:
                ts = tb.get("key")
                val = tb.get("doc_count", 0)
                if ts not in data_by_time:
                    data_by_time[ts] = {"time": ts}
                data_by_time[ts][svc] = val
                
        sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
        return sorted_data
    except Exception as exc:
        logger.error(f"Failed to fetch log metrics: {exc}")
        return []

@router.get("/systems/{system_id}/metrics/error_logs")
@cache(expire=300)
async def get_system_metrics_error_logs(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    
    range_hours = (end - start) / 3600
    if range_hours <= 1:
        interval = "1m"
    elif range_hours <= 6:
        interval = "5m"
    elif range_hours <= 24:
        interval = "15m"
    elif range_hours <= 72:
        interval = "1h"
    else:
        interval = "4h"

    query = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}},
                    {"range": {"@timestamp": {"gte": start * 1000, "lte": end * 1000, "format": "epoch_millis"}}}
                ],
                "should": [
                    {"match": {"level": "error"}},
                    {"match": {"level": "ERROR"}},
                    {"match": {"log.level": "error"}},
                    {"match": {"status": "ERROR"}},
                    {"match": {"message": "error"}},
                    {"match": {"message": "fatal"}},
                    {"match": {"message": "critical"}},
                    {"match": {"log.message": "error"}},
                    {"match": {"log.message": "fatal"}},
                    {"match": {"log.message": "critical"}}
                ],
                "minimum_should_match": 1
            }
        },
        "aggs": {
            "services": {
                "terms": {"field": "service.name", "size": 100},
                "aggs": {
                    "errors_over_time": {
                        "date_histogram": {
                            "field": "@timestamp",
                            "fixed_interval": interval,
                            "min_doc_count": 0,
                            "extended_bounds": {"min": start * 1000, "max": end * 1000}
                        }
                    }
                }
            }
        }
    }
    
    try:
        result = await container.opensearch.search(settings.opensearch_log_index, query)
        
        data_by_time = {}
        buckets = result.get("aggregations", {}).get("services", {}).get("buckets", [])
        for bucket in buckets:
            svc = bucket.get("key", "unknown")
            time_buckets = bucket.get("errors_over_time", {}).get("buckets", [])
            for tb in time_buckets:
                ts = tb.get("key")
                val = tb.get("doc_count", 0)
                if ts not in data_by_time:
                    data_by_time[ts] = {"time": ts}
                data_by_time[ts][svc] = val
                
        sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
        return sorted_data
    except Exception as exc:
        logger.error(f"Failed to fetch error log metrics: {exc}")
        return []

@router.get("/systems/{system_id}/metrics/restarts")
@cache(expire=300)
async def get_system_metrics_restarts(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    window = TimeWindow(start=datetime.fromtimestamp(start, tz=timezone.utc), end=datetime.fromtimestamp(end, tz=timezone.utc))
    step = container.prometheus.step_for(window)
    expression = f'sum by (container) (kube_pod_container_status_restarts_total{{system_id="{system_id}", container!="", container!="POD"}})'
    raw_series = await container.prometheus.query_range(expression, window, step)
    
    data_by_time = {}
    for series in raw_series:
        svc = series.get("metric", {}).get("container", "unknown")
        points = container.prometheus.to_points(series)
        for pt_time, val in points:
            ts = int(pt_time.timestamp()) * 1000
            if ts not in data_by_time:
                data_by_time[ts] = {"time": ts}
            data_by_time[ts][svc] = int(val)
            
    sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
    return sorted_data

@router.get("/systems/{system_id}/metrics/throttling")
@cache(expire=300)
async def get_system_metrics_throttling(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    window = TimeWindow(start=datetime.fromtimestamp(start, tz=timezone.utc), end=datetime.fromtimestamp(end, tz=timezone.utc))
    step = container.prometheus.step_for(window)
    expression = f'sum by (container) (rate(container_cpu_cfs_throttled_seconds_total{{system_id="{system_id}", container!="", container!="POD"}}[2m]))'
    raw_series = await container.prometheus.query_range(expression, window, step)
    
    data_by_time = {}
    for series in raw_series:
        svc = series.get("metric", {}).get("container", "unknown")
        points = container.prometheus.to_points(series)
        for pt_time, val in points:
            ts = int(pt_time.timestamp()) * 1000
            if ts not in data_by_time:
                data_by_time[ts] = {"time": ts}
            data_by_time[ts][svc] = round(val, 3)
            
    sorted_data = [data_by_time[k] for k in sorted(data_by_time.keys())]
    return sorted_data


@router.get("/systems/{system_id}/snapshot")
async def get_system_snapshot(system_id: str, request: Request) -> dict:
    container = deps(request)
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - 86400
    
    count_query = {
        "size": 0,
        "track_total_hits": True,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}},
                    {"range": {"@timestamp": {"gte": start * 1000, "lte": end * 1000, "format": "epoch_millis"}}}
                ]
            }
        }
    }
    
    uptime_query = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}}
                ]
            }
        },
        "aggs": {
            "first_seen": {
                "min": {"field": "@timestamp"}
            }
        }
    }

    try:
        count_res = await container.opensearch.search(settings.opensearch_log_index, count_query)
        total_logs_24h = count_res.get("hits", {}).get("total", {}).get("value", 0)
    except Exception as exc:
        logger.error(f"Count query failed: {exc}")
        total_logs_24h = 0
        
    try:
        uptime_res = await container.opensearch.search(settings.opensearch_log_index, uptime_query)
        first_seen_ms = uptime_res.get("aggregations", {}).get("first_seen", {}).get("value")
        first_seen = int(first_seen_ms / 1000) if first_seen_ms else end
    except Exception as exc:
        logger.error(f"Uptime query failed: {exc}")
        first_seen = end

    try:
        cpu_exp = f'sum by (container) (rate(container_cpu_usage_seconds_total{{system_id="{system_id}", container!="", container!="POD"}}[5m]))'
        cpu_data = await container.prometheus.query(cpu_exp)
        
        top_cpu_svc = None
        top_cpu_val = 0.0
        for res in cpu_data:
            svc = res.get("metric", {}).get("container")
            val = float(res.get("value", [0, 0])[1])
            if val > top_cpu_val:
                top_cpu_val = val
                top_cpu_svc = svc
    except Exception as exc:
        logger.error(f"CPU query failed: {exc}")
        top_cpu_svc = None
        top_cpu_val = 0.0

    try:
        ram_exp = f'sum by (container) (container_memory_usage_bytes{{system_id="{system_id}", container!="", container!="POD"}})'
        ram_data = await container.prometheus.query(ram_exp)
        
        top_ram_svc = None
        top_ram_val = 0.0
        for res in ram_data:
            svc = res.get("metric", {}).get("container")
            val = float(res.get("value", [0, 0])[1])
            if val > top_ram_val:
                top_ram_val = val
                top_ram_svc = svc
        if top_ram_val > 0:
            top_ram_val = top_ram_val / (1024 * 1024)
    except Exception as exc:
        logger.error(f"RAM query failed: {exc}")
        top_ram_svc = None
        top_ram_val = 0.0
        
    return {
        "total_logs_24h": total_logs_24h,
        "first_seen": first_seen,
        "top_cpu_service": top_cpu_svc,
        "top_cpu_value": round(top_cpu_val, 3),
        "top_ram_service": top_ram_svc,
        "top_ram_value": round(top_ram_val, 1)
    }

@router.get("/systems/{system_id}/alerts")
async def get_system_alerts(system_id: str, request: Request):
    container = deps(request)
    
    query = {
        "size": 100,
        "sort": [{"state": {"order": "asc"}}, {"start_time": {"order": "desc"}}],
        "query": {
            "match_all": {}
        }
    }
    
    try:
        # Search both active and historical alert indices
        result = await container.opensearch.search(".opendistro-alerting-alert*", query)
        hits = result.get("hits", {}).get("hits", [])
        
        alerts = []
        for hit in hits:
            src = hit.get("_source", {})
            
            # Extract bucket-level alert details if present
            agg_content = src.get("agg_alert_content", {})
            bucket = agg_content.get("bucket", {})
            bucket_key = bucket.get("key", {})
            
            # Determine service name and detailed error message
            service_name = bucket_key.get("service") if bucket_key else None
            error_message = src.get("error_message")
            if not error_message and bucket_key:
                error_message = bucket_key.get("error_pattern")
                
            alerts.append({
                "id": hit.get("_id"),
                "monitor_name": src.get("monitor_name", "Unknown Monitor"),
                "trigger_name": src.get("trigger_name", "Unknown Trigger"),
                "state": src.get("state", "ACTIVE"),
                "severity": src.get("severity", "1"),
                "start_time": src.get("start_time"),
                "end_time": src.get("end_time"),
                "error_message": error_message,
                "service": service_name
            })
            
        from fastapi.responses import JSONResponse
        return JSONResponse(content=alerts, headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    except Exception as exc:
        logger.error(f"Failed to fetch alerts: {exc}")
        return JSONResponse(content=[], headers={"Cache-Control": "no-store, no-cache, must-revalidate"})

@router.get("/systems/{system_id}/errors/top")
async def get_top_errors(system_id: str, start: int, end: int, request: Request):
    container = deps(request)
    
    query = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}},
                    {"range": {"@timestamp": {"gte": start * 1000, "lte": end * 1000, "format": "epoch_millis"}}}
                ],
                "should": [
                    {"match": {"level": "error"}},
                    {"match": {"level": "ERROR"}},
                    {"match": {"log.level": "error"}},
                    {"match": {"status": "ERROR"}},
                    {"match": {"message": "error"}},
                    {"match": {"message": "fatal"}},
                    {"match": {"message": "critical"}},
                    {"match": {"log.message": "error"}},
                    {"match": {"log.message": "fatal"}},
                    {"match": {"log.message": "critical"}}
                ],
                "minimum_should_match": 1
            }
        },
        "aggs": {
            "top_errors": {
                "terms": {
                    "field": "log.message.keyword",
                    "size": 5
                },
                "aggs": {
                    "services": {
                        "terms": {
                            "field": "service.name",
                            "size": 3
                        }
                    }
                }
            }
        }
    }
    
    try:
        result = await container.opensearch.search(settings.opensearch_log_index, query)
        buckets = result.get("aggregations", {}).get("top_errors", {}).get("buckets", [])
        
        # If log.message.keyword is empty, fallback to message.keyword
        if not buckets:
            query["aggs"]["top_errors"]["terms"]["field"] = "message.keyword"
            result = await container.opensearch.search(settings.opensearch_log_index, query)
            buckets = result.get("aggregations", {}).get("top_errors", {}).get("buckets", [])

        errors = []
        for b in buckets:
            msg = b.get("key")
            count = b.get("doc_count")
            services = []
            service_buckets = b.get("services", {}).get("buckets", [])
            for sb in service_buckets:
                services.append(sb.get("key"))
            service_str = ", ".join(services) if services else "Unknown"
            errors.append({"message": msg, "count": count, "service": service_str})
        return errors
    except Exception as exc:
        logger.error(f"Failed to fetch top errors: {exc}")
        return []

@router.get("/systems/{system_id}/logs/context")
async def get_logs_context(system_id: str, timestamp: int, service: str, request: Request):
    container = deps(request)
    
    # 5 seconds before and 5 seconds after
    start = timestamp - 5
    end = timestamp + 5
    
    query = {
        "size": 20,
        "sort": [{"@timestamp": {"order": "asc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"system.id": system_id}},
                    {"term": {"service.name": service}},
                    {"range": {"@timestamp": {"gte": start * 1000, "lte": end * 1000, "format": "epoch_millis"}}}
                ]
            }
        }
    }
    
    try:
        result = await container.opensearch.search(settings.opensearch_log_index, query)
        hits = result.get("hits", {}).get("hits", [])
        
        logs = []
        for hit in hits:
            src = hit.get("_source", {})
            logs.append({
                "id": hit.get("_id"),
                "timestamp": src.get("@timestamp"),
                "level": src.get("level") or src.get("log", {}).get("level", "INFO"),
                "message": src.get("message") or src.get("log", "No message")
            })
            
        return logs
    except Exception as exc:
        logger.error(f"Failed to fetch context logs: {exc}")
        return []

@router.get("/systems/{system_id}/logs")
async def get_system_raw_logs(system_id: str, request: Request, query: str = "", service: str = "", level: str = "", limit: int = 100, offset: int = 0, start: int = None, end: int = None):
    container = deps(request)
    
    filter_clauses = [{"term": {"system.id": system_id}}]
    if service:
        filter_clauses.append({"term": {"service.name": service}})
    if level:
        # Check both top-level and nested level fields, upper and lower case
        lvl_up = level.upper()
        lvl_low = level.lower()
        filter_clauses.append({
            "bool": {
                "should": [
                    {"match": {"level": lvl_low}},
                    {"match": {"level": lvl_up}},
                    {"match": {"log.level": lvl_low}},
                    {"match": {"log.level": lvl_up}},
                    {"match": {"status": lvl_up}}
                ],
                "minimum_should_match": 1
            }
        })
        
    if start or end:
        range_clause = {}
        if start:
            range_clause["gte"] = start * 1000
        if end:
            range_clause["lte"] = end * 1000
        range_clause["format"] = "epoch_millis"
        filter_clauses.append({"range": {"@timestamp": range_clause}})
    
    must_clauses = []
    if query:
        must_clauses.append({"query_string": {"query": query}})
    
    es_query = {
        "size": limit,
        "from": offset,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": filter_clauses,
            }
        }
    }
    if must_clauses:
        es_query["query"]["bool"]["must"] = must_clauses
        
    try:
        result = await container.opensearch.search(settings.opensearch_log_index, es_query)
        hits = result.get("hits", {}).get("hits", [])
        
        logs = []
        for hit in hits:
            src = hit.get("_source", {})
            msg = src.get("message") or src.get("log", "No message")
            if isinstance(msg, dict):
                msg = msg.get("message") or msg.get("log") or json.dumps(msg)
                
            lvl = src.get("level")
            if not lvl and isinstance(src.get("log"), dict):
                lvl = src.get("log").get("level")
            if not lvl:
                lvl = "UNKNOWN"
                
            if isinstance(lvl, dict):
                lvl = lvl.get("level", "UNKNOWN")
                
            # Attempt to parse CRI format containing JSON: "timestamp stdout F {...}"
            if isinstance(msg, str):
                match = re.search(r'^[^\s]+\s+(?:stdout|stderr)\s+[FP]\s+(\{.*\})$', msg.strip())
                if match:
                    try:
                        parsed = json.loads(match.group(1))
                        if "log" in parsed and isinstance(parsed["log"], dict):
                            inner_log = parsed["log"]
                            msg = inner_log.get("message", msg)
                            if lvl == "UNKNOWN" and "level" in inner_log:
                                lvl = inner_log["level"]
                        elif "message" in parsed:
                            msg = parsed["message"]
                            if lvl == "UNKNOWN" and "level" in parsed:
                                lvl = parsed["level"]
                    except Exception:
                        pass
                
            if lvl == "UNKNOWN" and isinstance(msg, str):
                prefix = msg[:200].upper()
                if re.search(r'\b(ERROR|FATAL|CRITICAL|ERR)\b', prefix):
                    lvl = "ERROR"
                elif re.search(r'\b(WARN|WARNING)\b', prefix):
                    lvl = "WARN"
                elif re.search(r'\b(INFO|NOTICE)\b', prefix):
                    lvl = "INFO"
                elif re.search(r'\b(DEBUG|TRACE)\b', prefix):
                    lvl = "DEBUG"
                elif re.search(r'HTTP/1\.[01]"\s+[23]\d{2}\b', prefix):
                    lvl = "INFO"
                elif re.search(r'HTTP/1\.[01]"\s+[45]\d{2}\b', prefix):
                    lvl = "ERROR"
                    
            logs.append({
                "id": hit.get("_id"),
                "timestamp": src.get("@timestamp"),
                "service": src.get("service", {}).get("name") or src.get("container_name") or "unknown",
                "level": str(lvl),
                "message": str(msg)
            })
            
        return {
            "total": result.get("hits", {}).get("total", {}).get("value", 0),
            "logs": logs
        }
    except Exception as exc:
        logger.error(f"Failed to fetch raw logs: {exc}")
        return {"total": 0, "logs": []}

