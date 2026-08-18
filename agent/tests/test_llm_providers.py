from __future__ import annotations

import json

import httpx
import pytest

from app.config import settings
from app.llm.anthropic import AnthropicClient
from app.llm.base import LLMUnavailable
from app.llm.factory import build_llm
from app.llm.ollama import OllamaClient
from app.llm.openai_compatible import OpenAICompatibleClient

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}}


def wire(client, handler):
    """Swaps in a transport so the client is exercised without a network."""
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
        headers=client._client.headers,
    )
    return client


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------
def test_the_default_backend_is_the_local_model(monkeypatch):
    """A local model is the design target: nothing leaves the machine, and an
    investigation that makes eight tool calls costs nothing to run."""
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    assert isinstance(build_llm(), OllamaClient)


def test_each_provider_name_selects_its_client(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    assert isinstance(build_llm(), OpenAICompatibleClient)
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    assert isinstance(build_llm(), AnthropicClient)


def test_an_unknown_provider_falls_back_rather_than_refusing_to_start(monkeypatch):
    """A misspelt provider degrades to the local model. Refusing to boot would
    take the whole investigation surface down over one environment variable."""
    monkeypatch.setattr(settings, "llm_provider", "openaii")
    assert isinstance(build_llm(), OllamaClient)


def test_llm_model_overrides_the_provider_specific_setting(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "llm_model", "llama3.1:8b")
    assert build_llm().model == "llama3.1:8b"


# --------------------------------------------------------------------------
# OpenAI-compatible
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_openai_compatible_reply_is_read_from_the_first_choice(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": '{"answer": "ok"}'},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 40},
        })

    client = wire(OpenAICompatibleClient(model="gpt-4o-mini"), handler)
    result = await client.generate(system="S", prompt="P", schema=SCHEMA)

    assert result.text == '{"answer": "ok"}'
    assert result.prompt_tokens == 900 and result.output_tokens == 40
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "S"}
    assert seen["body"]["response_format"]["type"] == "json_schema"
    await client.close()


@pytest.mark.asyncio
async def test_a_provider_that_rejects_json_schema_falls_back_once(monkeypatch):
    """Providers vary in whether they accept the schema form. Discovering that on
    every call would double the round trips for the rest of the run."""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    formats = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        formats.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "unsupported response_format"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {},
        })

    client = wire(OpenAICompatibleClient(), handler)
    await client.generate(system="S", prompt="P", schema=SCHEMA)
    await client.generate(system="S", prompt="P", schema=SCHEMA)

    assert formats == ["json_schema", "json_object", "json_object"], \
        "the fallback must stick rather than be rediscovered every call"
    await client.close()


@pytest.mark.asyncio
async def test_a_reply_cut_off_by_the_output_budget_says_so(monkeypatch):
    """A hosted API errors on an over-long prompt, but will happily stop
    mid-JSON when it runs out of output tokens — which parses as garbage."""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"headline": "part'},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2048},
        })

    client = wire(OpenAICompatibleClient(), handler)
    result = await client.generate(system="S", prompt="P")
    assert any("cut off" in w for w in result.warnings)
    await client.close()


@pytest.mark.asyncio
async def test_an_http_error_carries_the_provider_response_into_the_message(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "bad-key")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error": "invalid api key"}')

    client = wire(OpenAICompatibleClient(), handler)
    with pytest.raises(LLMUnavailable, match="invalid api key"):
        await client.generate(system="S", prompt="P")
    await client.close()


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_forced_tool_call_comes_back_as_json_text(monkeypatch):
    """Anthropic has no response_format, so a schema is enforced by declaring it
    as a tool and forcing that tool. Every caller parses JSON, so the tool input
    is re-serialised — one shape out, whichever way the reply arrived."""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={
            "model": "claude-sonnet-5",
            "content": [{"type": "tool_use", "name": "respond",
                         "input": {"answer": "ok"}}],
            "usage": {"input_tokens": 800, "output_tokens": 30},
            "stop_reason": "tool_use",
        })

    client = wire(AnthropicClient(), handler)
    result = await client.generate(system="S", prompt="P", schema=SCHEMA)

    assert json.loads(result.text) == {"answer": "ok"}
    assert seen["key"] == "test-key"
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "respond"}
    assert seen["body"]["system"] == "S", "the system prompt is a field, not a message"
    await client.close()


@pytest.mark.asyncio
async def test_a_plain_anthropic_reply_is_read_from_the_text_blocks(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "test-key")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "content": [{"type": "text", "text": "hello "},
                        {"type": "text", "text": "world"}],
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "stop_reason": "end_turn",
        })

    client = wire(AnthropicClient(), handler)
    result = await client.generate(system="S", prompt="P")
    assert result.text == "hello world"
    await client.close()


@pytest.mark.asyncio
async def test_a_hosted_provider_without_a_key_reports_itself_unavailable(monkeypatch):
    """Better a degraded health check than a run that fails on its first call."""
    monkeypatch.setattr(settings, "llm_api_key", "")
    assert await AnthropicClient().available() is False
    assert await OpenAICompatibleClient(base_url="https://api.openai.com/v1").available() is False
