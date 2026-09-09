"""
Per-investigation accounting of what the model actually cost.

A run's answer is only as trustworthy as the prompt the model saw, and the two
numbers that decide that — how many round trips were made, and how close each
prompt came to the context window — were previously visible only in the server
log. Neither is derivable from the answer: a run that made one call and a run
that made eight look identical once the prose is written, and a prompt at 97% of
the window is one evidence line away from the silent truncation that
`PromptTruncated` exists to catch.

The meter is a mutable object held in a `ContextVar`, so it collects calls made
anywhere inside one investigation's task tree — including the `asyncio.gather`
in evidence collection, which copies the context and would drop a rebinding but
keeps the reference. Nothing outside an investigation has a meter bound, and
recording is then a no-op: the providers stay usable from tests and the eval
harness without any setup.
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field

_current: contextvars.ContextVar["LLMMeter | None"] = contextvars.ContextVar(
    "llm_meter", default=None
)


@dataclass
class LLMCall:
    """One round trip, named by the stage that made it."""

    stage: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    cached_prompt_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: float = 0.0
    truncated: bool = False
    failed: str | None = None


@dataclass
class LLMMeter:
    provider: str = "unknown"
    model: str = "unknown"
    endpoint: str = "unknown"
    # 0 when the provider does not publish one (a hosted API enforces its own
    # limit server-side). Reported as unknown rather than guessed at: a wrong
    # ceiling makes the utilisation percentage a lie.
    context_window: int = 0
    calls: list[LLMCall] = field(default_factory=list)

    # Which stage the next call belongs to. Set by the pipeline as it moves
    # through plan -> reasoning, so the breakdown says where the budget went.
    stage: str = "unknown"

    def record(self, call: LLMCall) -> None:
        self.calls.append(call)

    @property
    def request_count(self) -> int:
        return len(self.calls)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def cached_prompt_tokens(self) -> int:
        return sum(c.cached_prompt_tokens for c in self.calls)

    @property
    def cache_write_tokens(self) -> int:
        return sum(c.cache_write_tokens for c in self.calls)

    @property
    def peak_prompt_tokens(self) -> int:
        """The largest prompt actually sent, cached portion included.

        Deliberately not the largest *billed* prompt. Those became different
        numbers when cache reporting arrived: a 5,300-token prompt with 4,200
        served from cache bills 1,100, and reporting that as the peak made a run
        look like it had used 0.1% of its context window when it had used 0.5%.

        The distinction matters most where it is cheapest to get wrong. This
        figure is the truncation early-warning — Ollama silently drops the
        *head* of anything past `num_ctx`, so an answer written from the tail of
        its own transcript reads exactly like a confident one — and a peak that
        shrinks as caching improves would hide the run creeping up on that
        limit. Cost belongs in `prompt_tokens` and `cached_prompt_tokens`; size
        belongs here.
        """
        return max((c.prompt_tokens + c.cached_prompt_tokens for c in self.calls),
                   default=0)

    def snapshot(self) -> dict:
        """The shape the UI reads. Kept flat so it survives the NDJSON stream."""
        peak = self.peak_prompt_tokens
        by_stage: dict[str, int] = {}
        for call in self.calls:
            by_stage[call.stage] = by_stage.get(call.stage, 0) + 1
        return {
            "provider": self.provider,
            "model": self.model,
            "endpoint": self.endpoint,
            "context_window": self.context_window,
            "requests": self.request_count,
            "failed_requests": sum(1 for c in self.calls if c.failed),
            # Billed, not sent: the cached prefix is excluded here and counted
            # under `cached_prompt_tokens` below. `peak_prompt_tokens` is the
            # other way round — see the property for why the two differ.
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.prompt_tokens + self.output_tokens,
            # What prompt caching saved. `cached_prompt_tokens` is prompt text
            # the provider served from its cache instead of re-reading, so a
            # healthy eight-step run shows most of its prompt here rather than
            # in `prompt_tokens`. Zero across a whole run means caching is off
            # or the prefix is not stable, which no total-token figure reveals.
            "cached_prompt_tokens": self.cached_prompt_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_hit_ratio": (
                round(self.cached_prompt_tokens
                      / (self.prompt_tokens + self.cached_prompt_tokens), 4)
                if (self.prompt_tokens + self.cached_prompt_tokens) else None
            ),
            "peak_prompt_tokens": peak,
            # None rather than 0 when there is no published window: the UI shows
            # "unknown", which is honest, instead of "0% used", which is not.
            "peak_context_used": (round(peak / self.context_window, 4)
                                  if self.context_window else None),
            "requests_by_stage": by_stage,
            "duration_ms": round(sum(c.duration_ms for c in self.calls), 1),
        }


def bind(meter: LLMMeter) -> contextvars.Token:
    return _current.set(meter)


def unbind(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> LLMMeter | None:
    return _current.get()


def set_stage(stage: str) -> None:
    meter = _current.get()
    if meter is not None:
        meter.stage = stage


def record(*, model: str, prompt_tokens: int = 0, output_tokens: int = 0,
           cached_prompt_tokens: int = 0, cache_write_tokens: int = 0,
           duration_ms: float = 0.0, truncated: bool = False,
           failed: str | None = None) -> None:
    """Called by every provider at the end of a round trip, success or not.

    A failed call still consumed a round trip and still cost latency, so it is
    counted — a run that shows "8 requests, 3 failed" explains a slow, thin
    answer in a way "5 requests" never could.
    """
    meter = _current.get()
    if meter is None:
        return
    meter.record(LLMCall(
        stage=meter.stage,
        model=model or meter.model,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        cache_write_tokens=cache_write_tokens,
        duration_ms=duration_ms,
        truncated=truncated,
        failed=failed,
    ))


def instrument(generate):
    """Wraps an `LLMClient.generate` so the call is counted however it ends.

    Applied automatically to every subclass (see `LLMClient.__init_subclass__`)
    rather than called by each provider. The per-provider version this replaced
    was three near-identical blocks that a fourth backend would have had to
    remember to copy, and a backend that forgot would silently report "0 model
    requests" for a run that made eight.
    """
    import functools
    import time as _time

    @functools.wraps(generate)
    async def counted(self, *, system: str, prompt: str, schema: dict | None = None):
        started = _time.perf_counter()
        try:
            response = await generate(self, system=system, prompt=prompt, schema=schema)
        except BaseException as exc:
            # A failed call still consumed a round trip and still cost latency,
            # so it is counted: "8 requests, 3 failed" explains a slow, thin
            # answer in a way "5 requests" never could. A provider that knows
            # what it got before failing — Ollama, on a truncated prompt —
            # attaches it, so the token figures survive the exception.
            partial = getattr(exc, "llm_response", None)
            record(model=getattr(partial, "model", "") or getattr(self, "model", ""),
                   prompt_tokens=getattr(partial, "prompt_tokens", 0) or 0,
                   output_tokens=getattr(partial, "output_tokens", 0) or 0,
                   duration_ms=(_time.perf_counter() - started) * 1000,
                   truncated=bool(getattr(partial, "truncated", False)),
                   failed=type(exc).__name__)
            raise
        record(model=response.model or getattr(self, "model", ""),
               prompt_tokens=response.prompt_tokens,
               output_tokens=response.output_tokens,
               cached_prompt_tokens=response.cached_prompt_tokens,
               cache_write_tokens=response.cache_write_tokens,
               duration_ms=response.duration_ms or (_time.perf_counter() - started) * 1000,
               truncated=response.truncated)
        return response

    counted.__llm_instrumented__ = True
    return counted
