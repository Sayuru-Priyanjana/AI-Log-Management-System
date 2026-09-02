"""
A root cause must not be named without looking at anything.

Observed in production: the loop was handed fifteen signals, made **zero tool
calls**, and concluded in one step — blaming an OOM kill whose reported onset was
merely the window edge, and inventing a dependency to explain how it caused a
service that does not call it.

The mechanism was a contradiction in the prompt. The system prompt said "stop as
soon as you can answer", written when every tool returned pre-fetched data and
extra steps genuinely added nothing. That is no longer true, and being in the
system prompt it outranked the mode guidance telling the model to investigate.
"""
from __future__ import annotations

import json

import pytest

from app.agents.react import SYSTEM_PROMPT, ReActAgent
from app.models.analysis import Candidate, CauseCategory, InvestigationWindows
from app.models.domain import TimeWindow
from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, MetricEvidence
from app.models.plan import Intent, InvestigationPlan
from app.models.signals import Severity, Signal, SignalType
from tests.conftest import T0, at


class ScriptedLLM:
    """Replies from a fixed script, recording the prompts it was given."""

    def __init__(self, *replies: dict) -> None:
        self.replies = [json.dumps(r) for r in replies]
        self.prompts: list[str] = []

    async def generate(self, *, system, prompt, schema=None):
        self.prompts.append(prompt)
        text = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]

        class R:
            pass
        r = R(); r.text = text
        r.prompt_tokens = 0; r.output_tokens = 0; r.duration_ms = 0.0
        r.model = "scripted"; r.warnings = []
        return r


def plan_for(intent: Intent) -> InvestigationPlan:
    return InvestigationPlan(
        intent=intent, system_id="cls", system_name="cls", environment="testbed",
        requested_window=TimeWindow(start=T0, end=at(1800)),
        tools=["logs", "events", "metrics"], goal="what is the root cause",
    )


def windows() -> InvestigationWindows:
    return InvestigationWindows(
        requested=TimeWindow(start=T0, end=at(1800)),
        incident=TimeWindow(start=T0, end=at(1800), label="incident"),
        onset=T0, onset_detected=True, method="test",
    )


def signals() -> list[Signal]:
    return [Signal(id="sig:OOM_KILL:prometheus-x", type=SignalType.OOM_KILL,
                   severity=Severity.CRITICAL, service="prometheus",
                   first_seen=T0, description="OOM killed")]


ANSWER = {"thought": "The OOM kill is clearly the cause.", "action": None,
          "action_input": None, "is_finished": True,
          "answer": {"headline": "Prometheus OOM", "root_cause_service": "prometheus"}}
TOOL_CALL = {"thought": "Let me check the call graph.", "action": "get_dependencies",
             "action_input": {"service_name": "all"}, "is_finished": False,
             "answer": None}


async def collect(agent, intent=Intent.INCIDENT_INVESTIGATION):
    events = []
    async for event in agent.run(plan_for(intent), windows(),
                                 EvidenceBundle(logs=LogEvidence(),
                                                events=EventEvidence(),
                                                metrics=MetricEvidence()),
                                 signals(), [Candidate(
                                     id="cand:1", category=CauseCategory.RESOURCE_EXHAUSTION,
                                     hypothesis="prometheus ran out of memory",
                                     service="prometheus")]):
        events.append(event)
    return events


# ------------------------------------------------------------- the prompt
def test_the_prompt_no_longer_tells_the_model_to_stop_early():
    """The instruction that caused it. Kept as a test because it reads like
    harmless advice and would be easy to reintroduce."""
    assert "Stop as soon as you can answer" not in SYSTEM_PROMPT
    assert "extra steps cost time and add nothing" not in SYSTEM_PROMPT


# -------------------------------------------------------------- the guard
@pytest.mark.asyncio
async def test_a_root_cause_concluded_with_no_tool_calls_is_pushed_back():
    llm = ScriptedLLM(ANSWER, TOOL_CALL, ANSWER)
    events = await collect(ReActAgent(llm, max_steps=4))

    notes = [e for e in events if e.get("type") == "note"]
    assert notes, "concluding with no evidence should be refused once"
    assert "without checking any evidence" in notes[0]["message"]

    actions = [e for e in events if e.get("type") == "action"]
    assert actions, "the model should have been given a chance to investigate"
    assert actions[0]["tool"] == "get_dependencies"


@pytest.mark.asyncio
async def test_the_pushback_names_what_to_check():
    """A bare refusal teaches nothing and wastes the step. The message has to say
    which questions are unanswered — whether the blamed service is actually
    upstream, and what preceded the signal's onset."""
    llm = ScriptedLLM(ANSWER, TOOL_CALL, ANSWER)
    await collect(ReActAgent(llm, max_steps=4))

    nudge = llm.prompts[-1]
    assert "get_dependencies" in nudge
    assert "logs_around" in nudge
    assert "not automatically the cause" in nudge


@pytest.mark.asyncio
async def test_an_answer_after_a_real_tool_call_is_accepted():
    """The guard must not block a conclusion that was actually investigated."""
    llm = ScriptedLLM(TOOL_CALL, ANSWER)
    events = await collect(ReActAgent(llm, max_steps=4))

    assert not [e for e in events if e.get("type") == "note"]
    answer = next(e for e in events if e.get("type") == "answer")
    assert answer["answer"]["root_cause_service"] == "prometheus"


@pytest.mark.asyncio
async def test_the_pushback_happens_only_once():
    """A model that keeps concluding must still produce an answer. Refusing every
    time would burn the whole step budget and return nothing."""
    llm = ScriptedLLM(ANSWER)          # concludes, always, with no tool call
    events = await collect(ReActAgent(llm, max_steps=4))

    assert len([e for e in events if e.get("type") == "note"]) == 1
    assert [e for e in events if e.get("type") == "answer"], \
        "a stubborn model should still yield its answer rather than nothing"


@pytest.mark.asyncio
async def test_retrieval_questions_are_not_pushed_back():
    """For "what signals fired?", the seeded list IS the answer. Demanding a tool
    call there would spend a step to re-read what is already on screen."""
    llm = ScriptedLLM({"thought": "The signals are the answer.", "action": None,
                       "action_input": None, "is_finished": True,
                       "answer": {"headline": "1 signal fired"}})
    events = await collect(ReActAgent(llm, max_steps=4), intent=Intent.DATA_EXTRACTION)

    assert not [e for e in events if e.get("type") == "note"]
    assert [e for e in events if e.get("type") == "answer"]


@pytest.mark.asyncio
async def test_a_conclusion_on_the_last_step_is_accepted_rather_than_lost():
    """With no steps left there is nowhere to investigate. A late answer beats
    an exhausted loop that reports nothing."""
    llm = ScriptedLLM(ANSWER)
    events = await collect(ReActAgent(llm, max_steps=1))

    assert not [e for e in events if e.get("type") == "note"]
    assert [e for e in events if e.get("type") == "answer"]
