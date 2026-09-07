"""
The conversation lives in the graph, not only in the caller.

Follow-up memory used to be entirely the browser's job: the UI assembled the
earlier questions and answers and sent them with each request. That works right
up until the caller forgets, truncates, or is a different client — and the agent
has no way to tell the difference between "this is the first question" and "you
were not told about the previous four".

The graph now keeps its own `memory` channel, restored per `thread_id` by the
checkpointer, and merges it with whatever the caller sent.
"""
from __future__ import annotations

import json

import pytest

from app.agents.orchestrator import OrchestratorAgent
from app.agents.react import ReActAgent
from app.models.evidence import EventEvidence, LogEvidence, MetricEvidence
from app.models.plan import ChatMessage, InvestigationRequest
from app.pipeline.run import InvestigationPipeline
from tests.test_pipeline import (
    COUNTS, PLAN, FakeLogTool, FakeRegistry, FakeTool, ScriptedLLM,
    dependency_outage_evidence,
)


def build(llm, logs, events, metrics):
    return InvestigationPipeline(
        log_tool=FakeLogTool(logs, COUNTS),
        event_tool=FakeTool(events),
        metric_tool=FakeTool(metrics),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm, max_steps=3),
        registry=FakeRegistry(),
    )


def ask(question, thread_id=None, history=()):
    return InvestigationRequest(
        system_id="shopdemo", environment="staging", question=question,
        thread_id=thread_id,
        chat_history=[ChatMessage(role=r, content=c) for r, c in history],
    )


ANSWER = json.dumps({
    "thought": "done", "action": None, "is_finished": True,
    "answer": {"headline": "payment-db failed", "root_cause_service": "payment-db",
               "detail": "It was OOM-killed.", "confidence": 0.7},
})


async def run(pipeline, request):
    payloads = {}
    async for event in pipeline.run(request):
        payloads[event.stage] = event.data
    return payloads


def planner_prompts(llm):
    """Only the classifier's calls.

    Indexing `llm.prompts` by position is fragile: the ReAct loop pushes back
    once on a root-cause answer that cited no tool call, so the number of calls
    per turn is not fixed. The planner is identified by its own system prompt.
    """
    return [prompt for prompt, system in zip(llm.prompts, llm.systems)
            if system.startswith("You classify operational questions")]


# Enough scripted replies for several turns, including the loop's push-back.
def script(turns):
    return [PLAN, ANSWER, ANSWER] * turns


@pytest.mark.asyncio
async def test_a_follow_up_is_remembered_even_when_the_caller_sends_nothing():
    """The case the client-side history cannot cover.

    The second question arrives with an empty `chat_history` — as it would from
    a reloaded tab, a different browser, or any other client — and the agent
    still answers it as the second turn of a conversation.
    """
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(2))
    pipeline = build(llm, logs, events, metrics)

    first = await run(pipeline, ask("why is checkout failing?", thread_id="thr-a"))
    assert first["plan"]["remembered_turns"] == 0

    second = await run(pipeline, ask("and what caused that?", thread_id="thr-a"))
    assert second["plan"]["remembered_turns"] == 1, (
        "the graph must recall the first turn without being told about it"
    )

    # The planner was actually given it, not just counted it.
    second_plan = planner_prompts(llm)[1]
    assert "why is checkout failing" in second_plan
    assert "payment-db failed" in second_plan


@pytest.mark.asyncio
async def test_memory_accumulates_across_several_turns():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(3))
    pipeline = build(llm, logs, events, metrics)

    counts = []
    for question in ("first?", "second?", "third?"):
        payloads = await run(pipeline, ask(question, thread_id="thr-b"))
        counts.append(payloads["plan"]["remembered_turns"])

    assert counts == [0, 1, 2]


@pytest.mark.asyncio
async def test_separate_threads_do_not_share_memory():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(3))
    pipeline = build(llm, logs, events, metrics)

    await run(pipeline, ask("first?", thread_id="thr-c"))
    await run(pipeline, ask("second?", thread_id="thr-c"))
    other = await run(pipeline, ask("unrelated?", thread_id="thr-d"))

    assert other["plan"]["remembered_turns"] == 0


@pytest.mark.asyncio
async def test_a_question_with_no_thread_is_a_conversation_of_one():
    """Thread-less runs must not pool into a shared bucket, which would hand
    every one of them the previous unrelated question as context."""
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(2))
    pipeline = build(llm, logs, events, metrics)

    await run(pipeline, ask("standalone one"))
    second = await run(pipeline, ask("standalone two"))

    assert second["plan"]["remembered_turns"] == 0


@pytest.mark.asyncio
async def test_the_caller_still_supplies_the_history_a_cold_agent_lacks():
    """After a restart the graph has never seen the thread. The client's copy is
    what keeps the answer from being amnesiac, so it must still be used."""
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(1))
    pipeline = build(llm, logs, events, metrics)

    payloads = await run(pipeline, ask(
        "and then?", thread_id="thr-never-seen",
        history=[("user", "what broke?"), ("assistant", "payment-db did")]))

    assert payloads["plan"]["remembered_turns"] == 0
    assert "what broke?" in planner_prompts(llm)[0], (
        "the caller's history must reach the planner when the graph has none"
    )


@pytest.mark.asyncio
async def test_a_new_turn_does_not_inherit_the_previous_run_state():
    """Everything except memory is per-run.

    With a checkpointer the next invoke starts from the saved channels, so a
    `visited` list that was not reset would grow until the drawn graph claimed
    every node ran twice.
    """
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(*script(2))
    pipeline = build(llm, logs, events, metrics)

    first = await run(pipeline, ask("first?", thread_id="thr-e"))
    second = await run(pipeline, ask("second?", thread_id="thr-e"))

    assert first["result"]["graph_path"] == second["result"]["graph_path"]
    assert len(second["result"]["graph_path"]) == len(set(second["result"]["graph_path"]))
    assert second["result"]["errors"] == []
