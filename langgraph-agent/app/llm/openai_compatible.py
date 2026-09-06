"""
A client for any service speaking the OpenAI chat-completions API.

That is one client for most of the hosted options — OpenAI, Groq, OpenRouter,
Together, DeepSeek, Mistral — and for self-hosted vLLM, LM Studio and llama.cpp
as well, because they all settled on the same surface. Only the base URL, the key
and the model name change.

Structured output is requested with `response_format`. Strict JSON-schema mode is
tried first and the client falls back to plain JSON mode once, permanently, if
the server rejects it: providers vary in whether they support the schema form,
and discovering that on every call would cost a doubled round trip each time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time

import httpx

from app.config import settings
from app.llm.base import LLMClient, LLMResponse, LLMUnavailable

logger = logging.getLogger(__name__)

# Statuses that mean "ask again shortly", not "this request is wrong":
#   429  rate limited — routine on a free tier
#   503  "experiencing high demand" — observed once in six calls against Gemini
#   500/502/504  transient gateway faults
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


class OpenAICompatibleClient(LLMClient):
    provider = "openai"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url
                         or "https://api.openai.com/v1").rstrip("/")
        self.model = model or settings.llm_model or "gpt-4o-mini"
        self._api_key = api_key or settings.llm_api_key
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=settings.llm_timeout,
                                         headers=headers)
        self._schema_supported = True

    async def close(self) -> None:
        await self._client.aclose()

    async def available(self) -> bool:
        if not self._api_key and "localhost" not in self.base_url:
            return False
            
        # Gemini's OpenAI wrapper does not support the /models discovery endpoint
        if "generativelanguage" in self.base_url:
            try:
                # Send a tiny ping to verify the key and model exist
                response = await self._client.post(
                    "/chat/completions",
                    json={"model": self.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
                )
                return response.status_code == 200
            except httpx.HTTPError:
                return False

        try:
            response = await self._client.get("/models")
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            # Some gateways do not expose /models but do serve completions. A key
            # that authenticates is a better signal than a missing catalogue.
            return response.status_code not in (401, 403)
        names = {m.get("id", "") for m in response.json().get("data", [])}
        return not names or self.model in names

    def _payload(self, system: str, prompt: str, schema: dict | None) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_output_tokens,
        }
        if schema is not None:
            payload["response_format"] = (
                {"type": "json_schema",
                 "json_schema": {"name": "answer", "strict": False, "schema": schema}}
                if self._schema_supported else {"type": "json_object"}
            )
        return payload

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        """How long to wait before asking again.

        The server's own `Retry-After` is authoritative when it sends one —
        guessing shorter just earns another rejection. Otherwise exponential
        backoff with jitter, because several investigations retrying in lockstep
        would rebuild the very spike they are backing off from.
        """
        header = response.headers.get("retry-after", "")
        if header.strip().isdigit():
            return min(float(header.strip()), settings.llm_retry_max_delay)
        base = min(settings.llm_retry_base_delay * (2 ** attempt),
                   settings.llm_retry_max_delay)
        return base * (0.5 + random.random() / 2)

    async def _post(self, payload: dict) -> httpx.Response:
        """POST with retries on transient faults.

        Without this a single 429 or 503 ends the investigation: the client
        raises LLMUnavailable, the ReAct loop treats it as fatal and returns, and
        six steps of gathered evidence are discarded because the provider was
        briefly busy. Measured against Gemini's free tier, one call in six came
        back 503 "experiencing high demand" — an investigation crossing eight
        steps would almost never finish.
        """
        last: httpx.Response | None = None
        for attempt in range(settings.llm_retry_attempts + 1):
            response = await self._client.post("/chat/completions", json=payload)
            if response.status_code not in _RETRYABLE:
                return response
            last = response
            if attempt == settings.llm_retry_attempts:
                break
            delay = self._retry_delay(response, attempt)
            logger.warning("%s returned %d; retrying in %.1fs (attempt %d of %d)",
                           self.base_url, response.status_code, delay,
                           attempt + 1, settings.llm_retry_attempts)
            await asyncio.sleep(delay)
        return last if last is not None else response

    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._post(self._payload(system, prompt, schema))
            if response.status_code == 400 and schema is not None and self._schema_supported:
                logger.warning("%s rejected a JSON schema; falling back to json_object mode",
                               self.base_url)
                self._schema_supported = False
                response = await self._post(self._payload(system, prompt, schema))
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LLMUnavailable(f"Cannot reach {self.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                f"{self.base_url} timed out after {settings.llm_timeout}s."
            ) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            raise LLMUnavailable(
                f"{self.base_url} returned {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Request to {self.base_url} failed: {exc}") from exc

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise LLMUnavailable(f"{self.base_url} returned no choices: {json.dumps(data)[:300]}")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}

        result = LLMResponse(
            text=message.get("content") or "",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            duration_ms=(time.perf_counter() - started) * 1000,
            model=data.get("model") or self.model,
        )
        # Unlike Ollama, a hosted API errors rather than silently dropping the
        # head of an over-long prompt — but it will happily stop mid-JSON when it
        # runs out of output budget, which parses as garbage further downstream.
        if choices[0].get("finish_reason") == "length":
            result.warnings.append(
                f"the reply was cut off at {settings.llm_max_output_tokens} output tokens; "
                f"raise LLM_MAX_OUTPUT_TOKENS if answers look truncated"
            )
        logger.info("LLM call (%s): %d prompt tokens, %d output tokens, %.0fms",
                    self.model, result.prompt_tokens, result.output_tokens, result.duration_ms)
        return result
