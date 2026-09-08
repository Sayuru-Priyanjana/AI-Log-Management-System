"""
What a run costs the model, and why the transcript is shaped the way it is.

The ReAct loop re-sends the whole investigation log on every step. Two
consequences follow, and they pull in opposite directions:

  * an observation is not paid for once, it is paid for on every remaining
    step — so each one has to be bounded;
  * the prompt's *prefix* is identical from one step to the next, which is what
    lets a provider serve it from cache instead of re-reading it — so the
    transcript must only ever be appended to.

Bounding at the point of appending satisfies both. Compacting older entries
instead would save a few tokens per step and lose the cache on all of them,
which is the more expensive mistake by an order of magnitude. These tests pin
that property, because it is invisible in any output and easy to break with a
change that looks like an optimisation.
"""
from __future__ import annotations

import json

import pytest

from app.agents.react import ReActAgent
from app.config import settings
from app.models.plan import Intent
from tests.test_react_investigates import ANSWER, ScriptedLLM, collect


SEARCH = {"thought": "look at the logs", "action": "search_logs",
          "action_input": {"query": "timeout"}, "is_finished": False, "answer": None}
DEPS = {"thought": "check the graph", "action": "get_dependencies",
        "action_input": {"service_name": "all"}, "is_finished": False, "answer": None}
TIMELINE = {"thought": "and the order", "action": "get_timeline",
            "action_input": {}, "is_finished": False, "answer": None}


def budget_free(prompt: str) -> str:
    """The prompt without its trailing step-budget line.

    That line is the one part that legitimately changes each step, and it is at
    the *end* precisely so it cannot disturb the prefix above it.
    """
    return prompt.split("\n[Step ")[0]


@pytest.mark.asyncio
async def test_the_transcript_is_only_ever_appended_to():
    """The property prompt caching depends on.

    Every step's prompt must begin with the previous step's prompt, byte for
    byte. When that holds, a provider re-reads only the newest lines — the
    system prompt, the tool schema and every earlier observation are served from
    cache. When it breaks, every step pays full price and nothing in the token
    totals says so.
    """
    llm = ScriptedLLM(SEARCH, DEPS, TIMELINE, ANSWER)
    await collect(ReActAgent(llm, max_steps=5))

    assert len(llm.prompts) >= 3, "need several steps for the property to mean anything"
    for earlier, later in zip(llm.prompts, llm.prompts[1:]):
        assert later.startswith(budget_free(earlier)), (
            "step N+1's prompt must extend step N's rather than rewrite it; "
            "rewriting invalidates the cached prefix on every step at once"
        )


@pytest.mark.asyncio
async def test_the_step_budget_is_the_only_thing_that_moves():
    """It changes every step, so it sits at the very end where it costs one
    cache miss of a few tokens rather than invalidating the transcript."""
    llm = ScriptedLLM(SEARCH, DEPS, ANSWER)
    await collect(ReActAgent(llm, max_steps=5))

    for index, prompt in enumerate(llm.prompts, start=1):
        assert f"[Step {index} of 5." in prompt
        assert prompt.rindex("[Step ") > len(prompt) - 120, \
            "the changing line belongs at the end of the prompt, not in the middle"


@pytest.mark.asyncio
async def test_one_huge_observation_does_not_dominate_every_later_step(monkeypatch):
    """A `search_logs` returning hundreds of lines used to be re-sent in full on
    every remaining step, so its cost was multiplied by the steps left."""
    monkeypatch.setattr(settings, "react_observation_chars", 300)

    llm = ScriptedLLM(SEARCH, DEPS, TIMELINE, ANSWER)
    await collect(ReActAgent(llm, max_steps=5))

    growth = [len(budget_free(b)) - len(budget_free(a))
              for a, b in zip(llm.prompts, llm.prompts[1:])]
    assert growth, "expected several steps"
    assert max(growth) < 1200, (
        f"one step added {max(growth)} characters; each observation is bounded so "
        f"the transcript grows linearly and predictably"
    )
    assert all(g > 0 for g in growth), "each step still adds what it learned"


@pytest.mark.asyncio
async def test_the_two_windows_and_the_present_are_stated_once_in_the_header():
    """Stated in the fixed header rather than repeated per step.

    The header is part of the cached prefix, so putting them there costs their
    tokens once for the whole run instead of once per step.
    """
    llm = ScriptedLLM(ANSWER, DEPS, ANSWER)
    await collect(ReActAgent(llm, max_steps=4))

    first = llm.prompts[0]
    assert first.count("Whole period asked about:") == 1
    assert first.count("Stretch analysed in depth against a baseline:") == 1


@pytest.mark.asyncio
async def test_the_seeded_signal_read_is_not_charged_as_a_step():
    """The measured facts are put in front of the model before it chooses.

    Left to itself the loop opens with a log search, gets nothing and concludes;
    one live run answered "what are the metric spikes?" with "no log patterns
    matched" while six signals sat uncollected. Seeding it costs no round trip,
    which is why it is worth doing unconditionally.
    """
    llm = ScriptedLLM(DEPS, ANSWER)
    events = await collect(ReActAgent(llm, max_steps=4))

    seeded = [e for e in events
              if e.get("type") == "observation" and e.get("automatic")]
    assert seeded, "the signals are read before the first model call"
    assert all(e["step"] == 0 for e in seeded)
    # One call per step, and the seeded read is not one of them.
    steps = [e for e in events if e.get("type") == "action"]
    assert len(llm.prompts) == len(steps) + 1


@pytest.mark.asyncio
async def test_a_repeated_tool_call_costs_no_query(monkeypatch):
    """The tools are pure functions of an evidence set that does not change
    mid-run, so re-asking cannot produce new information — only new tokens."""
    llm = ScriptedLLM(DEPS, DEPS, ANSWER)
    events = await collect(ReActAgent(llm, max_steps=4))

    observations = [e["text"] for e in events
                    if e.get("type") == "observation" and not e.get("automatic")]
    assert any("already called" in text for text in observations), \
        "the second identical call is answered from the guard, not re-run"
