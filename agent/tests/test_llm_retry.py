"""
Transient-fault retries for hosted model endpoints.

These exist because of a measured failure, not a hypothetical one: probing
Gemini's free tier, one call in six returned 503 "experiencing high demand".
Every HTTP error used to become LLMUnavailable, which the ReAct loop treats as
fatal — so a run that had already resolved its windows, collected evidence from
three sources and spent six steps reasoning would be thrown away because the
provider was briefly busy.

Retrying a 429 is not optimism, it is the documented way to use the API. What
must NOT be retried is a request that is actually wrong — a bad key or a
malformed schema returns the same answer however many times it is sent.
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.llm.openai_compatible import OpenAICompatibleClient


def reply(text: str = '{"ok": true}') -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class Sequence:
    """Serves a fixed list of responses, one per call, and counts the calls."""

    def __init__(self, *statuses: int | tuple[int, dict]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    async def __call__(self, *args, **kwargs) -> httpx.Response:
        item = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        status, headers = (item, {}) if isinstance(item, int) else item
        return httpx.Response(
            status_code=status,
            json=reply() if status == 200 else {"error": {"message": "busy"}},
            headers=headers,
            request=httpx.Request("POST", "https://example.test/chat/completions"),
        )


@pytest.fixture(autouse=True)
def _fast_and_predictable(monkeypatch):
    """No real sleeping, and a key so the client will talk at all."""
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_retry_attempts", 3)
    monkeypatch.setattr(settings, "llm_retry_base_delay", 0.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 0.0)


async def generate(client: OpenAICompatibleClient):
    return await client.generate(system="s", prompt="p")


# ------------------------------------------------------------------ retrying
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
async def test_a_transient_fault_is_retried_rather_than_ending_the_run(monkeypatch, status):
    client = OpenAICompatibleClient(base_url="https://example.test")
    calls = Sequence(status, 200)
    monkeypatch.setattr(client._client, "post", calls)

    result = await generate(client)
    assert result.text == '{"ok": true}'
    assert calls.calls == 2, "should have retried once and then succeeded"


@pytest.mark.asyncio
async def test_retries_are_bounded(monkeypatch):
    """A provider that is down stays down. Retrying forever would hang the
    investigation instead of degrading it to the rule engine."""
    client = OpenAICompatibleClient(base_url="https://example.test")
    calls = Sequence(503)
    monkeypatch.setattr(client._client, "post", calls)

    with pytest.raises(Exception):
        await generate(client)
    assert calls.calls == settings.llm_retry_attempts + 1


@pytest.mark.asyncio
async def test_the_servers_retry_after_is_respected(monkeypatch):
    """Guessing a shorter delay than the server asked for just earns another
    rejection, and on a rate-limited free tier it earns a longer ban."""
    monkeypatch.setattr(settings, "llm_retry_max_delay", 30.0)
    client = OpenAICompatibleClient(base_url="https://example.test")
    response = httpx.Response(
        429, headers={"retry-after": "7"},
        request=httpx.Request("POST", "https://example.test/"))

    assert client._retry_delay(response, attempt=0) == 7.0


@pytest.mark.asyncio
async def test_a_retry_after_beyond_the_ceiling_is_clamped(monkeypatch):
    monkeypatch.setattr(settings, "llm_retry_max_delay", 30.0)
    client = OpenAICompatibleClient(base_url="https://example.test")
    response = httpx.Response(
        429, headers={"retry-after": "600"},
        request=httpx.Request("POST", "https://example.test/"))

    assert client._retry_delay(response, attempt=0) == 30.0


@pytest.mark.asyncio
async def test_backoff_grows_and_is_jittered(monkeypatch):
    """Several investigations retrying in lockstep would rebuild the very spike
    they are backing off from."""
    monkeypatch.setattr(settings, "llm_retry_base_delay", 1.0)
    monkeypatch.setattr(settings, "llm_retry_max_delay", 60.0)
    client = OpenAICompatibleClient(base_url="https://example.test")
    response = httpx.Response(503, request=httpx.Request("POST", "https://example.test/"))

    first = [client._retry_delay(response, 0) for _ in range(20)]
    later = [client._retry_delay(response, 3) for _ in range(20)]

    assert min(later) > max(first), "delay should grow with the attempt number"
    assert len(set(first)) > 1, "identical delays mean no jitter"


# -------------------------------------------------------------- not retrying
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_request_that_is_simply_wrong_is_not_retried(monkeypatch, status):
    """A bad key or a missing model returns the same answer however many times
    it is sent. Retrying wastes the user's quota and delays a clear error."""
    client = OpenAICompatibleClient(base_url="https://example.test")
    calls = Sequence(status)
    monkeypatch.setattr(client._client, "post", calls)

    with pytest.raises(Exception):
        await generate(client)
    assert calls.calls == 1


@pytest.mark.asyncio
async def test_a_schema_rejection_still_falls_back_once(monkeypatch):
    """The 400 path predates retries and must survive them: a provider that
    refuses a JSON schema is retried in json_object mode, not backed off."""
    client = OpenAICompatibleClient(base_url="https://example.test")
    calls = Sequence(400, 200)
    monkeypatch.setattr(client._client, "post", calls)

    result = await client.generate(system="s", prompt="p", schema={"type": "object"})
    assert result.text == '{"ok": true}'
    assert client._schema_supported is False
    assert calls.calls == 2


@pytest.mark.asyncio
async def test_a_transient_fault_during_the_schema_fallback_also_retries(monkeypatch):
    """Both failure modes at once — schema refused, then the retry rate-limited.
    Each has its own remedy and they must not cancel each other out."""
    client = OpenAICompatibleClient(base_url="https://example.test")
    calls = Sequence(400, 429, 200)
    monkeypatch.setattr(client._client, "post", calls)

    result = await client.generate(system="s", prompt="p", schema={"type": "object"})
    assert result.text == '{"ok": true}'
    assert calls.calls == 3
