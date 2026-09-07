"""
The bodies of the graph's nodes.

Each one is the stage the sequential pipeline already ran, lifted into a
function that reads state and returns the fields it changed. They are kept apart
from `graph.py` so the topology stays servable without constructing an
OpenSearch client, and apart from `run.py` so the pipeline keeps owning the
tools rather than the graph owning them.

Two rules hold everywhere in this file:

  * a node never raises for an expected failure. A source being unreachable, a
    model being down, a loop running out of steps — all of these are recorded in
    `errors`/`degraded` and routed around, because a graph that throws loses the
    evidence collected before the throw.
  * a node that took a branch says why. `decisions` is what the UI draws after
    the answer, and an unexplained edge is not worth drawing.
"""
from __future__ import annotations

import logging
import time

from app.agents.graph import EventQueue
from app.llm import telemetry
from app.models.analysis import Analysis, CauseCategory, InvestigationResult
from app.models.answer import MODE_BY_INTENT, AnswerMode, DataTable, StructuredAnswer
from app.models.domain import TimeWindow, ensure_utc
from app.pipeline.answer_check import verify_answer
from app.pipeline.signals import SignalEngine
from app.pipeline.timeline import build_evidence_timeline, build_timeline
from app.pipeline.windows import WindowResolver

logger = logging.getLogger(__name__)


class GraphNodes:
    """Node callables bound to one pipeline and one investigation's event queue."""

    def __init__(self, pipeline, queue: EventQueue, metric_tool) -> None:
        self._p = pipeline
        self._q = queue
        self._metrics = metric_tool

    # ------------------------------------------------------------- utilities
    async def _emit(self, stage: str, data: dict) -> None:
        await self._q.put(stage, data)

    @staticmethod
    def _timed(state: dict, name: str, started: float) -> dict:
        timings = dict(state.get("timings_ms") or {})
        timings[name] = round((time.perf_counter() - started) * 1000, 1)
        return timings

    @staticmethod
    def _enter(state: dict, node: str) -> list:
        return list(state.get("visited") or []) + [node]

    @staticmethod
    def _decide(state: dict, frm: str, to: str, why: str) -> list:
        return list(state.get("decisions") or []) + [
            {"from": frm, "to": to, "why": why}
        ]

    # ---------------------------------------------------------------- nodes
    async def plan(self, state: dict) -> dict:
        telemetry.set_stage("plan")
        started = time.perf_counter()
        plan = await self._p.orchestrator.plan(state["request"], state["system"])
        mode = MODE_BY_INTENT.get(plan.intent.value, AnswerMode.ROOT_CAUSE)
        await self._emit("plan", {**plan.model_dump(mode="json"),
                                  "answer_mode": mode.value})
        return {"investigation_plan": plan, "mode": mode,
                "visited": self._enter(state, "plan"),
                "timings_ms": self._timed(state, "plan", started)}

    async def windows(self, state: dict) -> dict:
        telemetry.set_stage("windows")
        started = time.perf_counter()
        resolver = WindowResolver(self._p.logs, prometheus=self._p.prometheus_client)
        windows, histogram = await resolver.resolve(state["investigation_plan"])
        await self._emit("windows", {**windows.model_dump(mode="json"),
                                     "search_buckets": len(histogram)})
        return {"resolved_windows": windows, "search_histogram": histogram,
                "visited": self._enter(state, "windows"),
                "timings_ms": self._timed(state, "windows", started)}

    async def evidence(self, state: dict) -> dict:
        telemetry.set_stage("evidence")
        started = time.perf_counter()
        errors = list(state.get("errors") or [])
        evidence = await self._p._collect(state["investigation_plan"], state["resolved_windows"], errors,
                                          self._metrics)
        await self._emit("evidence", self._p._evidence_summary(evidence))
        return {"evidence_bundle": evidence, "errors": errors,
                "visited": self._enter(state, "evidence"),
                "timings_ms": self._timed(state, "evidence", started)}

    async def signals(self, state: dict) -> dict:
        telemetry.set_stage("signals")
        started = time.perf_counter()
        engine = SignalEngine(known_services=state["system"].service_names)
        signals = engine.detect(state["investigation_plan"], state["resolved_windows"], state["evidence_bundle"])
        await self._emit("signals", {
            "count": len(signals),
            "signals": [s.model_dump(mode="json") for s in signals],
        })
        return {"detected_signals": signals,
                "decisions": self._decide(state, "evidence", "signals",
                                          evidence_route(state)[1]),
                "visited": self._enter(state, "signals"),
                "timings_ms": self._timed(state, "signals", started)}

    async def candidates(self, state: dict) -> dict:
        telemetry.set_stage("candidates")
        started = time.perf_counter()
        candidates = self._p.hypotheses.generate(
            state["investigation_plan"], state["resolved_windows"], state["detected_signals"], state["evidence_bundle"])
        await self._emit("candidates", {
            "candidates": [c.model_dump(mode="json") for c in candidates],
        })
        return {"ranked_candidates": candidates,
                "visited": self._enter(state, "candidates"),
                "timings_ms": self._timed(state, "candidates", started)}

    async def reason(self, state: dict) -> dict:
        telemetry.set_stage("reasoning")
        started = time.perf_counter()
        errors = list(state.get("errors") or [])

        raw_answer: dict = {}
        exposed_ids: set = set()
        table = None
        steps_used = 0
        degraded: str | None = None

        # How far back the loop's live queries may reach. The resolver already
        # looked back several times further than the question asked, to find an
        # onset and a quiet baseline; its first bucket is the earliest data
        # examined. Clamping to the incident window would make "did this start
        # before the window?" unanswerable by construction.
        search_window = None
        histogram = state.get("search_histogram") or []
        if histogram:
            search_window = TimeWindow(
                start=ensure_utc(histogram[0].timestamp),
                end=state["resolved_windows"].incident.end, label="search",
            )

        loop = self._p.react_agent.run(
            state["investigation_plan"], state["resolved_windows"], state["evidence_bundle"], state["detected_signals"],
            state["ranked_candidates"], log_tool=self._p.logs, search_window=search_window,
        )
        # Held so it can be closed explicitly: breaking out of `async for` leaves
        # the generator suspended mid-await, and the event loop later complains
        # about a task destroyed while pending.
        try:
            async for event in loop:
                kind = event.get("type")

                if kind == "answer":
                    raw_answer = event.get("answer") or {}
                    exposed_ids = set(event.get("exposed_ids") or [])
                    steps_used = event.get("steps_used", 0)
                    break

                if kind in ("error", "exhausted"):
                    exposed_ids = set(event.get("exposed_ids") or [])
                    if kind == "error":
                        message = event.get("message", "the reasoning loop failed")
                        errors.append(message)
                        degraded = f"the reasoning loop did not complete: {message}"
                    else:
                        errors.append(event.get("message", "step limit reached"))
                        degraded = ("the reasoning loop hit its step limit without "
                                    "reaching a conclusion")
                        steps_used = event.get("steps_used", 0)
                    await self._emit("reasoning", event)
                    break

                # An observation carrying a table is the payload of an extraction
                # or aggregation answer; keep the most recent one.
                if kind == "observation" and event.get("table"):
                    table = DataTable(**event["table"])

                await self._emit("reasoning", event)
        finally:
            await loop.aclose()

        return {"raw_answer": raw_answer, "exposed_ids": exposed_ids, "table": table,
                "steps_used": steps_used, "degraded": degraded, "errors": errors,
                "visited": self._enter(state, "reason"),
                "timings_ms": self._timed(state, "reasoning", started)}

    async def fallback(self, state: dict) -> dict:
        """The deterministic answer, when the loop could not supply one.

        Every stage before the model already ran, so a real answer exists even
        with no model at all. Reporting it — clearly marked as the rules' answer
        rather than the agent's — beats returning nothing.
        """
        mode = state.get("mode") or AnswerMode.ROOT_CAUSE
        degraded = state.get("degraded") or (
            "no evidence source was reachable, so the reasoning loop was not run")
        raw = self._p._fallback_answer(mode, state.get("detected_signals") or [],
                                       state.get("ranked_candidates") or [])
        # Which branch sent it here: the reasoning loop only runs when evidence
        # was collected, so having visited it identifies the predecessor.
        came_from = "reason" if "reason" in (state.get("visited") or []) else "evidence"
        why = (reason_route(state)[1] if came_from == "reason"
               else evidence_route(state)[1])
        return {"raw_answer": raw, "degraded": degraded,
                "decisions": self._decide(state, came_from, "fallback", why),
                "visited": self._enter(state, "fallback")}

    async def verify(self, state: dict) -> dict:
        started = time.perf_counter()
        answer: StructuredAnswer = verify_answer(
            raw=state.get("raw_answer") or {},
            mode=state.get("mode") or AnswerMode.ROOT_CAUSE,
            signals=state.get("detected_signals") or [],
            candidates=state.get("ranked_candidates") or [],
            evidence=state["evidence_bundle"],
            windows=state["resolved_windows"],
            exposed_ids=state.get("exposed_ids") or set(),
            table=state.get("table"),
            steps_used=state.get("steps_used") or 0,
            degraded=state.get("degraded"),
        )
        await self._emit("answer", answer.model_dump(mode="json"))
        arrived_from = "fallback" if "fallback" in (state.get("visited") or []) else "reason"
        decisions = state.get("decisions") or []
        if arrived_from == "reason":
            decisions = self._decide(state, "reason", "verify", reason_route(state)[1])
        return {"answer": answer, "decisions": decisions,
                "visited": self._enter(state, "verify"),
                "timings_ms": self._timed(state, "verify", started)}

    async def finish(self, state: dict) -> dict:
        windows, evidence = state["resolved_windows"], state["evidence_bundle"]
        signals = state.get("detected_signals") or []
        candidates = state.get("ranked_candidates") or []
        answer: StructuredAnswer = state["answer"]
        degraded = state.get("degraded")

        evidence_timeline = build_evidence_timeline(windows, signals, evidence)
        await self._emit("evidence_timeline", {
            "window": windows.incident.model_dump(mode="json"),
            "baseline": windows.baseline.model_dump(mode="json") if windows.baseline else None,
            "entries": [e.model_dump(mode="json") for e in evidence_timeline],
            "collapsed_from": evidence.logs.total_documents,
        })

        visited = self._enter(state, "finish")
        result = InvestigationResult(
            id=state["investigation_id"],
            question=state["request"].question,
            plan=state["investigation_plan"],
            windows=windows,
            signals=signals,
            candidates=candidates,
            analysis=Analysis(
                incident_detected=bool(signals),
                severity=self._p._severity(signals),
                category=CauseCategory(answer.cause_category)
                if answer.cause_category else CauseCategory.UNKNOWN,
                chosen_candidate_id=candidates[0].id if candidates else None,
                cause_summary=answer.headline,
                narrative=answer.detail,
                timeline=build_timeline(windows, signals, evidence),
                confidence=answer.confidence,
                evidence_ids=[c.id for c in answer.citations],
                next_steps=[step.label for step in answer.next_steps],
                evidence_gaps=answer.limitations,
                analyst="langgraph" if not degraded else "langgraph (degraded)",
                engine_top_candidate_id=candidates[0].id if candidates else None,
                agrees_with_engine=self._p._agrees(answer, candidates),
            ),
            answer=answer,
            evidence_timeline=evidence_timeline,
            evidence_summary=self._p._evidence_summary(evidence),
            timings_ms=state.get("timings_ms") or {},
            errors=state.get("errors") or [],
            graph_path=visited,
            graph_decisions=state.get("decisions") or [],
        )
        return {"result": result, "visited": visited}

    # ------------------------------------------------------------- routing
    # Routers are pure: LangGraph calls them to pick an edge and discards
    # anything they write, so the reason a branch was taken is recorded by the
    # node the branch lands on, using the same helper the router used. That way
    # the drawn path and the executed path are computed from one function.
    def route_after_evidence(self, state: dict) -> str:
        return evidence_route(state)[0]

    def route_after_reason(self, state: dict) -> str:
        return reason_route(state)[0]


def evidence_route(state: dict) -> tuple[str, str]:
    """Skip the model when there is nothing for it to reason over.

    Every source unavailable is not the same as every source empty: an
    OpenSearch that refused the query and an OpenSearch that returned zero
    documents look identical to a model, and it will narrate the first as if it
    were the second. The rules can at least say "nothing was reachable".
    """
    statuses = state["evidence_bundle"].statuses()
    if statuses and all(s == "unavailable" for s in statuses.values()):
        return "fallback", (
            f"every evidence source was unavailable "
            f"({', '.join(sorted(statuses))}), so there was nothing for the "
            f"reasoning loop to read")
    available = [name for name, s in (statuses or {}).items() if s != "unavailable"]
    return "signals", f"evidence collected from {', '.join(sorted(available)) or 'no source'}"


def reason_route(state: dict) -> tuple[str, str]:
    if state.get("degraded") or not state.get("raw_answer"):
        return "fallback", (state.get("degraded")
                            or "the reasoning loop returned no answer")
    return "verify", f"the loop concluded after {state.get('steps_used') or 0} step(s)"
