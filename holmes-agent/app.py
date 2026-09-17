"""LogIntel API adapter for the separately deployed HolmesGPT server.

Holmes only sees scoped, client-executed tools. Every OpenSearch query fixes
system/environment/time filters here; model arguments cannot replace them.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(title="LogIntel HolmesGPT Adapter")

HOLMES_URL = os.getenv("HOLMES_URL", "http://holmes-runtime:5050").rstrip("/")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").rstrip("/")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
LOG_INDEX = os.getenv("OPENSEARCH_LOG_INDEX", "logintel-logs-*")
EVENT_INDEX = os.getenv("OPENSEARCH_EVENT_INDEX", "logintel-events-*")
INVESTIGATION_INDEX = os.getenv("OPENSEARCH_INVESTIGATION_INDEX", "logintel-investigations")
QUERY_INDEX = "logintel-promql-queries"
HOLMES_MODEL = os.getenv("HOLMES_MODEL", "gemini/gemini-3.5-flash-lite")
MAX_WINDOW = timedelta(days=7)
_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
_QUERY_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_SYSTEM_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
GRAPH_TOPOLOGY = {"engine": "holmes", "nodes": [
    {"id": "scope", "label": "Validate scope", "kind": "guard", "row": 0, "emits": "plan"},
    {"id": "holmes", "label": "HolmesGPT loop", "kind": "llm", "row": 1, "emits": "reasoning"},
    {"id": "verify", "label": "Verify evidence", "kind": "guard", "row": 2, "emits": "answer"},
    {"id": "result", "label": "Store result", "kind": "terminal", "row": 3, "emits": "result"}],
    "edges": [{"from": "__start__", "to": "scope"}, {"from": "scope", "to": "holmes"},
              {"from": "holmes", "to": "verify"}, {"from": "verify", "to": "result"}]}
BUILTIN_PROMQL = {
    "cpu_usage": {"name": "CPU usage by container", "expression":
        'sum by (system_id, container) (rate(container_cpu_usage_seconds_total{system_id="{{system_id}}",container!="",container!="POD"}[2m]))'},
    "memory_working_set": {"name": "Memory working set by container", "expression":
        'sum by (system_id, container) (container_memory_working_set_bytes{system_id="{{system_id}}",container!="",container!="POD"})'},
}


class ChatMessage(BaseModel):
    role: str
    content: str


class InvestigationRequest(BaseModel):
    system_id: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=4000)
    duration: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    service_hint: str | None = None
    thread_id: str | None = None
    chat_history: list[ChatMessage] = Field(default_factory=list)


class PromQuery(BaseModel):
    id: str
    system_id: str
    name: str
    expression: str


def window_for(request: InvestigationRequest) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if request.start_time or request.end_time:
        if not request.start_time or not request.end_time:
            raise ValueError("Both start_time and end_time are required")
        start = datetime.fromisoformat(request.start_time.replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.end_time.replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Time range must include a timezone")
        start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    else:
        match = _DURATION.fullmatch(request.duration or "1h")
        if not match:
            raise ValueError("Duration must use minutes, hours, or days")
        span = timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
        start, end = now - span, now
    if not start < end or end - start > MAX_WINDOW or end > now + timedelta(minutes=1):
        raise ValueError("Time range must be positive, no more than seven days, and not in the future")
    return start, end


async def os_search(client: httpx.AsyncClient, index: str, body: dict) -> dict:
    response = await client.post(f"{OPENSEARCH_URL}/{index}/_search", json=body, timeout=30)
    response.raise_for_status()
    return response.json()


def scope_filter(request: InvestigationRequest, start: datetime, end: datetime,
                 service: str | None = None) -> list[dict]:
    filters = [
        {"term": {"system.id": request.system_id}},
        {"term": {"environment": request.environment}},
        {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}},
    ]
    if service:
        filters.append({"term": {"service.name": service}})
    return filters


async def require_scope(client: httpx.AsyncClient, request: InvestigationRequest) -> dict:
    # Registry validation is performed against existing LogIntel documents.
    body = {"size": 0, "query": {"bool": {"filter": [
        {"term": {"system.id": request.system_id}},
        {"term": {"environment": request.environment}},
    ]}}, "aggs": {"name": {"terms": {"field": "system.name", "size": 1}},
                  "services": {"terms": {"field": "service.name", "size": 100}}}}
    data = await os_search(client, LOG_INDEX, body)
    if data.get("hits", {}).get("total", {}).get("value", 0) == 0:
        raise HTTPException(404, "Unknown system or environment")
    names = data.get("aggregations", {}).get("name", {}).get("buckets", [])
    services = [b["key"] for b in data.get("aggregations", {}).get("services", {}).get("buckets", [])]
    if request.service_hint and request.service_hint not in services:
        raise HTTPException(400, "Service is not registered in this system and environment")
    return {"name": names[0]["key"] if names else request.system_id, "services": services}


def frontend_tools() -> list[dict]:
    return [
        {"name": "logintel_search_logs", "description": "Search scoped LogIntel logs. Returns evidence IDs and bounded samples.",
         "parameters": {"type": "object", "properties": {"service": {"type": "string"},
             "level": {"type": "string"}, "pod": {"type": "string"},
             "text": {"type": "string"}, "limit": {"type": "integer"}}}},
        {"name": "logintel_count_logs", "description": "Count logs by level and service in the selected window.",
         "parameters": {"type": "object", "properties": {}}},
        {"name": "logintel_search_events", "description": "Find Kubernetes events for this system and window.",
         "parameters": {"type": "object", "properties": {"service": {"type": "string"}}}},
        {"name": "logintel_promql", "description": "Run a saved, system-scoped PromQL query by query ID.",
         "parameters": {"type": "object", "properties": {"query_id": {"type": "string"}},
                        "required": ["query_id"]}},
    ]


def evidence_id(hit: dict) -> str:
    return f"doc:{hit.get('_index', '')}:{hit.get('_id', '')}"


async def execute_tool(client: httpx.AsyncClient, name: str, args: dict,
                       request: InvestigationRequest, start: datetime, end: datetime,
                       services: list[str]) -> dict:
    service = args.get("service") or request.service_hint
    if service and service not in services:
        return {"error": "Service is outside the selected system"}
    filters = scope_filter(request, start, end, service)
    if name == "logintel_search_logs":
        if args.get("pod"):
            filters.append({"term": {"kubernetes.pod.name": str(args["pod"])[:150]}})
        if args.get("level"):
            filters.append({"term": {"log.level": str(args["level"]).upper()[:20]}})
        must = []
        if args.get("text"):
            must.append({"match": {"log.message": str(args["text"])[:200]}})
        body = {"size": max(1, min(int(args.get("limit") or 20), 50)),
                "query": {"bool": {"filter": filters, "must": must}},
                "sort": [{"@timestamp": "desc"}],
                "_source": ["@timestamp", "service.name", "log.level", "log.message", "kubernetes.pod.name"]}
        data = await os_search(client, LOG_INDEX, body)
        return {"total": data.get("hits", {}).get("total", {}).get("value", 0),
                "samples": [{"evidence_id": evidence_id(h), **h.get("_source", {})}
                            for h in data.get("hits", {}).get("hits", [])]}
    if name == "logintel_count_logs":
        data = await os_search(client, LOG_INDEX, {"size": 0,
            "query": {"bool": {"filter": filters}},
            "aggs": {"levels": {"terms": {"field": "log.level", "size": 12}},
                     "services": {"terms": {"field": "service.name", "size": 25}}}})
        return {"total": data.get("hits", {}).get("total", {}).get("value", 0),
                "levels": data.get("aggregations", {}).get("levels", {}).get("buckets", []),
                "services": data.get("aggregations", {}).get("services", {}).get("buckets", [])}
    if name == "logintel_search_events":
        # Event documents do not always carry service.name; a service hint must
        # not silently remove pod and deployment events from the investigation.
        filters = scope_filter(request, start, end)
        data = await os_search(client, EVENT_INDEX, {"size": 30,
            "query": {"bool": {"filter": filters}}, "sort": [{"@timestamp": "desc"}],
            "_source": ["@timestamp", "service.name", "kubernetes", "reason", "message", "event"]})
        return {"events": [{"evidence_id": evidence_id(h), **h.get("_source", {})}
                           for h in data.get("hits", {}).get("hits", [])]}
    if name == "logintel_promql":
        query_id = str(args.get("query_id") or "")
        if not _QUERY_ID.fullmatch(query_id):
            return {"error": "Invalid saved query ID"}
        if query_id in BUILTIN_PROMQL:
            template = BUILTIN_PROMQL[query_id]["expression"]
        else:
            key = f"{request.system_id}:{query_id}"
            saved = await client.get(f"{OPENSEARCH_URL}/{QUERY_INDEX}/_doc/{key}", timeout=10)
            if saved.status_code != 200:
                return {"error": "Unknown saved PromQL query"}
            template = saved.json().get("_source", {}).get("expression", "")
        expression = template.replace("{{system_id}}", request.system_id.replace('"', '\\"'))
        response = await client.get(f"{PROMETHEUS_URL}/api/v1/query_range", params={
            "query": expression, "start": start.timestamp(), "end": end.timestamp(),
            "step": max(15, int((end - start).total_seconds() / 60))}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            return {"error": payload.get("error", "Prometheus query failed")}
        series = payload.get("data", {}).get("result", [])[:20]
        if any(s.get("metric", {}).get("system_id") != request.system_id for s in series):
            return {"error": "PromQL output lacks the selected system_id label"}
        return {"query_id": query_id, "expression": expression,
                "series": [{"metric": s.get("metric", {}), "values": s.get("values", [])[:120]}
                           for s in series]}
    return {"error": "Tool is not enabled"}


async def holmes_events(client: httpx.AsyncClient, body: dict) -> AsyncIterator[tuple[str, dict]]:
    async with client.stream("POST", f"{HOLMES_URL}/api/chat", json=body, timeout=180) as response:
        response.raise_for_status()
        event_name = ""
        data_lines: list[str] = []
        async for line in response.aiter_lines():
            if not line:
                if data_lines:
                    try:
                        yield event_name, json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        pass
                event_name, data_lines = "", []
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            try:
                yield event_name, json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                pass


def ndjson(stage: str, data: dict) -> str:
    return json.dumps({"stage": stage, "data": data}, default=str) + "\n"


def assemble_result(request: InvestigationRequest, system: dict,
                    start: datetime, end: datetime, analysis: str,
                    evidence: dict[str, dict], steps: list[dict]) -> dict:
    run_id = f"inv-{uuid.uuid4().hex[:12]}"
    cited = [key for key in evidence if key in analysis]
    headline = next((line.strip("# *") for line in analysis.splitlines() if line.strip()),
                    "HolmesGPT investigation completed")[:240]
    confidence = 0.4 if cited else 0.2
    startup_evidence = any(re.search(
        r"fatal|outofmemory|oomkill|imagepull|back-off pulling|failed to start|uncaught exception|panic:",
        json.dumps(item).lower()) for item in evidence.values())
    unsupported_crash_cause = "crashloop" in analysis.lower() and not startup_evidence
    if unsupported_crash_cause:
        headline = "Crashloop observed; container exit cause unconfirmed"
        confidence = min(confidence, 0.3)
    elif not cited:
        headline = "Investigation completed; root cause unconfirmed"
    answer = {"mode": "root_cause", "headline": headline, "detail": analysis[:12000],
              "root_cause_service": None, "cause_category": "unknown", "confidence": confidence,
              "citations": [{"id": key, "label": "Observed LogIntel record", "status": "resolved"}
                            for key in cited],
              "reasoning": [{"claim": str((evidence[key].get("log") or {}).get("message") or
                                        evidence[key].get("message") or "Observed record")[:240],
                             "because": "This LogIntel record was cited in the HolmesGPT answer.",
                             "evidence_ids": [key], "kind": "observation"} for key in cited[:12]],
              "assumptions": [],
              "limitations": (["No startup or termination evidence established the crashloop exit cause."]
                              if unsupported_crash_cause else
                              ([] if cited else ["HolmesGPT did not cite a specific observed record; the root cause remains unconfirmed."])),
              "next_steps": [{"label": s.get("action_label", "Investigate further"),
                              "kind": "investigation", "question": s.get("prompt", "")}
                             for s in steps[:5]]}
    return {"id": run_id, "thread_id": request.thread_id or run_id,
            "created_at": datetime.now(timezone.utc).isoformat(), "question": request.question,
            "engine": "holmes", "plan": {"intent": "incident_investigation",
                "system_id": request.system_id, "system_name": system["name"],
                "environment": request.environment, "service": request.service_hint,
                "namespaces": [], "requested_window": {"start": start.isoformat(), "end": end.isoformat()},
                "tools": ["logs", "events", "metrics"], "goal": request.question,
                "planner": "holmes", "chat_history": [m.model_dump() for m in request.chat_history]},
            "windows": {"requested": {"start": start.isoformat(), "end": end.isoformat()},
                        "incident": {"start": start.isoformat(), "end": end.isoformat()},
                        "method": "user_requested"},
            "signals": [], "candidates": [], "analysis": {"incident_detected": False,
                "severity": "unknown", "category": "unknown", "cause_summary": headline,
                "narrative": analysis[:12000], "confidence": confidence,
                "evidence_ids": cited, "analyst": "holmes"},
            "answer": answer, "evidence_timeline": [
                {"id": key, "kind": "event" if "event" in key.split(":")[1] else "log",
                 "first_seen": item.get("@timestamp") or start.isoformat(),
                 "title": str((item.get("log") or {}).get("message") or item.get("reason") or
                              item.get("message") or "Observed record")[:240],
                 "detail": str(item.get("message") or (item.get("log") or {}).get("message") or "")[:1000],
                 "service": (item.get("service") or {}).get("name"), "occurrences": 1,
                 "notable": key in cited} for key, item in list(evidence.items())[:50]],
            "evidence_summary": {"holmes_tool_results": len(evidence)},
            "timings_ms": {}, "errors": [], "llm": {"provider": "holmes", "model": HOLMES_MODEL},
            "graph_path": ["scope", "holmes", "verify", "result"], "graph_decisions": [],
            "graph_topology": GRAPH_TOPOLOGY}


@app.post("/api/investigations")
async def investigate(request: InvestigationRequest):
    if not request.question.strip():
        raise HTTPException(400, "Question is required")
    try:
        start, end = window_for(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with httpx.AsyncClient() as client:
        system = await require_scope(client, request)
        saved_queries = await read_promql_queries(client, request.system_id)

    async def stream():
        evidence: dict[str, dict] = {}
        analysis = ""
        actions: list[dict] = []
        step_no = 0
        llm_usage = {"provider": "holmes", "model": HOLMES_MODEL,
                     "requests": 0, "peak_prompt_tokens": 0}
        prompt = (f"Investigate only LogIntel system {request.system_id}, environment {request.environment}, "
                  f"from {start.isoformat()} to {end.isoformat()}. "
                  f"Use the provided LogIntel tools. Cite evidence IDs in the answer. "
                  f"A deployment and crashloop at the same time do not establish the exit cause. "
                  f"If startup/termination evidence is missing, say the cause is unconfirmed. "
                  f"Question: {request.question}")
        if request.service_hint:
            prompt += f" Focus on service {request.service_hint}."
        if saved_queries:
            prompt += " Available saved PromQL query IDs: " + ", ".join(
                f"{q['id']} ({q.get('name', '')})" for q in saved_queries[:20]) + "."
        yield ndjson("plan", {"system_id": request.system_id, "environment": request.environment,
                              "service": request.service_hint, "goal": request.question})
        yield ndjson("windows", {"requested": {"start": start.isoformat(), "end": end.isoformat()},
                                 "incident": {"start": start.isoformat(), "end": end.isoformat()}})
        yield ndjson("graph", GRAPH_TOPOLOGY)
        body: dict[str, Any] = {"ask": prompt, "stream": True, "model": HOLMES_MODEL,
            "frontend_tools": frontend_tools(),
            "additional_system_prompt": "Use only LogIntel frontend tools for incident data. Never infer a specific exit cause from timing alone."}
        if request.chat_history:
            body["ask"] += "\nPrior conversation:\n" + "\n".join(
                f"{m.role}: {m.content[:1000]}" for m in request.chat_history[-10:])
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(12):
                    paused = False
                    async for event, data in holmes_events(client, body):
                        if event == "ai_message" and data.get("content"):
                            step_no += 1
                            yield ndjson("reasoning", {"type": "thought", "step": step_no,
                                                       "text": data["content"][:3000]})
                        elif event == "start_tool_calling":
                            step_no += 1
                            yield ndjson("reasoning", {"type": "action", "step": step_no,
                                                       "tool": data.get("tool_name", "unknown"), "input": {}})
                        elif event == "token_count":
                            llm_usage["requests"] += 1
                            usage = data.get("metadata", {}).get("usage", {})
                            llm_usage["peak_prompt_tokens"] = max(
                                llm_usage["peak_prompt_tokens"], int(usage.get("prompt_tokens") or 0))
                        elif event == "approval_required":
                            calls = data.get("pending_frontend_tool_calls") or []
                            if not calls:
                                raise RuntimeError("HolmesGPT requested an unavailable tool")
                            results = []
                            for call in calls[:8]:
                                name = call.get("tool_name", "")
                                args = call.get("arguments") or {}
                                try:
                                    observation = await execute_tool(client, name, args, request, start, end, system["services"])
                                except (httpx.HTTPError, ValueError) as exc:
                                    observation = {"error": str(exc)[:300]}
                                for item in observation.get("samples", []) + observation.get("events", []):
                                    evidence[item["evidence_id"]] = item
                                results.append({"tool_call_id": call["tool_call_id"], "tool_name": name,
                                                "result": json.dumps(observation)[:30000]})
                                ids = [item["evidence_id"] for item in
                                       observation.get("samples", []) + observation.get("events", [])]
                                yield ndjson("reasoning", {"type": "observation", "step": step_no,
                                                           "tool": name, "text": json.dumps(observation)[:3000],
                                                           "evidence_ids": ids})
                            body = {"ask": prompt, "stream": True, "model": HOLMES_MODEL,
                                    "frontend_tools": frontend_tools(),
                                    "conversation_history": data["conversation_history"],
                                    "frontend_tool_results": results}
                            paused = True
                            break
                        elif event == "ai_answer_end":
                            analysis = str(data.get("analysis") or "")
                            actions = data.get("follow_up_actions") or []
                    if not paused:
                        break
                if not analysis:
                    raise RuntimeError("HolmesGPT did not return a final answer")
                result = assemble_result(request, system, start, end, analysis, evidence, actions)
                result["llm"] = llm_usage
                yield ndjson("llm", llm_usage)
                saved = await client.put(f"{OPENSEARCH_URL}/{INVESTIGATION_INDEX}/_doc/{result['id']}",
                                         json=result, timeout=20)
                result["persisted"] = saved.is_success
                yield ndjson("answer", result["answer"])
                yield ndjson("result", result)
        except Exception as exc:
            yield ndjson("error", {"kind": type(exc).__name__, "detail": str(exc)[:500]})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/api/agent/identity")
async def identity() -> dict:
    return {"engine": "holmes", "upstream_version": "0.39.0", "llm": {"provider": "holmes", "model": HOLMES_MODEL,
                                        "endpoint": HOLMES_URL}}


@app.get("/api/agent/graph")
async def graph() -> dict:
    return GRAPH_TOPOLOGY


@app.get("/api/health")
async def health() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{HOLMES_URL}/readyz")
        ready = response.is_success
    except httpx.HTTPError:
        ready = False
    report = {"status": "ok" if ready else "degraded", "components": {
        "agent": {"status": "ok" if ready else "unreachable", "model": HOLMES_MODEL},
        "model": {"status": "ok" if ready else "unreachable", "model": HOLMES_MODEL}}}
    return report if ready else JSONResponse(report, status_code=503)


@app.get("/api/promql-queries")
async def list_promql_queries(system_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        return {"queries": await read_promql_queries(client, system_id)}


async def read_promql_queries(client: httpx.AsyncClient, system_id: str) -> list[dict]:
    builtins = [{"id": key, "system_id": system_id, **value, "builtin": True}
                for key, value in BUILTIN_PROMQL.items()]
    try:
        data = await os_search(client, QUERY_INDEX, {"size": 100,
            "query": {"term": {"system_id.keyword": system_id}}})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return builtins
        raise
    return builtins + [h.get("_source", {}) for h in data.get("hits", {}).get("hits", [])]


@app.put("/api/promql-queries")
async def save_promql_query(query: PromQuery) -> dict:
    normalized = query.expression.replace("{{system_id}}", "__LOGINTEL_SYSTEM__")
    selectors = re.findall(r"\{([^{}]*)\}", normalized)
    if query.id in BUILTIN_PROMQL or not _SYSTEM_ID.fullmatch(query.system_id) or not _QUERY_ID.fullmatch(query.id) or \
            len(query.expression) > 1000 or not selectors or \
            any('system_id="__LOGINTEL_SYSTEM__"' not in selector for selector in selectors):
        raise HTTPException(400, "Each PromQL selector must include system_id=\"{{system_id}}\"")
    async with httpx.AsyncClient() as client:
        response = await client.put(f"{OPENSEARCH_URL}/{QUERY_INDEX}/_doc/{query.system_id}:{query.id}",
                                    json=query.model_dump(), timeout=15)
        response.raise_for_status()
    return {"query": query.model_dump()}
