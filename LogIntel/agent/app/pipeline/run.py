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
from app.models.answer import MODE_BY_INTENT, AnswerMode, DataTable, StructuredAnswer
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan, InvestigationRequest
from app.pipeline.answer_check import verify_answer
from app.pipeline.hypotheses import HypothesisEngine
from app.pipeline.signals import SignalEngine
from app.pipeline.timeline import build_timeline
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
    """Deterministic evidence, a ReAct loop to reason over it, then verification.

        plan -> windows -> evidence -> signals -> candidates -> reasoning -> answer

    The first five stages are pure Python and produce the same result every time.
    The loop decides what to look at and how to explain it, but every figure it
    quotes was measured before it ran, and every claim it makes is checked after.
    """

    def __init__(self, *, log_tool: LogTool, event_tool: EventTool, metric_tool: MetricTool,
                 orchestrator: OrchestratorAgent, react_agent: ReActAgent,
                 registry: SystemRegistry, prometheus=None) -> None:
        self.logs = log_tool
        self.events = event_tool
        self.metrics = metric_tool
        self.orchestrator = orchestrator
        self.react_agent = react_agent
        self.registry = registry
        # The resolver gets Prometheus so it can spot latency-only incidents,
        # which leave no trace in an error histogram.
        self.windows = WindowResolver(log_tool, prometheus=prometheus)
        self.hypotheses = HypothesisEngine()

    async def run(self, request: InvestigationRequest) -> AsyncIterator[StageEvent]:
        investigation_id = f"inv-{uuid.uuid4().hex[:12]}"
        timings: dict[str, float] = {}
        errors: list[str] = []

        def mark(stage: str, started: float) -> None:
            timings[stage] = round((time.perf_counter() - started) * 1000, 1)

        started = time.perf_counter()
        system = await self.registry.require(request.system_id)
        mark("registry", started)

        # -- 1. plan -------------------------------------------------------
        started = time.perf_counter()
        plan = await self.orchestrator.plan(request, system)
        mode = MODE_BY_INTENT.get(plan.intent.value, AnswerMode.ROOT_CAUSE)
        mark("plan", started)
        yield StageEvent(stage="plan", data={**plan.model_dump(mode="json"),
                                             "answer_mode": mode.value})

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
        # Measured before the model runs, so nothing it says can change them.
        started = time.perf_counter()
        signals = SignalEngine(known_services=system.service_names).detect(
            plan, windows, evidence)
        mark("signals", started)
        yield StageEvent(stage="signals", data={
            "count": len(signals),
            "signals": [s.model_dump(mode="json") for s in signals],
        })

        # -- 5. candidates -------------------------------------------------
        started = time.perf_counter()
        candidates = self.hypotheses.generate(plan, windows, signals, evidence)
        mark("candidates", started)
        yield StageEvent(stage="candidates", data={
            "candidates": [c.model_dump(mode="json") for c in candidates],
        })

        # -- 6. reasoning loop ---------------------------------------------
        started = time.perf_counter()
        raw_answer: dict = {}
        exposed_ids: set[str] = set()
        table: DataTable | None = None
        steps_used = 0
        degraded: str | None = None

        # Held so it can be closed explicitly: breaking out of `async for` leaves
        # the generator suspended mid-await, and the event loop later complains
        # about a task destroyed while pending.
        loop = self.react_agent.run(plan, windows, evidence, signals, candidates)
        try:
            async for event in loop:
                kind = event.get("type")

                if kind == "answer":
                    raw_answer = event.get("answer") or {}
                    exposed_ids = set(event.get("exposed_ids") or [])
                    steps_used = event.get("steps_used", 0)
                    break

                if kind == "error":
                    message = event.get("message", "the reasoning loop failed")
                    errors.append(message)
                    degraded = f"the reasoning loop did not complete: {message}"
                    exposed_ids = set(event.get("exposed_ids") or [])
                    yield StageEvent(stage="reasoning", data=event)
                    break

                if kind == "exhausted":
                    errors.append(event.get("message", "step limit reached"))
                    degraded = ("the reasoning loop hit its step limit without "
                                "reaching a conclusion")
                    exposed_ids = set(event.get("exposed_ids") or [])
                    steps_used = event.get("steps_used", 0)
                    yield StageEvent(stage="reasoning", data=event)
                    break

                # An observation carrying a table is the payload of an extraction
                # or aggregation answer; keep the most recent one.
                if kind == "observation" and event.get("table"):
                    table = DataTable(**event["table"])

                yield StageEvent(stage="reasoning", data=event)
        finally:
            await loop.aclose()

        mark("reasoning", started)

        # -- 7. verify -----------------------------------------------------
        started = time.perf_counter()
        if not raw_answer and degraded:
            raw_answer = self._fallback_answer(mode, signals, candidates)
        answer: StructuredAnswer = verify_answer(
            raw=raw_answer, mode=mode, signals=signals, candidates=candidates,
            evidence=evidence, windows=windows, exposed_ids=exposed_ids,
            table=table, steps_used=steps_used, degraded=degraded,
        )
        mark("verify", started)
        yield StageEvent(stage="answer", data=answer.model_dump(mode="json"))

        result = InvestigationResult(
            id=investigation_id,
            question=request.question,
            plan=plan,
            windows=windows,
            signals=signals,
            candidates=candidates,
            analysis=Analysis(
                incident_detected=bool(signals),
                severity=self._severity(signals),
                category=CauseCategory(answer.cause_category)
                if answer.cause_category else CauseCategory.UNKNOWN,
                chosen_candidate_id=candidates[0].id if candidates else None,
                cause_summary=answer.headline,
                narrative=answer.detail,
                timeline=build_timeline(windows, signals, evidence),
                confidence=answer.confidence,
                evidence_ids=[c.id for c in answer.citations],
                next_steps=answer.next_steps,
                evidence_gaps=answer.limitations,
                analyst="react" if not degraded else "react (degraded)",
                engine_top_candidate_id=candidates[0].id if candidates else None,
                agrees_with_engine=self._agrees(answer, candidates),
            ),
            answer=answer,
            evidence_summary=self._evidence_summary(evidence),
            timings_ms=timings,
            errors=errors,
        )
        yield StageEvent(stage="result", data=result.model_dump(mode="json"))

    async def run_collect(self, request: InvestigationRequest) -> InvestigationResult:
        final: dict | None = None
        async for event in self.run(request):
            if event.stage == "result":
                final = event.data
        if final is None:
            raise RuntimeError("pipeline produced no result")
        return InvestigationResult(**final)

    # ------------------------------------------------------------------ util
    @staticmethod
    def _fallback_answer(mode: AnswerMode, signals, candidates) -> dict:
        """What to say when the loop failed.

        The deterministic stages already ran, so there is a real answer available
        even with no model at all. Reporting it — clearly marked as the rules'
        answer rather than the agent's — beats returning nothing.
        """
        if candidates:
            top = candidates[0]
            return {
                "headline": top.hypothesis,
                "detail": (f"{top.rationale} This came from the rule engine; the "
                           f"reasoning loop did not finish, so there is no "
                           f"model-written explanation."),
                "root_cause_service": top.service,
                "reasoning": [{"claim": top.hypothesis, "because": top.rationale,
                               "evidence_ids": top.supporting_signals,
                               "kind": "inference"}],
                "confidence": top.score,
                "limitations": ["The reasoning loop did not complete; this is the "
                                "deterministic ranking only."],
            }
        if signals:
            return {
                "headline": f"{len(signals)} signal(s) were detected but no explanation "
                            f"could be assembled.",
                "detail": "The measurements are reported below.",
                "reasoning": [{"claim": s.description, "evidence_ids": [s.id],
                               "kind": "observation"} for s in signals[:5]],
                "confidence": 0.2,
            }
        return {
            "headline": "Nothing measurable departed from baseline in this window.",
            "detail": "No signal crossed its threshold.",
            "confidence": 0.3,
        }

    @staticmethod
    def _severity(signals) -> str:
        if not signals:
            return "none"
        return max(signals, key=lambda s: s.severity.rank).severity.value

    @staticmethod
    def _agrees(answer: StructuredAnswer, candidates) -> bool:
        if not candidates or not answer.root_cause_service:
            return True
        return candidates[0].service == answer.root_cause_service

    async def _collect(self, plan: InvestigationPlan, windows: InvestigationWindows,
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
                "dependency_edges": evidence.logs.dependency_edges,
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
