"""
Evaluation harness.

Injects each incident scenario into the testbed, waits for it to develop, runs a
real investigation, and scores two things separately:

  signal recall   did the deterministic engine detect the right signals?
  cause accuracy  did the final answer name the right root cause?

Keeping them apart matters. Signal recall is the leading indicator: if the engine
never detected the OOM kill, no amount of prompt work will make the answer name
it. Only once recall is high does cause accuracy say anything about the model.

Ground truth lives in the incident controller alongside the injector, so the two
cannot drift apart.

    python -m eval.run_eval                        # every scenario
    python -m eval.run_eval --scenario crashloop   # one
    python -m eval.run_eval --no-inject --scenario crashloop
                                                   # score whatever is running now
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.agents.analyst import AnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.config import settings
from app.llm.ollama import OllamaClient
from app.models.analysis import InvestigationResult
from app.models.plan import InvestigationRequest
from app.pipeline.run import InvestigationPipeline
from app.registry.systems import SystemRegistry
from app.sources.opensearch import OpenSearchClient
from app.sources.prometheus import PrometheusClient
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool

QUESTION = "Something is wrong with checkout. What is the root cause?"


class IncidentController:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0)
        self.base_url = base_url

    async def close(self) -> None:
        await self._client.aclose()

    async def catalogue(self) -> dict:
        response = await self._client.get("/incidents")
        response.raise_for_status()
        return response.json()["scenarios"]

    async def start(self, scenario: str) -> dict:
        response = await self._client.post(f"/incidents/{scenario}/start")
        response.raise_for_status()
        return response.json()

    async def stop(self, scenario: str) -> dict:
        response = await self._client.post(f"/incidents/{scenario}/stop")
        response.raise_for_status()
        return response.json()

    async def reset_all(self) -> dict:
        response = await self._client.post("/incidents/reset-all")
        response.raise_for_status()
        return response.json()


@dataclass
class ScenarioScore:
    scenario: str
    expected_cause: str
    expected_signals: list[str]
    detected_signals: list[str] = field(default_factory=list)
    actual_cause: str = ""
    actual_service: str | None = None
    expected_service: str | None = None
    confidence: float = 0.0
    analyst: str = ""
    agreed_with_engine: bool = True
    duration_s: float = 0.0
    error: str | None = None

    @property
    def recall(self) -> float:
        if not self.expected_signals:
            return 1.0
        found = sum(1 for s in self.expected_signals if s in self.detected_signals)
        return found / len(self.expected_signals)

    @property
    def missing_signals(self) -> list[str]:
        return [s for s in self.expected_signals if s not in self.detected_signals]

    @property
    def cause_correct(self) -> bool:
        return self.actual_cause == self.expected_cause

    @property
    def service_correct(self) -> bool:
        if not self.expected_service:
            return True
        return self.actual_service == self.expected_service


async def build_pipeline() -> tuple[InvestigationPipeline, list]:
    opensearch = OpenSearchClient()
    prometheus = PrometheusClient()
    llm = OllamaClient()
    registry = SystemRegistry(opensearch)
    log_tool = LogTool(opensearch)

    await opensearch.ensure_templates()
    pipeline = InvestigationPipeline(
        log_tool=log_tool,
        event_tool=EventTool(opensearch),
        metric_tool=MetricTool(prometheus),
        orchestrator=OrchestratorAgent(llm),
        analyst=AnalystAgent(llm),
        registry=registry,
    )
    return pipeline, [opensearch, prometheus, llm]


async def countdown(seconds: int, label: str) -> None:
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        print(f"\r    {label}: {remaining:5.0f}s remaining ", end="", flush=True)
        await asyncio.sleep(min(5, remaining))
    print(f"\r    {label}: done{' ' * 20}")


async def score_one(pipeline: InvestigationPipeline, scenario_id: str, spec: dict,
                    system_id: str, environment: str, duration: str) -> ScenarioScore:
    score = ScenarioScore(
        scenario=scenario_id,
        expected_cause=spec["expected_cause"],
        expected_signals=list(spec["expected_signals"]),
        expected_service=spec.get("expected_service"),
    )
    started = time.perf_counter()
    try:
        result: InvestigationResult = await pipeline.run_collect(InvestigationRequest(
            system_id=system_id, environment=environment,
            question=QUESTION, duration=duration,
        ))
    except Exception as exc:
        score.error = f"{type(exc).__name__}: {exc}"
        score.duration_s = time.perf_counter() - started
        return score

    score.duration_s = time.perf_counter() - started
    score.detected_signals = sorted({signal.type.value for signal in result.signals})
    score.actual_cause = result.analysis.category.value
    score.confidence = result.analysis.confidence
    score.analyst = result.analysis.analyst
    score.agreed_with_engine = result.analysis.agrees_with_engine
    chosen = next((c for c in result.candidates
                   if c.id == result.analysis.chosen_candidate_id), None)
    score.actual_service = chosen.service if chosen else None
    return score


def render(scores: list[ScenarioScore]) -> str:
    lines: list[str] = []
    width = max((len(s.scenario) for s in scores), default=10) + 2

    lines.append("")
    lines.append("=" * 100)
    lines.append("EVALUATION RESULTS")
    lines.append("=" * 100)
    lines.append(f"{'scenario'.ljust(width)}{'recall':>8}{'cause':>8}{'service':>9}"
                 f"{'conf':>7}{'analyst':>15}  expected -> actual")
    lines.append("-" * 100)

    for score in sorted(scores, key=lambda s: s.scenario):
        if score.error:
            lines.append(f"{score.scenario.ljust(width)}{'ERROR':>8}   {score.error[:60]}")
            continue
        lines.append(
            f"{score.scenario.ljust(width)}"
            f"{score.recall:>7.0%}"
            f"{('PASS' if score.cause_correct else 'FAIL'):>8}"
            f"{('ok' if score.service_correct else 'wrong'):>9}"
            f"{score.confidence:>7.2f}"
            f"{score.analyst:>15}"
            f"  {score.expected_cause} -> {score.actual_cause}"
        )
        if score.missing_signals:
            lines.append(f"{' ' * width}missing signals: {', '.join(score.missing_signals)}")
        if not score.agreed_with_engine:
            lines.append(f"{' ' * width}note: the model disagreed with the rule ranking")

    scored = [s for s in scores if not s.error]
    lines.append("-" * 100)
    if scored:
        mean_recall = sum(s.recall for s in scored) / len(scored)
        cause_accuracy = sum(1 for s in scored if s.cause_correct) / len(scored)
        service_accuracy = sum(1 for s in scored if s.service_correct) / len(scored)
        deterministic = sum(1 for s in scored if s.analyst == "deterministic")
        lines.append(f"signal recall   {mean_recall:.0%}   "
                     f"(the leading indicator — fix this before touching prompts)")
        lines.append(f"cause accuracy  {cause_accuracy:.0%}   "
                     f"({sum(1 for s in scored if s.cause_correct)}/{len(scored)} scenarios)")
        lines.append(f"service accuracy {service_accuracy:.0%}  "
                     f"(did it name the right component, not just the right shape)")
        if deterministic:
            lines.append(f"note: {deterministic} run(s) fell back to the deterministic ranking "
                         f"because the model did not answer usably")
    lines.append(f"errors          {len(scores) - len(scored)}")
    lines.append("=" * 100)
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Score LogIntel against injected incidents.")
    parser.add_argument("--scenario", help="run a single scenario by id")
    parser.add_argument("--system-id", default="shopdemo")
    parser.add_argument("--environment", default="staging")
    parser.add_argument("--duration", default="1h",
                        help="investigation window handed to the pipeline")
    parser.add_argument("--quiet-seconds", type=int, default=90,
                        help="settling time after reset, so the baseline window is clean")
    parser.add_argument("--no-inject", action="store_true",
                        help="do not touch the cluster; score whatever is running right now")
    parser.add_argument("--report", default="eval-report.json")
    args = parser.parse_args()

    controller = IncidentController(settings.incident_controller_url)
    try:
        catalogue = await controller.catalogue()
    except Exception as exc:
        print(f"Cannot reach the incident controller at {controller.base_url}: {exc}")
        print("Is the testbed up? (cd testbed && vagrant status)")
        return 2

    selected = [args.scenario] if args.scenario else sorted(catalogue)
    unknown = [s for s in selected if s not in catalogue]
    if unknown:
        print(f"Unknown scenario(s): {', '.join(unknown)}")
        print(f"Available: {', '.join(sorted(catalogue))}")
        return 2

    pipeline, closeables = await build_pipeline()
    scores: list[ScenarioScore] = []

    if not args.no_inject:
        total = sum(args.quiet_seconds + catalogue[s]["settle_seconds"] + 60 for s in selected)
        print(f"Running {len(selected)} scenario(s). Rough estimate: {total / 60:.0f} minutes.\n")

    try:
        for index, scenario_id in enumerate(selected, start=1):
            spec = catalogue[scenario_id]
            print(f"[{index}/{len(selected)}] {scenario_id}: {spec['title']}")

            if not args.no_inject:
                await controller.reset_all()
                await countdown(args.quiet_seconds, "settling to a clean baseline")
                await controller.start(scenario_id)
                await countdown(spec["settle_seconds"], "letting the incident develop")

            print("    investigating ...")
            score = await score_one(pipeline, scenario_id, spec,
                                    args.system_id, args.environment, args.duration)
            scores.append(score)

            verdict = "ERROR" if score.error else (
                f"recall {score.recall:.0%}, cause "
                f"{'correct' if score.cause_correct else 'WRONG (' + score.actual_cause + ')'}"
            )
            print(f"    {verdict}  [{score.duration_s:.0f}s]\n")

            if not args.no_inject:
                await controller.stop(scenario_id)
    finally:
        if not args.no_inject:
            try:
                await controller.reset_all()
            except Exception:
                pass
        await controller.close()
        for closeable in closeables:
            await closeable.close()

    report = render(scores)
    print(report)

    Path(args.report).write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.ollama_model,
        "num_ctx": settings.ollama_num_ctx,
        "scores": [score.__dict__ | {"recall": score.recall,
                                     "cause_correct": score.cause_correct,
                                     "missing_signals": score.missing_signals}
                   for score in scores],
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {args.report}")

    failures = sum(1 for s in scores if s.error or not s.cause_correct)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
