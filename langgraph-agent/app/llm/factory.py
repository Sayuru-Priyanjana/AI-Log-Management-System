"""
Chooses the model backend from configuration.

The model is an external service in every case — a local Ollama on the host, or a
hosted API over the network — so it is selected by environment variable and never
by import. `LLM_PROVIDER=ollama` is the default because the design target is a
local model: nothing leaves the machine, and there is no per-token cost to an
investigation that makes eight tool calls.

Switching provider changes what the pipeline can rely on. A larger hosted model
follows the ReAct protocol more reliably and writes better prose, but none of the
deterministic layer changes: signals, windows and the verifier behave identically,
and the answer is checked against the same evidence either way.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.llm.anthropic import AnthropicClient
from app.llm.base import LLMClient
from app.llm.gemini import GeminiClient
from app.llm.ollama import OllamaClient
from app.llm.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)

PROVIDERS = ("ollama", "openai", "anthropic", "groq", "gemini")


def build_llm() -> LLMClient:
    provider = (settings.llm_provider or "ollama").strip().lower()

    if provider not in PROVIDERS:
        # Falling back is better than refusing to start: an unreachable model
        # degrades to the rule engine, whereas a process that will not boot takes
        # the whole investigation surface with it.
        logger.warning("Unknown LLM_PROVIDER %r; falling back to ollama. Known: %s",
                       provider, ", ".join(PROVIDERS))
        provider = "ollama"

    if provider == "anthropic":
        client: LLMClient = AnthropicClient()
    elif provider == "openai":
        client = OpenAICompatibleClient()
    elif provider == "groq":
        client = OpenAICompatibleClient(base_url=settings.llm_base_url or "https://api.groq.com/openai/v1")
        client.provider = "groq"
    elif provider == "gemini":
        # Gemini's own API rather than its OpenAI-compatible shim. The shim
        # works, but its `usage` object carries no cached-token field at all, so
        # there was no way to see whether a run re-read the same 2,000-token
        # system prompt on all four of its calls — or to do anything about it.
        # The native surface reports `cachedContentTokenCount` and exposes
        # `cachedContents`; see app/llm/gemini.py for what each buys, measured.
        client = GeminiClient()
    else:
        client = OllamaClient()

    logger.info("LLM provider: %s | model=%s | endpoint=%s",
                provider, describe_model(client), describe_endpoint(client))
    return client


def describe_model(client: LLMClient) -> str:
    return getattr(client, "model", "unknown")


def describe_endpoint(client: LLMClient) -> str:
    return getattr(client, "base_url", "unknown")


def describe_provider(client: LLMClient) -> str:
    return getattr(client, "provider", "ollama")


# Published context windows for the hosted models this project is actually run
# against. Only used for the utilisation figure shown beside an investigation —
# an unknown model reports 0, which the UI renders as "unknown" rather than
# inventing a ceiling and a percentage that would be wrong.
_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4o": 128_000, "gpt-4o-mini": 128_000, "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576, "gpt-4-turbo": 128_000, "gpt-3.5-turbo": 16_385,
    "o3-mini": 200_000, "o4-mini": 200_000,
    "claude-opus-4": 200_000, "claude-sonnet-4": 200_000,
    "claude-3-5-sonnet": 200_000, "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000, "claude-3-haiku": 200_000,
    "llama-3.3-70b-versatile": 128_000, "llama-3.1-8b-instant": 128_000,
    "mixtral-8x7b-32768": 32_768,
    "gemini-3.5-pro": 1_048_576, "gemini-3.5-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-2.5-pro": 1_048_576, "gemini-2.5-flash": 1_048_576,
    "gemini-2.5-flash-lite": 1_048_576,
    "gemini-2.0-flash": 1_048_576, "gemini-1.5-pro": 2_097_152,
    "gemini-1.5-flash": 1_048_576,
}


def describe_context_window(client: LLMClient) -> int:
    """How many tokens the prompt may occupy, or 0 when that is not knowable.

    Ollama is the case that matters: the window is a setting this process
    chooses (`OLLAMA_NUM_CTX`), the server silently truncates past it, and the
    resulting answer is written from the tail of its own transcript. Reporting
    the number beside the peak prompt size is what makes that visible before it
    becomes a wrong answer rather than after.
    """
    explicit = getattr(client, "num_ctx", None)
    if isinstance(explicit, int) and explicit > 0:
        return explicit

    # An operator who knows their model's window can state it. Checked before
    # the table so a newer model than this file knows about is still reported
    # correctly, without anyone having to guess on its behalf.
    if settings.llm_context_window > 0:
        return settings.llm_context_window

    model = (describe_model(client) or "").lower()
    if model in _CONTEXT_WINDOWS:
        return _CONTEXT_WINDOWS[model]
    # Hosted names carry dated or versioned suffixes ("claude-3-5-sonnet-latest",
    # "gpt-4o-2024-11-20"); match the longest known prefix rather than failing.
    matches = [w for name, w in _CONTEXT_WINDOWS.items() if model.startswith(name)]
    return max(matches) if matches else 0
