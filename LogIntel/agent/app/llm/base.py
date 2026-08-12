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
    @abstractmethod
    async def generate(self, *, system: str, prompt: str,
                       schema: dict | None = None) -> LLMResponse:
        ...

    @abstractmethod
    async def available(self) -> bool:
        ...
