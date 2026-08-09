from __future__ import annotations

import logging
import time

import httpx

from app.config import settings
from app.llm.base import LLMClient, LLMResponse, LLMUnavailable, PromptTruncated

logger = logging.getLogger(__name__)

# Ollama reports how many prompt tokens it actually evaluated. If that lands at
# the context limit, the rest was dropped. A small margin absorbs the template
# tokens the server adds around the prompt.
_TRUNCATION_MARGIN = 48


class OllamaClient(LLMClient):
    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.num_ctx = settings.ollama_num_ctx
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=settings.ollama_timeout)
        self._schema_supported = True

    async def close(self) -> None:
        await self._client.aclose()

    async def available(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            if response.status_code != 200:
                return False
            names = {m.get("name", "").split(":")[0] for m in response.json().get("models", [])}
            return self.model.split(":")[0] in names
        except httpx.HTTPError:
            return False

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/api/tags")
            return [m.get("name", "") for m in response.json().get("models", [])]
        except httpx.HTTPError:
            return []

    def _payload(self, system: str, prompt: str, schema: dict | None) -> dict:
        payload: dict = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {
                # The single most consequential setting in this file. Ollama
                # defaults num_ctx to 2048 and truncates anything longer without
                # any error, keeping only the tail.
                "num_ctx": self.num_ctx,
                "temperature": settings.ollama_temperature,
                "seed": settings.ollama_seed,
                "top_p": 0.9,
                "repeat_penalty": 1.05,
            },
        }
        if schema is not None:
            # Ollama accepts a full JSON schema here on recent versions, and the
            # literal string "json" on older ones.
            payload["format"] = schema if self._schema_supported else "json"
        return payload

    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post("/api/generate",
                                               json=self._payload(system, prompt, schema))
            if response.status_code == 400 and schema is not None and self._schema_supported:
                # Older Ollama rejects a schema object; fall back once and stay
                # in that mode for the rest of the process.
                logger.warning("Ollama rejected a JSON schema; falling back to format=json")
                self._schema_supported = False
                response = await self._client.post("/api/generate",
                                                   json=self._payload(system, prompt, schema))
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.base_url}. From WSL this is usually the "
                f"Windows host address, and Ollama must be started with OLLAMA_HOST=0.0.0.0."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMUnavailable(
                f"Ollama timed out after {settings.ollama_timeout}s. A long prompt on a "
                f"cold model can exceed this; raise OLLAMA_TIMEOUT or shrink the evidence budget."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"Ollama request failed: {exc}") from exc

        data = response.json()
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        result = LLMResponse(
            text=data.get("response", ""),
            prompt_tokens=prompt_tokens,
            output_tokens=int(data.get("eval_count") or 0),
            duration_ms=(time.perf_counter() - started) * 1000,
            model=self.model,
        )

        if prompt_tokens and prompt_tokens >= self.num_ctx - _TRUNCATION_MARGIN:
            result.truncated = True
            raise PromptTruncated(
                f"Ollama evaluated {prompt_tokens} prompt tokens against a context window of "
                f"{self.num_ctx} — the prompt was truncated and the model did not see all of "
                f"the evidence. Raise OLLAMA_NUM_CTX, or lower the max_prompt_* evidence budgets."
            )

        logger.info("LLM call: %d prompt tokens, %d output tokens, %.0fms",
                    result.prompt_tokens, result.output_tokens, result.duration_ms)
        return result
