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
from app.llm.ollama import OllamaClient
from app.llm.openai_compatible import OpenAICompatibleClient

logger = logging.getLogger(__name__)

PROVIDERS = ("ollama", "openai", "anthropic")


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
