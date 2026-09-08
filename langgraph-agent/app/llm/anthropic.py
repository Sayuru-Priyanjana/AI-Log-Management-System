"""
A client for the Anthropic Messages API.

Kept separate from the OpenAI-compatible client rather than shimmed onto it: the
request shape differs (system prompt is a top-level field, not a message), and so
does the way structured output is requested. Anthropic has no `response_format`,
so a schema is enforced by declaring it as a tool and forcing that tool — which
is stricter than asking for JSON in the prompt, not weaker.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from app.config import settings
from app.llm.base import LLMClient, LLMResponse, LLMUnavailable

logger = logging.getLogger(__name__)

API_VERSION = "2023-06-01"
_TOOL_NAME = "respond"


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url
                         or "https://api.anthropic.com").rstrip("/")
        self.model = model or settings.llm_model or "claude-sonnet-5"
        self._api_key = api_key or settings.llm_api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.llm_timeout,
            headers={
                "content-type": "application/json",
                "anthropic-version": API_VERSION,
                **({"x-api-key": self._api_key} if self._api_key else {}),
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            response = await self._client.get("/v1/models")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def _payload(self, system: str, prompt: str, schema: dict | None) -> dict:
        """The request, with the reusable half marked as cacheable.

        A ReAct investigation makes up to eight calls that differ only in their
        tail. Everything before that — the system prompt, which carries the full
        tool schema, and the forced-response tool definition — is byte-identical
        on every one of them, and identical again for the next question in the
        same thread. Without a cache marker each call re-reads all of it at full
        price; a run's prompt cost is then roughly `steps x prefix`, and the
        prefix is the largest part.

        The marker goes on the *last* cacheable block, because a breakpoint
        caches everything above it: one marker on the system prompt covers the
        tool definitions too when they are declared before it, so only one of
        the four allowed breakpoints is spent.

        The user turn is deliberately not marked. It is the part that changes,
        and a breakpoint there would write a new cache entry on every step —
        paying the write premium for something never read back.
        """
        cache = settings.llm_prompt_caching
        payload: dict = {
            "model": self.model,
            # A plain string when caching is off, so a provider or gateway that
            # does not understand the block form is unaffected by a setting the
            # deployment never turned on.
            "system": ([{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}]
                       if cache else system),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": settings.llm_max_output_tokens,
            "temperature": settings.llm_temperature,
        }
        if schema is not None:
            payload["tools"] = [{
                "name": _TOOL_NAME,
                "description": "Return the answer in the required structure.",
                "input_schema": schema,
            }]
            payload["tool_choice"] = {"type": "tool", "name": _TOOL_NAME}
        return payload

    @staticmethod
    def _text_from(content: list[dict]) -> str:
        """The reply, as a JSON string whatever form it arrived in.

        A forced tool call returns parsed arguments; a plain reply returns text.
        Every caller here parses JSON, so the tool input is re-serialised rather
        than handed back as a dict — one shape out, whichever way it came in.
        """
        for block in content:
            if block.get("type") == "tool_use" and block.get("name") == _TOOL_NAME:
                return json.dumps(block.get("input") or {})
        return "".join(b.get("text", "") for b in content if b.get("type") == "text")

    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post("/v1/messages",
                                               json=self._payload(system, prompt, schema))
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"Cannot reach {self.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                f"{self.base_url} timed out after {settings.llm_timeout}s."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMUnavailable(
                f"{self.base_url} returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Request to {self.base_url} failed: {exc}") from exc

        data = response.json()
        usage = data.get("usage") or {}
        result = LLMResponse(
            text=self._text_from(data.get("content") or []),
            # `input_tokens` counts only what was NOT served from cache, so the
            # two cache figures are additions to it rather than a subset of it.
            prompt_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_prompt_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            duration_ms=(time.perf_counter() - started) * 1000,
            model=data.get("model") or self.model,
        )
        if data.get("stop_reason") == "max_tokens":
            result.warnings.append(
                f"the reply was cut off at {settings.llm_max_output_tokens} output tokens; "
                f"raise LLM_MAX_OUTPUT_TOKENS if answers look truncated"
            )
        logger.info("LLM call (%s): %d prompt tokens (%d cached, %d written), "
                    "%d output tokens, %.0fms",
                    self.model, result.prompt_tokens, result.cached_prompt_tokens,
                    result.cache_write_tokens, result.output_tokens, result.duration_ms)
        return result
