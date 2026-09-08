"""
A client for Gemini's own API, rather than its OpenAI-compatible shim.

The shim works and was what this project used, but it drops the one number that
matters for cost: its `usage` object carries no `prompt_tokens_details`, so a run
that re-read the same 2,000-token system prompt four times looked identical to
one that read it once. Measured against `gemini-3.5-flash-lite`, three calls
sharing a byte-identical 5,616-token prefix each reported `prompt_tokens: 5616`
and nothing else — there was no way to tell whether caching was happening, let
alone make it happen.

The native surface reports `usageMetadata.cachedContentTokenCount` and exposes
`cachedContents`, so both questions become answerable.

**What caching is actually available, measured rather than assumed.** Two
mechanisms exist, and the interesting one is not the one this was built for.

*Implicit* caching is free and automatic. A synthetic probe — one systemInstruction
repeated across two calls — suggested a high minimum, reporting nothing cached
until the prompt was very large:

    4,012 tokens -> 0 cached          31,211 tokens -> 16,364 cached
    7,811 tokens -> 0 cached          52,011 tokens -> 49,117 cached
   15,611 tokens -> 0 cached

That reading was misleading, and it is worth saying why rather than deleting it:
a real investigation does not look like that probe. It issues three or four calls
in quick succession whose prompts share a long, *growing* identical prefix — the
system prompt, the evidence header, and every earlier step of the transcript,
because the transcript is append-only. Measured on two live runs against
`gemini-3.5-flash-lite`:

    3 requests, 6,666 tokens sent, 4,164 cached  (62% hit)
    4 requests, 11,315 tokens sent, 6,246 cached (55% hit)

against 16,372 prompt tokens billed for a comparable four-call run through the
OpenAI-compatible shim, which cached nothing and could not have reported it if it
had. So the shape of the prompt matters more than its size, and the append-only
transcript is what earns the discount.

*Explicit* caching pins the constant 2,082-token system prompt outright, and is
what actually delivers here. Measured on a live run: one cache created per
process and reused, then every reasoning call reports exactly 2,082 tokens served
from it —

    planner      424 billed,     0 cached   (prompt too small to be worth caching)
    reasoning 1  1,085 billed, 2,082 cached
    reasoning 2  1,717 billed, 2,082 cached

3,226 billed against 7,390 sent, a 56% reduction, and about 80% below the 16,372
that the same question cost through the OpenAI-compatible shim.

Its availability is not stable, which the code has to survive. The same key
refused twice with

    429 TotalCachedContentStorageTokensPerModelFreeTier limit=0, requested=2082

and then began succeeding, so the refusal is a quota window rather than a
property of the account. A refusal therefore defers caching for
`gemini_cache_retry_seconds` instead of disabling it: giving up permanently
would cost every later run its caching over one bad minute, and retrying on
every call would pay a rejected round trip per request on a key that really has
no quota.

The two refusals are kept apart deliberately. "This key has no cache quota" is
permanent and switches caching off process-wide; "this particular prompt is too
small to cache" is not, and must not. The planner's system instruction is a
dozen tokens — far under Gemini's cache minimum — so an attempt to cache it is
rejected, and treating that as an account-level failure would disable caching
for the 2,000-token reasoning prompt that is the only one worth caching.
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Same statuses the OpenAI-compatible client retries, for the same reason: on a
# free tier one call in six comes back 429 or 503, and without retries a single
# one ends an investigation that had already done all of its expensive work.
_RETRYABLE = frozenset({429, 500, 502, 503, 504})

# Keys Gemini's schema dialect does not accept. It takes an OpenAPI 3 subset,
# not JSON Schema, and rejects the whole request rather than ignoring a stray
# keyword.
_SCHEMA_DROP = frozenset({
    "additionalProperties", "$schema", "$ref", "definitions", "$defs",
    "patternProperties", "allOf", "anyOf", "oneOf", "not", "default",
})


def to_gemini_schema(schema: dict | None) -> dict | None:
    """Translates a JSON Schema into Gemini's dialect, or declines to.

    Two differences bite:

    * a nullable field is `{"type": ["string", "null"]}` in JSON Schema and
      `{"type": "string", "nullable": true}` here;
    * an object must declare its properties. A free-form one — `action_input`,
      which carries whatever arguments the chosen tool takes — cannot be
      expressed, and translating it to a propertyless object would tell the
      model the opposite of what is true: that no arguments are allowed.

    Returning None for that case is deliberate. The caller then asks only for
    JSON rather than for JSON matching a schema, which is what the Ollama client
    already falls back to and what the prompt itself has always specified in
    prose. A wrong schema is worse than no schema: it constrains the model away
    from correct answers, silently.
    """
    if not isinstance(schema, dict):
        return None

    def convert(node: dict) -> dict | None:
        if not isinstance(node, dict):
            return None
        out: dict = {}
        raw_type = node.get("type")

        if isinstance(raw_type, list):
            concrete = [t for t in raw_type if t != "null"]
            if len(concrete) != 1:
                return None            # a genuine union; not expressible here
            out["type"] = concrete[0].upper()
            if "null" in raw_type:
                out["nullable"] = True
        elif isinstance(raw_type, str):
            out["type"] = raw_type.upper()
        else:
            return None                # Gemini requires an explicit type

        for key in ("description", "enum", "format"):
            if key in node:
                out[key] = node[key]

        if out["type"] == "OBJECT":
            properties = node.get("properties")
            if not isinstance(properties, dict) or not properties:
                # Free-form: see the docstring. Decline rather than misdescribe.
                return None
            converted: dict = {}
            for name, child in properties.items():
                child_schema = convert(child)
                if child_schema is None:
                    return None
                converted[name] = child_schema
            out["properties"] = converted
            required = [r for r in (node.get("required") or []) if r in converted]
            if required:
                out["required"] = required

        if out["type"] == "ARRAY":
            items = convert(node.get("items") or {})
            if items is None:
                return None
            out["items"] = items

        for key in _SCHEMA_DROP:
            out.pop(key, None)
        return out

    return convert(schema)


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or settings.llm_model or "gemini-2.5-flash"
        self._api_key = api_key or settings.llm_api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.llm_timeout,
            headers={"Content-Type": "application/json",
                     **({"x-goog-api-key": self._api_key} if self._api_key else {})},
        )
        # The cached system prompt, if one could be created. Keyed by the prompt
        # itself so a change to the tool schema invalidates it rather than
        # silently serving the old instructions.
        self._cache_name: str | None = None
        self._cached_system: str | None = None
        self._cache_expires_at: float = 0.0
        # Why the API last refused to create a cache, and when to ask again.
        #
        # Not permanent, which is the correction that matters here. This key
        # returned `TotalCachedContentStorageTokensPerModelFreeTier limit=0`
        # twice and then started succeeding, so the refusal is a quota window,
        # not a property of the account. Treating it as permanent — which this
        # did — would cost every subsequent run its caching because of one bad
        # minute, and the only cure would be restarting the pod. Retrying on
        # every call is the opposite mistake: a rejected round trip per request,
        # forever, on a key that genuinely has no quota. So it backs off.
        self._cache_unavailable: str | None = None
        self._cache_retry_at: float = 0.0
        # System prompts this key could not cache for a reason specific to the
        # prompt rather than to the account. Kept apart from the flag above
        # because conflating the two is a bug that hides the whole feature: the
        # planner's system instruction is a dozen tokens, far below Gemini's
        # cache minimum, so trying to cache it fails — and treating that as
        # "caching is unavailable" would switch it off for the 2,000-token
        # reasoning prompt that is the only one worth caching in the first place.
        self._uncacheable: set[str] = set()
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            response = await self._client.get(f"/models/{self.model}")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def context_window(self) -> int:
        """The model's own published input limit, asked rather than guessed.

        A hard-coded table goes stale every time Google ships a model, and a
        wrong ceiling makes the utilisation figure beside an investigation a lie.
        """
        try:
            response = await self._client.get(f"/models/{self.model}")
            if response.status_code == 200:
                return int(response.json().get("inputTokenLimit") or 0)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return 0

    # ----------------------------------------------------------- caching
    def _defer_caching(self, reason: str) -> None:
        """Stops asking for a while, rather than for good.

        See `_cache_unavailable` for why the distinction is load-bearing: the
        refusal this guards against has been observed to clear on its own.
        """
        self._cache_unavailable = reason
        self._cache_retry_at = time.time() + max(
            int(settings.gemini_cache_retry_seconds), 60)

    async def _cache_for(self, system: str) -> str | None:
        """The name of a cached copy of the system prompt, if one can be had.

        The system prompt is the same 2,082 tokens on every call of every
        investigation — it is built once from the tool schema and never varies —
        so it is exactly what explicit caching is for. Created lazily on the
        first call and reused until its TTL runs out.

        Every failure path here ends in caching being switched off for the
        process rather than retried. The common one is a free-tier key, which
        answers `limit=0` and would otherwise be asked again on every request
        for the life of the pod.
        """
        if not settings.llm_prompt_caching:
            return None
        if self._cache_unavailable:
            if time.time() < self._cache_retry_at:
                return None
            # The back-off has elapsed; clear the flag and try once more.
            self._cache_unavailable = None
        if system in self._uncacheable:
            return None
        # Estimated rather than counted: a `countTokens` round trip to decide
        # whether to make another round trip costs more than it saves, and the
        # only decision resting on it is "is this obviously too small".
        if len(system) / 3.6 < settings.gemini_cache_min_tokens:
            self._uncacheable.add(system)
            return None

        async with self._lock:
            fresh = (self._cache_name
                     and self._cached_system == system
                     and time.time() < self._cache_expires_at)
            if fresh:
                return self._cache_name
            if self._cache_unavailable and time.time() < self._cache_retry_at:
                return None

            ttl = max(int(settings.gemini_cache_ttl_seconds), 60)
            try:
                response = await self._client.post("/cachedContents", json={
                    "model": f"models/{self.model}",
                    "systemInstruction": {"parts": [{"text": system}]},
                    "ttl": f"{ttl}s",
                })
            except httpx.HTTPError as exc:
                self._defer_caching(str(exc))
                logger.info("Gemini prompt caching unavailable (%s); "
                            "every call will send the system prompt in full", exc)
                return None

            if response.status_code != 200:
                detail = response.text[:300]
                # Which of the two kinds of refusal this is decides whether
                # caching is off for the process or just for this prompt.
                account_level = (response.status_code in (401, 403, 429)
                                 or "FreeTier" in detail or "limit=0" in detail
                                 or "PERMISSION_DENIED" in detail)
                if account_level:
                    self._defer_caching(detail)
                    # Worth an explicit sentence: this is the difference between
                    # a run billing 7,400 prompt tokens and 3,200, and the cause
                    # is quota rather than anything in this codebase.
                    logger.info(
                        "Gemini refused to create a prompt cache (%s). The system "
                        "prompt (~2k tokens) will be sent in full with every call "
                        "until this is retried in %ds. Investigations are "
                        "unaffected otherwise.",
                        response.status_code, settings.gemini_cache_retry_seconds,
                    )
                else:
                    self._uncacheable.add(system)
                    logger.info("Gemini declined to cache this prompt (%s): %s. "
                                "Other prompts will still be tried.",
                                response.status_code, detail)
                return None

            body = response.json()
            self._cache_name = body.get("name")
            self._cached_system = system
            # Expire our copy early. A cache that has lapsed server-side comes
            # back as a 403 on the *next* generate call, which would cost a
            # whole retry cycle to discover.
            self._cache_expires_at = time.time() + ttl * 0.8
            cached_tokens = (body.get("usageMetadata") or {}).get("totalTokenCount")
            logger.info("Gemini prompt cache created (%s, %s tokens, ttl %ss)",
                        self._cache_name, cached_tokens, ttl)
            return self._cache_name

    # ----------------------------------------------------------- requests
    def _payload(self, system: str, prompt: str, schema: dict | None,
                 cache_name: str | None) -> dict:
        generation: dict = {
            "temperature": settings.llm_temperature,
            "maxOutputTokens": settings.llm_max_output_tokens,
        }
        if schema is not None:
            # Always ask for JSON; ask for a *shaped* JSON only when the shape
            # survives translation. See `to_gemini_schema`.
            generation["responseMimeType"] = "application/json"
            translated = to_gemini_schema(schema)
            if translated is not None:
                generation["responseSchema"] = translated

        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }
        if cache_name:
            # The instructions live in the cache now. Sending both is rejected,
            # and sending the prompt again would defeat the point.
            payload["cachedContent"] = cache_name
        else:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after", "")
        if header.strip().isdigit():
            return min(float(header.strip()), settings.llm_retry_max_delay)
        base = min(settings.llm_retry_base_delay * (2 ** attempt),
                   settings.llm_retry_max_delay)
        return base * (0.5 + random.random() / 2)

    async def _post(self, payload: dict) -> httpx.Response:
        path = f"/models/{self.model}:generateContent"
        last: httpx.Response | None = None
        for attempt in range(settings.llm_retry_attempts):
            try:
                response = await self._client.post(path, json=payload)
            except httpx.ConnectError as exc:
                raise LLMUnavailable(f"Cannot reach {self.base_url}: {exc}") from exc
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(
                    f"{self.base_url} timed out after {settings.llm_timeout}s."
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMUnavailable(f"Request to {self.base_url} failed: {exc}") from exc

            if response.status_code not in _RETRYABLE:
                return response

            last = response
            if attempt == settings.llm_retry_attempts - 1:
                break
            delay = self._retry_delay(response, attempt)
            logger.warning("Gemini returned %s; retrying in %.1fs (attempt %d/%d)",
                           response.status_code, delay, attempt + 1,
                           settings.llm_retry_attempts)
            await asyncio.sleep(delay)

        assert last is not None
        raise LLMUnavailable(
            f"{self.base_url} returned {last.status_code} after "
            f"{settings.llm_retry_attempts} attempts: {last.text[:300]}"
        )

    @staticmethod
    def _text_from(candidate: dict) -> str:
        parts = ((candidate.get("content") or {}).get("parts") or [])
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        started = time.perf_counter()
        cache_name = await self._cache_for(system)

        response = await self._post(self._payload(system, prompt, schema, cache_name))

        if response.status_code == 403 and cache_name:
            # The cache expired or was deleted under us. Drop it and send the
            # system prompt inline rather than failing a call that can succeed.
            logger.info("Gemini cache %s is no longer usable; resending the system "
                        "prompt inline", cache_name)
            async with self._lock:
                self._cache_name, self._cache_expires_at = None, 0.0
            response = await self._post(self._payload(system, prompt, schema, None))

        if response.status_code != 200:
            raise LLMUnavailable(
                f"{self.base_url} returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            # A safety block returns no candidate at all. Say which, rather than
            # letting an empty string parse as unusable JSON three layers up.
            feedback = data.get("promptFeedback") or {}
            raise LLMUnavailable(
                f"Gemini returned no candidates"
                + (f" (blocked: {feedback.get('blockReason')})" if feedback.get("blockReason")
                   else f": {json.dumps(data)[:300]}"))

        usage = data.get("usageMetadata") or {}
        cached = int(usage.get("cachedContentTokenCount") or 0)
        # `promptTokenCount` is the whole prompt including anything served from
        # cache, so the cached part is subtracted out — the same normalisation
        # the OpenAI-compatible client does, so a run reports the same way
        # whichever provider answered it.
        prompt_tokens = max(int(usage.get("promptTokenCount") or 0) - cached, 0)
        # Thinking models bill their reasoning as output even though it never
        # appears in the reply. Counting it keeps the output figure honest.
        output_tokens = (int(usage.get("candidatesTokenCount") or 0)
                         + int(usage.get("thoughtsTokenCount") or 0))

        result = LLMResponse(
            text=self._text_from(candidates[0]),
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            cached_prompt_tokens=cached,
            duration_ms=(time.perf_counter() - started) * 1000,
            model=data.get("modelVersion") or self.model,
        )

        if candidates[0].get("finishReason") == "MAX_TOKENS":
            result.warnings.append(
                f"the reply was cut off at {settings.llm_max_output_tokens} output tokens; "
                f"raise LLM_MAX_OUTPUT_TOKENS if answers look truncated"
            )

        logger.info("LLM call (%s): %d prompt tokens (%d served from cache), "
                    "%d output tokens, %.0fms",
                    self.model, result.prompt_tokens, result.cached_prompt_tokens,
                    result.output_tokens, result.duration_ms)
        return result
