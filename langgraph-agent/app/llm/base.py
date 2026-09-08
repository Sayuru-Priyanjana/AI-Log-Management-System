from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    truncated: bool = False
    model: str = ""
    warnings: list[str] = field(default_factory=list)

    # Prompt caching, reported separately from `prompt_tokens` because the two
    # are billed differently and because the difference is the whole point of
    # measuring it. A ReAct run makes eight calls whose prompts share a long
    # identical prefix — the system prompt, the tool schema, the evidence
    # header, and every earlier step of the transcript — so from the second call
    # on, most of the prompt should be a cache *read*. If these stay at zero
    # across a run, caching is not working and the run is paying full price
    # eight times over; that is invisible in a total token count, which looks
    # the same either way.
    cached_prompt_tokens: int = 0       # prefix served from cache
    cache_write_tokens: int = 0         # prefix written to cache this call

    @property
    def cache_hit_ratio(self) -> float | None:
        """Fraction of the prompt that did not have to be re-read."""
        total = self.prompt_tokens + self.cached_prompt_tokens
        if not total:
            return None
        return self.cached_prompt_tokens / total


class LLMUnavailable(RuntimeError):
    """The model could not be reached or did not answer."""


class PromptTruncated(RuntimeError):
    """The context window silently dropped part of the prompt.

    This is fatal rather than a warning: a truncated prompt loses its head, so
    the model answers from the tail — the schema reminder and a few stray
    evidence lines — and produces something that reads like analysis while
    having seen almost none of the evidence.
    """


class LLMClient(ABC):
    def __init_subclass__(cls, **kwargs) -> None:
        """Counts every model round trip, whoever implements it.

        Instrumenting here rather than inside each provider is what makes the
        "LLM requests" figure beside an investigation trustworthy: it cannot be
        wrong for a backend that forgot to add a line, because there is no line
        to add. The import is deferred to call time so that `app.llm.telemetry`
        stays importable on its own, without pulling this module in first.
        """
        super().__init_subclass__(**kwargs)
        generate = cls.__dict__.get("generate")
        if generate is None or getattr(generate, "__llm_instrumented__", False):
            return
        from app.llm.telemetry import instrument
        cls.generate = instrument(generate)

    @abstractmethod
    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        ...

    @abstractmethod
    async def available(self) -> bool:
        ...
