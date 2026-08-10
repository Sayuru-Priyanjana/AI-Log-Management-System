from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.agents.orchestrator import OrchestratorAgent
from app.agents.react import ReActAgent
from app.models.analysis import Analysis, CauseCategory, InvestigationResult, InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationRequest
from app.pipeline.windows import WindowResolver
from app.registry.systems import SystemRegistry
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool

logger = logging.getLogger(__name__)


class StageEvent(BaseModel):
    stage: str
    data: dict


class InvestigationPipeline:
    """The whole run, using a ReAct agent loop for investigation."""

    def __init__(self, *, log_tool: LogTool, event_tool: EventTool, metric_tool: MetricTool,
                 orchestrator: OrchestratorAgent, react_agent: ReActAgent,
                 registry: SystemRegistry) -> None:
        self.logs = log_tool
        self.events = event_tool
        self.metrics = metric_tool
        self.orchestrator = orchestrator
        self.react_agent = react_agent
        self.registry = registry
        self.windows = WindowResolver(log_tool)

    async def run(self, request: InvestigationRequest) -> AsyncIterator[StageEvent]:
        investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
        timings: dict[str, float] = {}
        errors: list[str] = []

        def mark(stage: str, started: float) -> None:
            timings[stage] = round((time.perf_counter() - started) * 1000, 1)

        # -- 0. registry ---------------------------------------------------
        started = time.perf_counter()
        system = await self.registry.require(request.system_id)
        mark("registry", started)

        # -- 1. plan -------------------------------------------------------
        started = time.perf_counter()
        plan = await self.orchestrator.plan(request, system)
        mark("plan", started)
        yield StageEvent(stage="plan", data=plan.model_dump(mode="json"))

        # -- 2. windows ----------------------------------------------------
        started = time.perf_counter()
        windows, search_histogram = await self.windows.resolve(plan)
        mark("windows", started)
        yield StageEvent(stage="windows", data={
            **windows.model_dump(mode="json"),
            "search_buckets": len(search_histogram),
        })

        # -- 3. evidence (bulk collect for tools) --------------------------
        started = time.perf_counter()
        evidence = await self._collect(plan, windows, errors)
        mark("evidence", started)
        yield StageEvent(stage="evidence", data=self._evidence_summary(evidence))

        # -- 4. ReAct loop -------------------------------------------------
        started = time.perf_counter()
        
        final_conclusion = None
        final_service = None
        
        async for event in self.react_agent.run(plan, windows, evidence):
            event_type = event.pop("type")
            if event_type == "conclusion":
                final_conclusion = event.get("conclusion")
                final_service = event.get("service")
                break
            elif event_type == "error":
                errors.append(event.get("message", "Unknown ReAct error"))
                yield StageEvent(stage="error", data={"detail": event.get("message")})
                break
            else:
                yield StageEvent(stage=event_type, data=event)
                
        mark("react_loop", started)

        # Yield Analysis to UI
        yield StageEvent(stage="analysis", data={
            "summary": final_conclusion or "Analysis failed or didn't conclude.",
            "cause": final_conclusion,
            "confidence": 1.0 if final_conclusion else 0.0,
        })
        
        v_msg = "ReAct loop concluded the investigation."
        yield StageEvent(stage="verified", data={"message": v_msg})

        # Fake models for compatibility with legacy Result schema for now
        # until the UI is fully moved off Candidates/Signals.
        result = InvestigationResult(
            id=investigation_id,
            question=request.question,
            plan=plan,
            windows=windows,
            signals=[],
            candidates=[],
            analysis=Analysis(
                cause_summary=final_conclusion or "Analysis failed",
                category=CauseCategory.UNKNOWN,
                analyst="react",
                confidence=1.0 if final_conclusion else 0.0,
            ),
            evidence_summary=self._evidence_summary(evidence),
            timings_ms=timings,
            errors=errors,
        )
        yield StageEvent(stage="result", data=result.model_dump(mode="json"))

    async def run_collect(self, request: InvestigationRequest) -> InvestigationResult:
        """Non-streaming convenience used by the evaluation harness."""
        final: dict | None = None
        async for event in self.run(request):
            if event.stage == "result":
                final = event.data
        if final is None:
            raise RuntimeError("pipeline produced no result")
        return InvestigationResult(**final)

    # ------------------------------------------------------------------ util
    async def _collect(self, plan, windows: InvestigationWindows,
                       errors: list[str]) -> EvidenceBundle:
        incident, baseline = windows.incident, windows.baseline
        tasks = {
            "logs": self.logs.collect(plan, incident, baseline) if "logs" in plan.tools else None,
            "events": self.events.collect(plan, incident, baseline) if "events" in plan.tools else None,
            "metrics": self.metrics.collect(plan, incident, baseline) if "metrics" in plan.tools else None,
        }
        active = {name: task for name, task in tasks.items() if task is not None}
        results = await asyncio.gather(*active.values(), return_exceptions=True)

        bundle = EvidenceBundle()
        for name, outcome in zip(active.keys(), results):
            if isinstance(outcome, BaseException):
                message = f"{name} collection failed: {outcome}"
                logger.warning(message)
                errors.append(message)
                getattr(bundle, name).status = "unavailable"
                getattr(bundle, name).reason = str(outcome)
            else:
                setattr(bundle, name, outcome)
        return bundle

    @staticmethod
    def _evidence_summary(evidence: EvidenceBundle) -> dict:
        return {
            "statuses": evidence.statuses(),
            "gaps": evidence.gaps(),
            "logs": {
                "documents": evidence.logs.total_documents,
                "baseline_documents": evidence.logs.baseline_documents,
                "patterns": len(evidence.logs.patterns),
                "new_error_patterns": sum(
                    1 for p in evidence.logs.patterns
                    if p.is_new and p.level in ("ERROR", "FATAL", "CRITICAL")
                ),
                "unparsed": evidence.logs.unparsed_documents,
                "by_level": evidence.logs.totals_by_level,
            },
            "events": {
                "count": len(evidence.events.events),
                "warnings": sum(1 for e in evidence.events.events if e.severity != "info"),
            },
            "metrics": {
                "series": len(evidence.metrics.series),
                "unavailable": list(evidence.metrics.unavailable),
            },
        }
