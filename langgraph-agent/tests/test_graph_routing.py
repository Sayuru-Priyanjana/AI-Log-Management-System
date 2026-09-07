"""
The graph's branches, and what the run reports about the model it used.

These are the two things the LangGraph backend has that the deterministic one
does not: a route that can differ between runs, and a record of why it differed.
A graph whose fallback edge is never exercised is a straight line with extra
nodes, so it is exercised here.
"""
from __future__ import annotations

import json

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.agents.react import ReActAgent
from app.llm.base import LLMUnavailable
from app.models.evidence import EventEvidence, LogEvidence, MetricEvidence
from app.pipeline.run import InvestigationPipeline
from tests.test_pipeline import (
    COUNTS, PLAN, FakeLogTool, FakeRegistry, FakeTool, ScriptedLLM, ask,
    dependency_outage_evidence,
)


class UnreachableTool:
    """A source that fails the way a real one does: by raising, not by 404ing."""

    async def histogram(self, plan, window, interval="60s"):
        from tests.conftest import buckets
        return buckets(COUNTS)

    async def collect(self, plan, incident, baseline):
        raise ConnectionError("OpenSearch refused the connection")


def build(llm, *, logs=None, events=None, metrics=None, counts=COUNTS):
    return InvestigationPipeline(
        log_tool=logs if logs is not None else FakeLogTool(LogEvidence(), counts),
        event_tool=events if events is not None else FakeTool(EventEvidence()),
        metric_tool=metrics if metrics is not None else FakeTool(MetricEvidence()),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm, max_steps=4),
        registry=FakeRegistry(),
    )


async def drain(pipeline):
    stages, payloads = [], {}
    async for event in pipeline.run(ask()):
        stages.append(event.stage)
        payloads[event.stage] = event.data
    return stages, payloads


@pytest.mark.asyncio
async def test_a_failing_reasoning_loop_routes_through_the_rule_answer():
    """The model is down. There is still an answer, and the path says where from.

    Without the fallback edge this run ends with no answer at all — the evidence
    and the ranking were already computed, and throwing them away because the
    model was unreachable is the failure mode the branch exists to prevent.
    """
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN, raise_on={2: LLMUnavailable("Ollama is not running")})
    pipeline = build(llm, logs=FakeLogTool(logs, COUNTS),
                     events=FakeTool(events), metrics=FakeTool(metrics))

    stages, payloads = await drain(pipeline)

    result = payloads["result"]
    assert "fallback" in result["graph_path"]
    assert result["graph_path"][-2:] == ["verify", "finish"]
    assert result["analysis"]["analyst"] == "langgraph (degraded)"

    branch = [d for d in result["graph_decisions"] if d["to"] == "fallback"]
    assert branch, "the branch to the rule answer must be recorded, not just taken"
    assert branch[0]["from"] == "reason"
    assert "Ollama is not running" in branch[0]["why"]

    # The answer is the deterministic ranking, and says so rather than passing
    # itself off as the model's.
    assert payloads["answer"]["headline"]
    assert result["errors"], "the model failure must survive into the stored run"


@pytest.mark.asyncio
async def test_unreachable_evidence_skips_the_reasoning_loop_entirely():
    """Every source down: there is nothing to reason over, so the loop is not run.

    A model handed an empty evidence bundle narrates it as "nothing happened",
    which is a different claim from "nothing could be read" and a dangerous one
    to make about a system that may well be on fire.
    """
    llm = ScriptedLLM(PLAN, json.dumps({"thought": "x", "action": None,
                                        "is_finished": True, "answer": {"headline": "hi"}}))
    pipeline = build(llm, logs=UnreachableTool(), events=UnreachableTool(),
                     metrics=UnreachableTool())

    stages, payloads = await drain(pipeline)

    result = payloads["result"]
    assert "reason" not in result["graph_path"], (
        "the loop must not run when no source could be read")
    assert "fallback" in result["graph_path"]
    assert [d for d in result["graph_decisions"]
            if d["from"] == "evidence" and d["to"] == "fallback"]
    # One planning call, and none for a loop that never ran.
    assert payloads["llm"]["requests"] == 1


@pytest.mark.asyncio
async def test_the_run_reports_what_the_model_cost():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "look deeper", "action": "get_dependencies",
                    "action_input": {"service_name": "all"}, "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed", "confidence": 0.7}}),
    )
    pipeline = build(llm, logs=FakeLogTool(logs, COUNTS),
                     events=FakeTool(events), metrics=FakeTool(metrics))

    stages, payloads = await drain(pipeline)
    usage = payloads["llm"]

    assert usage["requests"] == llm.calls, "every round trip is counted, not estimated"
    assert usage["requests_by_stage"]["plan"] == 1
    assert usage["requests_by_stage"]["reasoning"] == llm.calls - 1
    assert usage["failed_requests"] == 0
    assert usage["peak_prompt_tokens"] == 900
    # This client publishes no context window, and the run says so rather than
    # reporting "0% used" — a fabricated ceiling would make the one figure that
    # warns about silent truncation actively misleading.
    assert usage["context_window"] == 0
    assert usage["peak_context_used"] is None
    assert payloads["result"]["llm"] == usage


def test_a_known_context_window_is_reported_and_turned_into_a_percentage():
    """Ollama is the case that matters: the window is a setting this process
    chooses, and the server truncates past it without erroring."""
    from app.llm import telemetry
    from app.llm.factory import describe_context_window
    from app.llm.ollama import OllamaClient

    client = OllamaClient(base_url="http://localhost:11434", model="qwen2.5-coder")
    assert describe_context_window(client) == client.num_ctx

    meter = telemetry.LLMMeter(context_window=client.num_ctx)
    meter.record(telemetry.LLMCall(stage="plan", model="qwen2.5-coder",
                                   prompt_tokens=800))
    meter.record(telemetry.LLMCall(stage="reasoning", model="qwen2.5-coder",
                                   prompt_tokens=12_000))
    snapshot = meter.snapshot()

    # The peak, not the sum: each call is a fresh prompt, so what decides
    # whether anything was truncated is the largest one on its own.
    assert snapshot["peak_prompt_tokens"] == 12_000
    assert snapshot["peak_context_used"] == round(12_000 / client.num_ctx, 4)


def test_a_hosted_model_name_resolves_through_its_dated_suffix():
    """Hosted names carry version suffixes; the window must still be found."""
    from app.llm.factory import describe_context_window

    class Client:
        model = "gpt-4o-mini-2024-07-18"

    assert describe_context_window(Client()) == 128_000


@pytest.mark.asyncio
async def test_the_streamed_topology_matches_the_compiled_graph():
    """The picture the UI draws is the machine, not a diagram beside it."""
    from app.agents.graph import TOPOLOGY

    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(PLAN, json.dumps({"thought": "done", "action": None,
                                        "is_finished": True,
                                        "answer": {"headline": "x", "confidence": 0.5}}))
    pipeline = build(llm, logs=FakeLogTool(logs, COUNTS),
                     events=FakeTool(events), metrics=FakeTool(metrics))

    stages, payloads = await drain(pipeline)

    assert stages[0] == "graph"
    assert payloads["graph"]["engine"] == "langgraph"
    assert payloads["graph"]["nodes"] == TOPOLOGY["nodes"]

    declared = {n["id"] for n in TOPOLOGY["nodes"]}
    for edge in TOPOLOGY["edges"]:
        for end in (edge["from"], edge["to"]):
            assert end in declared or end in ("__start__", "__end__"), end

    # Every node the run visited is a node the drawing knows about; a path
    # naming something the UI cannot place would silently vanish from it.
    assert set(payloads["result"]["graph_path"]) <= declared


@pytest.mark.asyncio
async def test_the_served_topology_is_the_one_the_graph_compiles_from():
    """`/api/agent/graph` is what the UI draws. If it were a second copy of the
    shape, a node added to the graph would go missing from the picture."""
    from app.agents.graph import TOPOLOGY
    from app.api.routes import agent_graph

    served = await agent_graph()
    assert served["engine"] == "langgraph"
    assert served["nodes"] is TOPOLOGY["nodes"]
    assert served["edges"] is TOPOLOGY["edges"]


def test_the_compiled_graph_declares_exactly_the_drawn_nodes():
    """Compiling is the real check that the topology and the node bindings agree:
    a node named in one and missing from the other fails here rather than
    halfway through an investigation."""
    from app.agents.graph import TOPOLOGY, EventQueue, build_graph
    from app.agents.nodes import GraphNodes

    compiled = build_graph(GraphNodes(pipeline=None, queue=EventQueue(), metric_tool=None))
    drawn = {n["id"] for n in TOPOLOGY["nodes"]}
    assert drawn <= set(compiled.get_graph().nodes)
