from __future__ import annotations

import json
import logging
import re

from app.llm.base import LLMClient, LLMUnavailable, PromptTruncated
from app.models.domain import SystemDescriptor, TimeWindow, parse_duration
from app.models.plan import (
    ALLOWED_DURATIONS,
    DEFAULT_DURATION_BY_INTENT,
    INTENT_TOOLS,
    Intent,
    InvestigationPlan,
    InvestigationRequest,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You classify operational questions for a log analysis system.

Return only JSON. Do not explain, do not add prose.

Rules:
- Choose `intent` from the provided list only.
- Choose `service` from the provided service list only, copying the name exactly.
  Use null when the question does not clearly name one of them.
- Choose `duration` from the provided list only. If the question names no period,
  pick the shortest duration that plausibly covers it.
- `goal` is one short sentence restating what should be investigated.

Never invent a service name. Never invent a duration format."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "service": {"type": ["string", "null"]},
        "duration": {"type": "string", "enum": ALLOWED_DURATIONS},
        "goal": {"type": "string"},
    },
    "required": ["intent", "duration", "goal"],
}

_INTENT_KEYWORDS: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.INCIDENT_INVESTIGATION,
     ("fail", "error", "down", "broken", "crash", "outage", "incident", "why", "5xx", "wrong")),
    (Intent.PERFORMANCE_REVIEW,
     ("slow", "latency", "performance", "timeout", "throughput", "cpu", "memory", "saturat")),
    (Intent.HEALTH_CHECK,
     ("health", "healthy", "status", "ok", "fine", "everything")),
    (Intent.HISTORICAL_QUERY,
     ("yesterday", "last week", "history", "trend", "over time", "since")),
)

_DURATION_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\b(\d+)\s*min", "m"), (r"\b(\d+)\s*hour", "h"),
    (r"\b(\d+)\s*day", "d"), (r"\b(\d+)\s*week", "w"),
)


class OrchestratorAgent:
    """Turns a question into a validated plan.

    The model contributes intent, a service name and a duration — and nothing
    else. Identity comes from the request, the tool list from a lookup table, and
    every model-supplied value is checked against the registry before it reaches
    a query. A hallucinated service name is caught here rather than becoming a
    filter that matches nothing and an investigation that reports all-clear.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def plan(self, request: InvestigationRequest,
                   system: SystemDescriptor) -> InvestigationPlan:
        notes: list[str] = []
        planner = "llm"

        try:
            raw = await self._ask(request, system)
        except (LLMUnavailable, PromptTruncated, ValueError) as exc:
            logger.warning("Planner LLM failed (%s); using keyword heuristics", exc)
            notes.append(f"planner fell back to heuristics: {exc}")
            planner = "heuristic"
            raw = {}

        intent = self._resolve_intent(raw.get("intent"), request.question)

        requested_service = request.service_hint or raw.get("service")
        service = system.resolve_service(requested_service)
        if request.service_hint and not service:
            notes.append(
                f"the requested service '{request.service_hint}' does not match any known "
                f"service in {system.id}; investigating the whole system instead"
            )
        elif requested_service and not service:
            # Do not silently drop it: an unmatched name usually means the user
            # meant something real that is not shipping logs.
            notes.append(
                f"'{requested_service}' does not match any known service in {system.id} "
                f"(known: {', '.join(system.service_names) or 'none'}); "
                f"investigating the whole system instead"
            )
        elif service and requested_service and service != requested_service:
            notes.append(f"resolved service '{requested_service}' to '{service}'")

        duration = self._resolve_duration(request, raw.get("duration"), intent, notes)

        if request.environment not in system.environments and system.environments:
            notes.append(
                f"environment '{request.environment}' has no data in {system.id}; "
                f"known environments: {', '.join(system.environments)}"
            )

        return InvestigationPlan(
            intent=intent,
            system_id=system.id,
            system_name=system.name,
            environment=request.environment,
            service=service,
            namespaces=system.namespaces,
            requested_window=TimeWindow.last(duration, label="requested"),
            tools=INTENT_TOOLS[intent],
            goal=(raw.get("goal") or request.question).strip()[:300],
            planner=planner,
            notes=notes,
        )

    async def _ask(self, request: InvestigationRequest, system: SystemDescriptor) -> dict:
        services = system.service_names or ["(none discovered)"]
        prompt = (
            f"System: {system.name} ({system.id})\n"
            f"Environment: {request.environment}\n"
            f"Services available: {', '.join(services)}\n"
            f"Intents available: {', '.join(i.value for i in Intent)}\n"
            f"Durations available: {', '.join(ALLOWED_DURATIONS)}\n\n"
            f"Question: {request.question}\n\n"
            f"JSON:"
        )
        response = await self._llm.generate(system=SYSTEM_PROMPT, prompt=prompt,
                                            schema=RESPONSE_SCHEMA)
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"planner returned non-JSON: {text[:200]}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("planner returned a non-object")
        return parsed

    @staticmethod
    def _resolve_intent(candidate: str | None, question: str) -> Intent:
        if candidate:
            try:
                return Intent(candidate)
            except ValueError:
                pass
        lowered = question.lower()
        for intent, keywords in _INTENT_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                return intent
        return Intent.INCIDENT_INVESTIGATION

    @staticmethod
    def _resolve_duration(request: InvestigationRequest, candidate: str | None,
                          intent: Intent, notes: list[str]) -> str:
        if request.duration:
            try:
                parse_duration(request.duration)
                return request.duration
            except ValueError:
                notes.append(f"ignored invalid duration override '{request.duration}'")

        if candidate in ALLOWED_DURATIONS:
            return candidate

        # The question itself is more trustworthy than a model that ignored the
        # enum it was handed.
        lowered = request.question.lower()
        for pattern, unit in _DURATION_PHRASES:
            match = re.search(pattern, lowered)
            if match:
                spelled = f"{match.group(1)}{unit}"
                if spelled in ALLOWED_DURATIONS:
                    return spelled
                try:
                    parse_duration(spelled)
                    return spelled
                except ValueError:
                    break

        if candidate:
            notes.append(f"planner proposed unsupported duration '{candidate}'")
        return DEFAULT_DURATION_BY_INTENT[intent]
