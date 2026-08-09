from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel

from app.agents.analyst import AnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompts import build_evidence_index
from app.models.analysis import Analysis, InvestigationResult, InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationRequest
from app.pipeline.hypotheses import HypothesisEngine
from app.pipeline.signals import SignalEngine
from app.pipeline.timeline import build_timeline
from app.pipeline.verify import verify
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
    """The whole run, in fixed order:

        plan -> windows -> evidence -> signals -> candidates -> analysis -> verify

    Only two of those stages involve an LLM, and both are constrained. The order
    matters: windows come before evidence because deciding *what to look at* is
    what makes the evidence worth anything.
    """

    def __init__(self, *, log_tool: LogTool, event_tool: EventTool, metric_tool: MetricTool,
                 orchestrator: OrchestratorAgent, analyst: AnalystAgent,
                 registry: SystemRegistry) -> None:
        self.logs = log_tool
        self.events = event_tool
        self.metrics = metric_tool
        self.orchestrator = orchestrator
        self.analyst = analyst
        self.registry = registry
        self.windows = WindowResolver(log_tool)
        self.hypotheses = HypothesisEngine()

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

        # -- 3. evidence ---------------------------------------------------
        started = time.perf_counter()
        evidence = await self._collect(plan, windows, errors)
        mark("evidence", started)
        yield StageEvent(stage="evidence", data=self._evidence_summary(evidence))

        # -- 4. signals ----------------------------------------------------
        started = time.perf_counter()
        signal_engine = SignalEngine(known_services=system.service_names)
        signals = signal_engine.detect(plan, windows, evidence)
        mark("signals", started)
        yield StageEvent(stage="signals", data={
            "count": len(signals),
            "signals": [signal.model_dump(mode="json") for signal in signals],
        })

        # -- 5. candidates -------------------------------------------------
        started = time.perf_counter()
        candidates = self.hypotheses.generate(plan, windows, signals, evidence)
        mark("candidates", started)
        yield StageEvent(stage="candidates", data={
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        })

        # -- 6. analysis ---------------------------------------------------
        started = time.perf_counter()
        evidence_index = build_evidence_index(signals, candidates, evidence)
        choice, prompt, warnings = await self.analyst.select(
            plan, windows, signals, candidates, evidence
        )
        timeline = build_timeline(windows, signals, evidence)
        mark("analysis", started)

        # -- 7. verify -----------------------------------------------------
        started = time.perf_counter()
        analysis: Analysis = verify(
            choice=choice, candidates=candidates, signals=signals, evidence=evidence,
            windows=windows, evidence_index=evidence_index, timeline=timeline,
            warnings=warnings,
        )
        mark("verify", started)

        # Narration last, and never load-bearing: it restates a conclusion that
        # is already fixed, so its failure costs prose and nothing else.
        started = time.perf_counter()
        chosen = next((c for c in candidates if c.id == analysis.chosen_candidate_id), None)
        narrative, narrative_warnings = await self.analyst.narrate(
            plan, chosen, analysis.timeline, analysis.confidence
        )
        analysis.narrative = narrative
        for warning in narrative_warnings:
            errors.append(warning)
        mark("narrative", started)

        # Yield Analysis to UI
        yield StageEvent(stage="analysis", data={
            "summary": analysis.narrative or "Analysis complete.",
            "cause": analysis.cause_summary,
            "confidence": analysis.confidence,
        })

        # Yield Verification to UI
        if analysis.verification:
            issues = "\n".join(f"- {v.detail}" for v in analysis.verification)
            v_msg = f"Verification complete with warnings:\n{issues}"
        else:
            v_msg = "The root cause has been verified against deterministic data with no contradictions."
        
        yield StageEvent(stage="verified", data={"message": v_msg})

        result = InvestigationResult(
            id=investigation_id,
            question=request.question,
            plan=plan,
            windows=windows,
            signals=signals,
            candidates=candidates,
            analysis=analysis,
            evidence_summary=self._evidence_summary(evidence),
            timings_ms=timings,
            errors=errors,
        )
        yield StageEvent(stage="result", data=result.model_dump(mode="json"))
        self._last_prompt = prompt

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
                # One source failing must not abort the run; it becomes a
                # declared gap, which the verifier then uses to cap confidence.
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
