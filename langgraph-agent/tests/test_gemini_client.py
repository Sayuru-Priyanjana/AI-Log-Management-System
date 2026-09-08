"""
Gemini's own API, and the two things it is here for.

The OpenAI-compatible shim this replaced reported no cached-token figure at all,
so a run that re-read the same 2,000-token system prompt on every one of its four
calls was indistinguishable from one that read it once. These pin the reporting,
the schema translation, and — most of all — the distinction between the two ways
a cache request can be refused, which is the part that silently disables the
whole feature when it is got wrong.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.llm.base import LLMUnavailable
from app.llm.gemini import GeminiClient, to_gemini_schema


def wire(client: GeminiClient, handler) -> GeminiClient:
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json", "x-goog-api-key": "test-key"},
    )
    return client


def reply(text="{}", *, prompt=100, output=10, cached=0, thoughts=0,
          finish="STOP") -> dict:
    usage = {"promptTokenCount": prompt, "candidatesTokenCount": output}
    if cached:
        usage["cachedContentTokenCount"] = cached
    if thoughts:
        usage["thoughtsTokenCount"] = thoughts
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": usage,
        "modelVersion": "gemini-3.5-flash-lite",
    }


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_prompt_caching", True)
    monkeypatch.setattr(settings, "gemini_cache_min_tokens", 1024)


# ------------------------------------------------------------------ schema
def test_a_nullable_field_is_translated_rather_than_passed_through():
    """JSON Schema spells nullable as a type union; Gemini spells it as a flag,
    and rejects the whole request rather than ignoring the form it does not
    know."""
    out = to_gemini_schema({
        "type": "object",
        "properties": {"service": {"type": ["string", "null"]},
                       "duration": {"type": "string", "enum": ["15m", "1h"]}},
        "required": ["duration"],
    })
    assert out["type"] == "OBJECT"
    assert out["properties"]["service"] == {"type": "STRING", "nullable": True}
    assert out["properties"]["duration"]["enum"] == ["15m", "1h"]
    assert out["required"] == ["duration"]


def test_a_free_form_object_is_declined_rather_than_misdescribed():
    """`action_input` carries whatever arguments the chosen tool takes.

    Gemini cannot express that, and an object with no properties does not mean
    "anything" — it means "nothing". Emitting one would constrain the model away
    from every correct answer, silently, which is worse than sending no schema.
    """
    assert to_gemini_schema({"type": ["object", "null"]}) is None
    assert to_gemini_schema({
        "type": "object",
        "properties": {"action_input": {"type": ["object", "null"]}},
    }) is None


def test_the_projects_real_schemas_translate_as_intended():
    from app.agents.orchestrator import RESPONSE_SCHEMA as PLAN
    from app.agents.react import RESPONSE_SCHEMA as REACT

    plan = to_gemini_schema(PLAN)
    assert plan is not None, "the planner's schema is worth enforcing and can be"
    assert plan["properties"]["intent"]["enum"]

    assert to_gemini_schema(REACT) is None, (
        "the reasoning schema holds a free-form action_input, so the loop asks "
        "for JSON without a schema rather than for the wrong schema")


def test_unsupported_keywords_are_dropped_not_forwarded():
    out = to_gemini_schema({
        "type": "object", "additionalProperties": False, "$schema": "http://x",
        "properties": {"a": {"type": "string", "default": "z"}},
    })
    assert "additionalProperties" not in out and "$schema" not in out
    assert "default" not in out["properties"]["a"]


# ------------------------------------------------------------------ usage
@pytest.mark.asyncio
async def test_the_cached_half_of_a_prompt_is_reported_and_not_billed_twice():
    """Gemini counts cached tokens *inside* promptTokenCount.

    Anthropic counts them beside it. Left as they arrive, the same run would
    report different totals depending on which provider answered.
    """
    client = wire(GeminiClient(model="gemini-3.5-flash-lite"),
                  lambda _r: httpx.Response(200, json=reply(prompt=5000, cached=4200)))
    client._cache_unavailable = "test: skip cache creation"

    result = await client.generate(system="s", prompt="p")
    assert result.cached_prompt_tokens == 4200
    assert result.prompt_tokens == 800, "the cached prefix must not be billed twice"
    assert round(result.cache_hit_ratio, 3) == 0.84
    await client.close()


@pytest.mark.asyncio
async def test_thinking_tokens_are_counted_as_output():
    """A thinking model bills its reasoning even though none of it reaches the
    reply. Dropping it makes the output figure quietly wrong."""
    client = wire(GeminiClient(),
                  lambda _r: httpx.Response(200, json=reply(output=40, thoughts=260)))
    client._cache_unavailable = "test"
    result = await client.generate(system="s", prompt="p")
    assert result.output_tokens == 300
    await client.close()


@pytest.mark.asyncio
async def test_a_truncated_reply_is_flagged():
    client = wire(GeminiClient(),
                  lambda _r: httpx.Response(200, json=reply(finish="MAX_TOKENS")))
    client._cache_unavailable = "test"
    result = await client.generate(system="s", prompt="p")
    assert any("cut off" in w for w in result.warnings)
    await client.close()


@pytest.mark.asyncio
async def test_a_blocked_prompt_says_so_rather_than_returning_empty_text():
    """An empty string parses as unusable JSON three layers up, where the reason
    is no longer available."""
    client = wire(GeminiClient(), lambda _r: httpx.Response(
        200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    client._cache_unavailable = "test"
    with pytest.raises(LLMUnavailable, match="SAFETY"):
        await client.generate(system="s", prompt="p")
    await client.close()


# ------------------------------------------------------------------ caching
@pytest.mark.asyncio
async def test_a_cacheable_system_prompt_is_cached_once_and_reused():
    """The system prompt is the same 2,000 tokens on every call of every run, so
    it is created once and referenced thereafter — and the instructions must not
    also be sent inline, which Gemini rejects and which would defeat the point."""
    creates, generates = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/cachedContents"):
            creates.append(body)
            return httpx.Response(200, json={
                "name": "cachedContents/abc123",
                "usageMetadata": {"totalTokenCount": 2082}})
        generates.append(body)
        return httpx.Response(200, json=reply(prompt=2500, cached=2082))

    client = wire(GeminiClient(), handler)
    system = "S" * 8000                      # comfortably over the minimum

    for _ in range(3):
        await client.generate(system=system, prompt="p", schema=None)

    assert len(creates) == 1, "the cache is created once, not per call"
    assert len(generates) == 3
    for body in generates:
        assert body["cachedContent"] == "cachedContents/abc123"
        assert "systemInstruction" not in body, (
            "the instructions live in the cache; sending both is rejected")
    await client.close()


@pytest.mark.asyncio
async def test_a_free_tier_key_switches_caching_off_once_and_keeps_working():
    """The measured behaviour of the key this runs against today.

    `TotalCachedContentStorageTokensPerModelFreeTier limit=0` is permanent, so
    it must be discovered once rather than re-paid on every request — and it
    must not stop the investigation.
    """
    attempts = {"create": 0, "generate": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cachedContents"):
            attempts["create"] += 1
            return httpx.Response(429, json={"error": {
                "code": 429,
                "message": "TotalCachedContentStorageTokensPerModelFreeTier limit "
                           "exceeded for model gemini-3.5-flash-lite: limit=0",
                "status": "RESOURCE_EXHAUSTED"}})
        attempts["generate"] += 1
        return httpx.Response(200, json=reply(prompt=2500))

    client = wire(GeminiClient(), handler)
    system = "S" * 8000

    for _ in range(4):
        result = await client.generate(system=system, prompt="p")
        assert result.cached_prompt_tokens == 0

    assert attempts["create"] == 1, "a permanent refusal is asked once, not four times"
    assert attempts["generate"] == 4, "and the run proceeds regardless"
    await client.close()


@pytest.mark.asyncio
async def test_a_prompt_too_small_to_cache_does_not_disable_caching_for_the_rest():
    """The bug this test exists for.

    The planner's system instruction is a dozen tokens, far below Gemini's cache
    minimum. Treating its rejection as "caching is unavailable" switched the
    feature off for the 2,000-token reasoning prompt — the only one worth
    caching — so the feature appeared to do nothing at all.
    """
    creates = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cachedContents"):
            creates.append(json.loads(request.content))
            return httpx.Response(200, json={
                "name": "cachedContents/big",
                "usageMetadata": {"totalTokenCount": 2082}})
        return httpx.Response(200, json=reply(prompt=2500, cached=2082))

    client = wire(GeminiClient(), handler)

    # A tiny planner prompt: never even offered to the API.
    await client.generate(system="You classify questions. Return JSON.", prompt="p")
    assert creates == [], "a prompt under the minimum is not worth a round trip"
    assert client._cache_unavailable is None, "and must not disable the feature"

    # The large reasoning prompt still gets cached.
    result = await client.generate(system="S" * 8000, prompt="p")
    assert len(creates) == 1
    assert result.cached_prompt_tokens == 2082
    await client.close()


@pytest.mark.asyncio
async def test_an_expired_cache_falls_back_to_sending_the_prompt_inline():
    """A cache that lapsed server-side returns 403 on the next call. Failing the
    call would lose an investigation to something entirely recoverable."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cachedContents"):
            return httpx.Response(200, json={"name": "cachedContents/gone",
                                             "usageMetadata": {"totalTokenCount": 2082}})
        body = json.loads(request.content)
        seen.append(body)
        if "cachedContent" in body:
            return httpx.Response(403, json={"error": {"message": "cache not found"}})
        return httpx.Response(200, json=reply(prompt=2500))

    client = wire(GeminiClient(), handler)
    result = await client.generate(system="S" * 8000, prompt="p")

    assert len(seen) == 2, "the rejected call is retried without the cache"
    assert "cachedContent" in seen[0] and "systemInstruction" not in seen[0]
    assert "systemInstruction" in seen[1]
    assert result.prompt_tokens == 2500
    await client.close()


@pytest.mark.asyncio
async def test_caching_can_be_turned_off_entirely():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=reply())

    settings.llm_prompt_caching = False
    try:
        client = wire(GeminiClient(), handler)
        await client.generate(system="S" * 8000, prompt="p")
        assert not any(p.endswith("/cachedContents") for p in calls)
        await client.close()
    finally:
        settings.llm_prompt_caching = True


# ------------------------------------------------------------------ wiring
def test_the_factory_builds_the_native_client_for_gemini(monkeypatch):
    """Not the OpenAI-compatible shim, which reports no cache figures at all."""
    from app.llm.factory import build_llm

    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "llm_model", "gemini-3.5-flash-lite")
    monkeypatch.setattr(settings, "llm_base_url", "")
    client = build_llm()
    assert isinstance(client, GeminiClient)
    assert client.provider == "gemini"
    assert "generativelanguage.googleapis.com/v1beta" in client.base_url
    assert "openai" not in client.base_url


def test_the_context_window_is_known_for_the_models_in_use(monkeypatch):
    """It read "window not published by this provider" while the model publishes
    1,048,576 — so the utilisation figure beside every run was blank."""
    from app.llm.factory import describe_context_window

    monkeypatch.setattr(settings, "llm_context_window", 0)
    client = GeminiClient(model="gemini-3.5-flash-lite")
    assert describe_context_window(client) == 1_048_576


def test_the_peak_prompt_is_the_size_sent_not_the_amount_billed():
    """These stopped being the same number when cache reporting arrived.

    A 5,300-token prompt with 4,200 served from cache bills 1,100. Reporting
    that as the peak made a run look like it had used a tenth of the context it
    actually used — and this figure is the truncation early-warning, so a peak
    that shrinks as caching improves hides a run creeping up on its limit.
    """
    from app.llm.telemetry import LLMCall, LLMMeter

    meter = LLMMeter(context_window=16384)
    meter.record(LLMCall(stage="reasoning", model="m",
                         prompt_tokens=1100, cached_prompt_tokens=4200))
    meter.record(LLMCall(stage="reasoning", model="m",
                         prompt_tokens=900, cached_prompt_tokens=0))

    snapshot = meter.snapshot()
    assert snapshot["peak_prompt_tokens"] == 5300, "the largest prompt actually sent"
    assert snapshot["prompt_tokens"] == 2000, "billed stays billed"
    assert snapshot["cached_prompt_tokens"] == 4200
    assert snapshot["peak_context_used"] == round(5300 / 16384, 4)


@pytest.mark.asyncio
async def test_a_refused_cache_is_retried_later_rather_than_given_up_on(monkeypatch):
    """The refusal has been observed to clear on its own.

    The same key returned `limit=0` twice and then started succeeding. Treating
    that as permanent cost every later run its caching over one bad minute, with
    a pod restart as the only cure — so a refusal defers rather than disables.
    """
    import time as _time
    creates = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cachedContents"):
            creates["n"] += 1
            if creates["n"] == 1:
                return httpx.Response(429, json={"error": {
                    "message": "TotalCachedContentStorageTokensPerModelFreeTier "
                               "limit exceeded: limit=0"}})
            return httpx.Response(200, json={
                "name": "cachedContents/later",
                "usageMetadata": {"totalTokenCount": 2082}})
        return httpx.Response(200, json=reply(prompt=2500, cached=2082))

    monkeypatch.setattr(settings, "gemini_cache_retry_seconds", 900)
    client = wire(GeminiClient(), handler)
    system = "S" * 8000

    await client.generate(system=system, prompt="p")
    assert creates["n"] == 1 and client._cache_unavailable is not None

    # Still inside the back-off: not asked again.
    await client.generate(system=system, prompt="p")
    assert creates["n"] == 1, "a refusal must not be re-paid on every call"

    # Once the back-off elapses it tries again, and succeeds.
    monkeypatch.setattr(_time, "time", lambda: client._cache_retry_at + 1)
    result = await client.generate(system=system, prompt="p")
    assert creates["n"] == 2, "the refusal is a quota window, so it is retried"
    assert result.cached_prompt_tokens == 2082
    await client.close()


# ------------------------------------------------- what may be cached at all
@pytest.mark.asyncio
async def test_measurements_go_in_the_per_call_turn_never_the_cached_block():
    """The safety property behind prompt caching, pinned end to end.

    The cache is process-wide and outlives the investigation that created it, so
    whatever is inside it is shown to every later run — including runs about a
    different system, a different window, or someone else's question. That is
    harmless for the loop's rules and its tool schema, which are identical for
    every investigation ever made, and would be a correctness disaster for
    evidence.

    Checking the system prompt for strings like "sig:" does not work: the
    instructions legitimately explain the citation format, and the answer schema
    carries an illustrative ID. What distinguishes instructions from evidence is
    not vocabulary but *variance* — so this runs the loop twice over different
    measurements and asserts the system prompt did not move while the user turn
    did.

    Worth pinning because the tempting next optimisation is to move the evidence
    header into the system prompt to cache another ~1,000 tokens per call. That
    would serve one investigation's measurements to the next.
    """
    from app.agents.react import ReActAgent
    from app.models.evidence import EventEvidence, EvidenceBundle, LogEvidence, MetricEvidence
    from app.models.signals import Severity, Signal, SignalType
    from tests.test_react_investigates import ANSWER, plan_for, windows as react_windows
    from app.models.plan import Intent

    class Recorder:
        def __init__(self):
            self.systems, self.prompts = [], []

        async def generate(self, *, system, prompt, schema=None):
            self.systems.append(system)
            self.prompts.append(prompt)

            class R:
                text = json.dumps(ANSWER)
                prompt_tokens = output_tokens = 0
                duration_ms = 0.0
                model = "recorder"
                warnings: list = []
            return R()

    async def run_with(signal_id: str, service: str, recorder: Recorder):
        agent = ReActAgent(recorder, max_steps=1)
        loop = agent.run(
            plan_for(Intent.INCIDENT_INVESTIGATION), react_windows(),
            EvidenceBundle(logs=LogEvidence(), events=EventEvidence(),
                           metrics=MetricEvidence()),
            [Signal(id=signal_id, type=SignalType.OOM_KILL, severity=Severity.CRITICAL,
                    service=service, description=f"{service} was OOM killed")],
            [],
        )
        async for _ in loop:
            pass
        await loop.aclose()

    recorder = Recorder()
    await run_with("sig:OOM_KILL:payment-db-aaa", "payment-db", recorder)
    await run_with("sig:OOM_KILL:checkout-api-bbb", "checkout-api", recorder)

    assert len(set(recorder.systems)) == 1, (
        "the system prompt must be identical across investigations — it is the "
        "block that gets cached and reused for all of them")

    system = recorder.systems[0]
    assert "payment-db-aaa" not in system and "checkout-api-bbb" not in system, (
        "a measured signal id reached the cached block")

    # …and each run's own measurements did reach that run's user turn.
    assert any("payment-db-aaa" in p for p in recorder.prompts)
    assert any("checkout-api-bbb" in p for p in recorder.prompts)


def test_the_system_prompt_does_not_vary_with_the_investigation():
    """The same guarantee at its source: two constants and a classmethod over a
    static tool list, so there is nothing per-run for it to carry."""
    from app.agents.react import SYSTEM_PROMPT, ANSWER_SCHEMA_HINT
    from app.agents.tool_bindings import ToolBindings

    build = lambda: SYSTEM_PROMPT.format(tools=ToolBindings.schema(),
                                         answer_schema=ANSWER_SCHEMA_HINT)
    assert build() == build()


@pytest.mark.asyncio
async def test_a_changed_system_prompt_is_never_served_from_the_old_cache():
    """Keyed by the prompt itself, so a changed tool schema cannot be answered
    with the previous one still in force."""
    creates = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cachedContents"):
            creates.append(json.loads(request.content))
            return httpx.Response(200, json={
                "name": f"cachedContents/v{len(creates)}",
                "usageMetadata": {"totalTokenCount": 2082}})
        return httpx.Response(200, json=reply(prompt=2500, cached=2082))

    client = wire(GeminiClient(), handler)

    await client.generate(system="A" * 8000, prompt="p")
    await client.generate(system="A" * 8000, prompt="p")
    assert len(creates) == 1, "the same instructions reuse the same cache"

    await client.generate(system="B" * 8000, prompt="p")
    assert len(creates) == 2, "different instructions must not reuse it"
    await client.close()
