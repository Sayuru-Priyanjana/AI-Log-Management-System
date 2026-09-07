from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel

# `InMemorySaver` is the name in langgraph-checkpoint >= 2.1; the version this
# image pins (2.0.9) calls the same class `MemorySaver`. Importing the new name
# only works on a machine that happens to have the newer package — which is how
# this shipped broken: the local venv had it, the built image did not.
try:  # pragma: no cover - one branch runs per installed version
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

from app.agents.graph import TOPOLOGY, EventQueue, build_graph
from app.agents.nodes import GraphNodes
from app.agents.orchestrator import OrchestratorAgent
from app.agents.react import ReActAgent
from app.llm import telemetry
from app.llm.factory import (
    describe_context_window, describe_endpoint, describe_model, describe_provider,
)
from app.models.analysis import Analysis, CauseCategory, InvestigationResult, InvestigationWindows
from app.models.answer import MODE_BY_INTENT, AnswerMode, DataTable, StructuredAnswer
from app.models.domain import TimeWindow, ensure_utc
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan, InvestigationRequest
from app.pipeline.answer_check import verify_answer
from app.pipeline.hypotheses import HypothesisEngine
from app.pipeline.signals import SignalEngine
from app.pipeline.timeline import build_evidence_timeline, build_timeline
from app.pipeline.windows import WindowResolver
from app.registry.systems import SystemRegistry
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool

logger = logging.getLogger(__name__)


from app.store.system_settings import SystemSettingsStore

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

    def __init__(self, *, log_tool: LogTool, event_tool: EventTool,
                 orchestrator: OrchestratorAgent, react_agent: ReActAgent,
                 registry: SystemRegistry,
                 system_settings: SystemSettingsStore | None = None,
                 metric_tool: MetricTool | None = None,
                 prometheus_client=None, llm=None) -> None:
        self.logs = log_tool
        self.events = event_tool
        self.orchestrator = orchestrator
        self.react_agent = react_agent
        # Held for telemetry only — the stages reach the model through the
        # orchestrator and the loop, not through this. Defaults to the
        # orchestrator's client so an existing caller that does not pass it
        # still reports the right model name rather than "unknown".
        self.llm = llm if llm is not None else getattr(orchestrator, "_llm", None)
        self.registry = registry
        self.system_settings = system_settings
        self.prometheus_client = prometheus_client
        # Injected rather than built inside `run`. Constructing it per call made
        # the metric source impossible to substitute, which took the whole
        # pipeline out of reach of the test suite and the eval harness at once —
        # both were still passing `metric_tool=` and both had been failing with a
        # TypeError ever since.
        self.metric_tool = metric_tool
        self.hypotheses = HypothesisEngine()
        # One checkpointer for the process, so a thread's memory is shared
        # across the investigations that belong to it rather than being rebuilt
        # (and emptied) with every graph.
        self._checkpointer = InMemorySaver()

    async def run(self, request: InvestigationRequest) -> AsyncIterator[StageEvent]:
        """Executes the graph, forwarding what its nodes emit as it happens.

        The graph is run as a task rather than awaited, because a node cannot
        yield: the ReAct loop produces a dozen thoughts and observations that the
        reader should see while the node is still inside it. Those go onto a
        queue, and this drains the queue until the graph closes it.
        """
        investigation_id = f"inv-{uuid.uuid4().hex[:12]}"

        meter = telemetry.LLMMeter(
            provider=describe_provider(self.llm),
            model=describe_model(self.llm),
            endpoint=describe_endpoint(self.llm),
            context_window=describe_context_window(self.llm),
        )
        token = telemetry.bind(meter)

        system = await self.registry.require(request.system_id)
        metrics_tool = self.metric_tool or MetricTool(self.prometheus_client)

        queue = EventQueue()
        nodes = GraphNodes(self, queue, metrics_tool)
        graph = build_graph(nodes, checkpointer=self._checkpointer)

        # Every per-run channel is reset explicitly.
        #
        # With a checkpointer, invoking the same thread again starts from the
        # *saved* channel values, so anything not overwritten here would be
        # inherited from the previous question: last turn's errors, last turn's
        # timings, and a `visited` list that grows until the drawn graph claims
        # every node ran nine times. `memory` is the one channel deliberately
        # left out, because accumulating is exactly what it is for.
        initial: dict = {
            "investigation_id": investigation_id,
            "request": request,
            "system": system,
            "errors": [],
            "timings_ms": {},
            "visited": [],
            "decisions": [],
            "raw_answer": {},
            "exposed_ids": set(),
            "table": None,
            "steps_used": 0,
            "degraded": None,
            "answer": None,
            "result": None,
            "search_histogram": [],
        }

        # The shape first, so the UI can draw the graph before a single node has
        # run and light the nodes up as their events arrive, rather than
        # revealing the picture only once there is nothing left to watch.
        yield StageEvent(stage="graph", data={
            **TOPOLOGY,
            "investigation_id": investigation_id,
            "engine": "langgraph",
        })

        final: dict = {}
        failure: BaseException | None = None

        async def drive() -> None:
            nonlocal final, failure
            try:
                # `recursion_limit` bounds the whole run, not one node: without
                # it a routing bug becomes an investigation that never returns.
                # The thread is what the checkpointer keys memory on. A request
                # without one gets this run's own id, so a single question is a
                # conversation of one rather than sharing a bucket with every
                # other thread-less run.
                final = await graph.ainvoke(initial, {
                    "recursion_limit": 32,
                    "configurable": {"thread_id": request.thread_id or investigation_id},
                })
            except BaseException as exc:            # noqa: BLE001 - reported below
                failure = exc
            finally:
                await queue.close()

        task = asyncio.create_task(drive())
        try:
            async for stage, data in queue.drain():
                yield StageEvent(stage=stage, data=data)
            await task
        finally:
            task.cancel()
            telemetry.unbind(token)

        if failure is not None:
            logger.exception("Investigation graph failed", exc_info=failure)
            yield StageEvent(stage="error", data={"detail": str(failure)})
            return

        # Emitted after the answer so the count is the run's real total, and
        # separately from `result` so it is present even when a run is stopped
        # before it is stored.
        yield StageEvent(stage="llm", data=meter.snapshot())

        result: InvestigationResult | None = final.get("result")
        if result is None:
            yield StageEvent(stage="error",
                             data={"detail": "the graph produced no result"})
            return
        result.llm = meter.snapshot()
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
                       errors: list[str], metric_tool: MetricTool) -> EvidenceBundle:
        incident, baseline = windows.incident, windows.baseline
        tasks = {
            "logs": self.logs.collect(plan, incident, baseline) if "logs" in plan.tools else None,
            "events": self.events.collect(plan, incident, baseline) if "events" in plan.tools else None,
            "metrics": metric_tool.collect(plan, incident, baseline) if "metrics" in plan.tools else None,
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
