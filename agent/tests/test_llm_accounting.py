"""
What the run reports about the model it used.

The three figures shown beside an investigation — how many round trips it made,
how large the biggest prompt was against the context window, and which model
served it — are not recoverable from the answer afterwards. A confident
paragraph looks the same whether it came from one call or eight, and whether the
prompt that produced it was complete or silently cut at the head.
"""
from __future__ import annotations

import json

import pytest

from app.llm import telemetry
from app.llm.base import LLMResponse
from app.llm.factory import describe_context_window
from app.llm.ollama import OllamaClient
from tests.test_pipeline import (
    COUNTS, PLAN, FakeLogTool, FakeTool, ScriptedLLM, ask, build,
    dependency_outage_evidence,
)


@pytest.mark.asyncio
async def test_every_model_round_trip_is_counted_against_the_run():
    logs, events, metrics = dependency_outage_evidence()
    llm = ScriptedLLM(
        PLAN,
        json.dumps({"thought": "look deeper", "action": "get_dependencies",
                    "action_input": {"service_name": "all"}, "is_finished": False}),
        json.dumps({"thought": "done", "action": None, "is_finished": True,
                    "answer": {"headline": "payment-db failed", "confidence": 0.7}}),
    )
    pipeline = build(llm, logs, events, metrics, COUNTS)

    usage, result = None, None
    async for event in pipeline.run(ask()):
        if event.stage == "llm":
            usage = event.data
        if event.stage == "result":
            result = event.data

    assert usage is not None, "the model's cost must be streamed with the run"
    # Counted at the client boundary, so it cannot drift from what was actually
    # sent — no provider has to remember to report itself.
    assert usage["requests"] == llm.calls
    assert usage["requests_by_stage"] == {"plan": 1, "reasoning": llm.calls - 1}
    assert usage["failed_requests"] == 0
    assert result["llm"] == usage


@pytest.mark.asyncio
async def test_a_failed_call_is_still_a_call():
    """A run that spent three round trips failing is slow for a reason, and the
    figure that would explain it must not quietly exclude them."""
    meter = telemetry.LLMMeter(model="m")
    token = telemetry.bind(meter)
    try:
        class Broken(OllamaClient):
            async def generate(self, *, system, prompt, schema=None):
                raise RuntimeError("upstream refused")

        client = Broken(base_url="http://localhost:11434", model="m")
        with pytest.raises(RuntimeError):
            await client.generate(system="s", prompt="p")
    finally:
        telemetry.unbind(token)

    assert meter.request_count == 1
    assert meter.snapshot()["failed_requests"] == 1


def test_the_context_window_is_reported_only_when_it_is_known():
    """Ollama's window is a setting this process chooses and the server
    truncates past it silently; a hosted model's is often not published at all.
    Guessing one would make the figure that warns about truncation misleading."""
    ollama = OllamaClient(base_url="http://localhost:11434", model="qwen2.5-coder")
    assert describe_context_window(ollama) == ollama.num_ctx

    class Unknown:
        model = "some-local-finetune"

    assert describe_context_window(Unknown()) == 0
    assert telemetry.LLMMeter(context_window=0).snapshot()["peak_context_used"] is None


def test_the_reported_usage_is_the_peak_prompt_not_the_sum():
    """Each call sends a fresh prompt, so what decides whether anything was
    truncated is the largest single one, never the total across the run."""
    meter = telemetry.LLMMeter(context_window=16384)
    meter.record(telemetry.LLMCall(stage="plan", model="m", prompt_tokens=900,
                                   output_tokens=80))
    meter.record(telemetry.LLMCall(stage="reasoning", model="m", prompt_tokens=9_000,
                                   output_tokens=120))
    snapshot = meter.snapshot()

    assert snapshot["peak_prompt_tokens"] == 9_000
    assert snapshot["prompt_tokens"] == 9_900
    assert snapshot["peak_context_used"] == round(9_000 / 16384, 4)


@pytest.mark.asyncio
async def test_a_truncated_prompt_keeps_its_token_count_through_the_failure():
    """The call that truncates is the largest prompt the run sent, and the one
    the reader most needs the number for."""
    from app.llm.base import PromptTruncated

    meter = telemetry.LLMMeter(context_window=2048)
    token = telemetry.bind(meter)
    try:
        class Truncating(OllamaClient):
            async def generate(self, *, system, prompt, schema=None):
                failure = PromptTruncated("cut")
                failure.llm_response = LLMResponse(text="", prompt_tokens=2040,
                                                   model="m", truncated=True)
                raise failure

        with pytest.raises(PromptTruncated):
            await Truncating(base_url="http://x", model="m").generate(system="s", prompt="p")
    finally:
        telemetry.unbind(token)

    snapshot = meter.snapshot()
    assert snapshot["peak_prompt_tokens"] == 2040
    assert snapshot["failed_requests"] == 1
