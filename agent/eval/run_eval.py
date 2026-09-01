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

from app.agents.react import ReActAgent
from app.agents.orchestrator import OrchestratorAgent
from app.config import settings
from app.llm.factory import build_llm, describe_model, describe_provider
from app.models.analysis import InvestigationResult
from app.models.plan import InvestigationRequest
from app.pipeline.run import InvestigationPipeline
from app.registry.systems import SystemRegistry
from app.sources.opensearch import OpenSearchClient
from app.sources.prometheus import PrometheusClient, PrometheusError
from app.store.investigations import InvestigationStore
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import MetricTool

QUESTION = "Something is wrong with checkout. What is the root cause?"

# Some signals are strictly stronger statements than others, and the engine emits
# only the stronger one rather than both. A container in CrashLoopBackOff has by
# definition restarted, so reporting POD_RESTART alongside CRASHLOOP would be
# redundant noise in the evidence handed to the model — but a scenario that lists
# POD_RESTART as expected would then be marked as a miss for a detection that
# actually happened, and more precisely than asked for.
SUBSUMES: dict[str, set[str]] = {
    "CRASHLOOP": {"POD_RESTART"},
    "OOM_KILL": {"POD_RESTART"},
    "DEPENDENCY_UNAVAILABLE": {"DEPENDENCY_DEGRADED"},
}


class IncidentController:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0)
        self.base_url = base_url
        self.namespace = "shopdemo"

    async def close(self) -> None:
        await self._client.aclose()

    async def catalogue(self) -> dict:
        response = await self._client.get("/incidents")
        response.raise_for_status()
        payload = response.json()
        # Remembered so the health check can be scoped to the same namespace the
        # scenarios act on, rather than assuming a name.
        self.namespace = payload.get("namespace", "shopdemo")
        return payload["scenarios"]

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
    investigation_id: str = ""

    def _was_detected(self, expected: str) -> bool:
        if expected in self.detected_signals:
            return True
        return any(expected in SUBSUMES.get(detected, set())
                   for detected in self.detected_signals)

    @property
    def recall(self) -> float:
        if not self.expected_signals:
            return 1.0
        found = sum(1 for s in self.expected_signals if self._was_detected(s))
        return found / len(self.expected_signals)

    @property
    def missing_signals(self) -> list[str]:
        return [s for s in self.expected_signals if not self._was_detected(s)]

    @property
    def cause_correct(self) -> bool:
        return self.actual_cause == self.expected_cause

    @property
    def service_correct(self) -> bool:
        if not self.expected_service:
            return True
        return self.actual_service == self.expected_service


async def build_pipeline() -> tuple[InvestigationPipeline, list, OpenSearchClient,
                                    InvestigationStore, PrometheusClient]:
    opensearch = OpenSearchClient()
    # PrometheusClient takes its base URL explicitly — it has no settings
    # default, and calling it bare raised TypeError before the harness got as
    # far as its first scenario.
    prometheus = PrometheusClient(settings.prometheus_url)
    llm = build_llm()
    registry = SystemRegistry(opensearch)
    log_tool = LogTool(opensearch)

    await opensearch.ensure_templates()
    pipeline = InvestigationPipeline(
        log_tool=log_tool,
        event_tool=EventTool(opensearch),
        metric_tool=MetricTool(prometheus),
        orchestrator=OrchestratorAgent(llm),
        react_agent=ReActAgent(llm),
        registry=registry,
        # Named `prometheus_client` on the pipeline; the window resolver uses it
        # for latency-based onset detection.
        prometheus_client=prometheus,
    )
    return (pipeline, [opensearch, prometheus, llm], opensearch,
            InvestigationStore(opensearch), prometheus)


async def countdown(seconds: int, label: str) -> None:
    deadline = time.time() + seconds
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        print(f"\r    {label}: {remaining:5.0f}s remaining ", end="", flush=True)
        await asyncio.sleep(min(5, remaining))
    print(f"\r    {label}: done{' ' * 20}")


async def error_rate_per_minute(client: OpenSearchClient, system_id: str,
                                minutes: int = 3) -> float:
    """Errors per minute over the last few minutes, straight from the index."""
    result = await client.search(settings.opensearch_log_index, {
        "size": 0,
        "track_total_hits": True,
        "query": {"bool": {"filter": [
            {"term": {"system.id": system_id}},
            {"terms": {"log.level": ["ERROR", "FATAL", "CRITICAL"]}},
            {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}},
        ]}},
    })
    return result["hits"]["total"]["value"] / minutes


async def workload_is_healthy(prometheus: PrometheusClient, namespace: str) -> bool:
    """True when every pod in the namespace is Ready.

    A low error rate is not by itself evidence of health, and assuming it was
    caused a real misdiagnosis: after a crashloop scenario, payment-api was down,
    so it served almost no traffic and produced almost no errors. The rate check
    passed, the next scenario was injected on top of a still-broken system, and
    its window filled with the previous failure.
    """
    try:
        result = await prometheus.query(
            f'min(kube_pod_status_ready{{namespace="{namespace}", condition="true"}})'
        )
    except PrometheusError:
        return True     # cannot tell; do not block the run on it
    if not result:
        return True
    try:
        return float(result[0]["value"][1]) >= 1.0
    except (KeyError, IndexError, ValueError):
        return True


async def wait_until_quiet(client: OpenSearchClient, prometheus: PrometheusClient,
                           system_id: str, *, namespace: str = "shopdemo",
                           threshold: float, timeout: int, hold: int = 180) -> bool:
    """Waits for the system to actually recover, rather than for a fixed delay.

    A scenario that scaled a deployment to zero is not finished when the API call
    returns — pods have to come back and the error rate has to fall. Sleeping a
    fixed number of seconds either wastes time or, worse, starts the next
    scenario while the previous one is still draining, which silently poisons the
    baseline window every downstream signal is measured against.

    Both conditions have to hold: errors back to baseline *and* every pod Ready.
    Either one alone can be satisfied by a system that is still broken.

    They must also hold *continuously* for `hold` seconds. This is not padding.
    The pipeline places its baseline window immediately before the incident —
    that is, before the moment the system went quiet — so a baseline drawn one
    minute after a reset still lands inside the previous scenario. Injecting only
    once the system has been calm for longer than the baseline window is the only
    way for that comparison to mean anything. It is the single largest cost in a
    full evaluation run, and skipping it silently invalidates the results.
    """
    deadline = time.time() + timeout
    quiet_since: float | None = None
    while time.time() < deadline:
        rate = await error_rate_per_minute(client, system_id)
        healthy = await workload_is_healthy(prometheus, namespace)
        remaining = deadline - time.time()

        if rate <= threshold and healthy:
            quiet_since = quiet_since or time.time()
            held = time.time() - quiet_since
            if held >= hold:
                print(f"\r    baseline quiet for {held:.0f}s "
                      f"({rate:.1f} errors/min, all pods ready){' ' * 16}")
                return True
            status = f"quiet for {held:3.0f}s of {hold}s"
        else:
            quiet_since = None
            status = "pods not ready" if not healthy else f"{rate:.1f} errors/min"

        print(f"\r    settling: {status:<28} {remaining:4.0f}s left ", end="", flush=True)
        await asyncio.sleep(5)
    rate = await error_rate_per_minute(client, system_id)
    healthy = await workload_is_healthy(prometheus, namespace)
    print(f"\r    WARNING: after {timeout}s still {rate:.1f} errors/min, "
          f"pods_ready={healthy}; the baseline may be contaminated{' ' * 10}")
    return False


async def score_one(pipeline: InvestigationPipeline, scenario_id: str, spec: dict,
                    system_id: str, environment: str, duration: str,
                    store: InvestigationStore | None = None) -> ScenarioScore:
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

    # Persist, exactly as the API route does. Without this an eval run leaves no
    # audit trail, so a scenario that scored badly cannot afterwards be opened up
    # with `python -m eval.explain` to see which stage went wrong — which is the
    # whole point of storing investigations.
    if store is not None:
        score.investigation_id = result.id
        await store.save(result)

    score.duration_s = time.perf_counter() - started

    # Read from the run, not asserted. These six lines used to be hardcoded —
    # `detected_signals = []`, `actual_cause = "Unknown"`, `confidence = 1.0` —
    # so every scenario scored 0% recall and a failed cause no matter what the
    # pipeline actually did, and a green run was indistinguishable from a broken
    # one. A harness that does not read its own subject measures nothing.
    score.detected_signals = sorted({s.type.value for s in result.signals})

    analysis = result.analysis
    score.actual_cause = analysis.category.value
    score.actual_service = (result.answer.root_cause_service if result.answer else None)
    score.confidence = (result.answer.confidence if result.answer
                        else analysis.confidence)
    # "react" vs "react (degraded)" vs "deterministic" — a run the model failed
    # on still produces an answer via the fallback, and that must not be counted
    # as the model getting it right.
    score.analyst = analysis.analyst
    score.agreed_with_engine = analysis.agrees_with_engine

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
        # The pipeline labels a fallback run "react (degraded)", never
        # "deterministic", so this counter could never fire and a run where the
        # model failed on every scenario looked identical to a clean sweep.
        deterministic = sum(1 for s in scored if "degraded" in s.analyst)
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
    # Short on purpose. Scenarios run back to back, so a long window would reach
    # into the previous one and score this scenario against contaminated
    # evidence. It is also the realistic case: you investigate a fresh incident
    # with a recent window.
    parser.add_argument("--duration", default="15m",
                        help="investigation window, used as-is with --duration-fixed or "
                             "--no-inject; otherwise derived from the time since the reset")
    parser.add_argument("--duration-fixed", action="store_true",
                        help="use --duration verbatim instead of deriving it from the reset")
    parser.add_argument("--quiet-seconds", type=int, default=420,
                        help="how long to wait, at most, for the system to settle after a reset")
    parser.add_argument("--quiet-hold", type=int, default=180,
                        help="how long the system must stay quiet before injecting. Must exceed "
                             "the baseline window or the baseline lands in the previous scenario")
    # The demo services each carry a 0.5% base error rate, and that compounds up
    # the three tiers, so a perfectly healthy shopdemo still sits at roughly
    # 4 errors/min. The threshold has to clear that floor or nothing is ever
    # "quiet"; real incidents run an order of magnitude higher (20-100+/min).
    parser.add_argument("--quiet-threshold", type=float, default=7.0,
                        help="errors/min at or below which the baseline counts as quiet")
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

    pipeline, closeables, opensearch, store, prometheus = await build_pipeline()
    scores: list[ScenarioScore] = []

    if not args.no_inject:
        total = sum(args.quiet_hold + 60 + catalogue[s]["settle_seconds"] + 60 for s in selected)
        print(f"Running {len(selected)} scenario(s). Rough estimate: "
              f"{total / 60:.0f}+ minutes (longer if a scenario is slow to drain).\n")

    try:
        for index, scenario_id in enumerate(selected, start=1):
            spec = catalogue[scenario_id]
            print(f"[{index}/{len(selected)}] {scenario_id}: {spec['title']}")

            duration = args.duration
            if not args.no_inject:
                await controller.reset_all()
                # Wait for the system to actually recover, not for a fixed delay.
                # A scenario that scaled a deployment to zero keeps producing
                # errors well after the reset call returns, and starting the next
                # one early puts the previous failure inside this one's baseline.
                quiet = await wait_until_quiet(
                    opensearch, prometheus, args.system_id,
                    namespace=controller.namespace,
                    threshold=args.quiet_threshold, timeout=args.quiet_seconds,
                    hold=args.quiet_hold,
                )
                if not quiet:
                    print("    (proceeding anyway; treat this scenario's result with caution)")
                quiet_at = time.time()
                await controller.start(scenario_id)
                await countdown(spec["settle_seconds"], "letting the incident develop")

                # Ask about exactly the stretch since the system went *quiet* —
                # not since the reset. Errors keep draining for a minute or two
                # after a reset returns, and including that tail puts the
                # previous scenario's failure inside this one's window, where it
                # precedes the injected fault and makes the correct answer look
                # like an effect that arrived before its cause.
                if not args.duration_fixed:
                    minutes = max(6, int((time.time() - quiet_at) / 60) + 1)
                    duration = f"{minutes}m"
                    print(f"    investigating the {duration} since the system went quiet")

            print("    investigating ...")
            score = await score_one(pipeline, scenario_id, spec,
                                    args.system_id, args.environment, duration,
                                    store=store)
            scores.append(score)

            verdict = "ERROR" if score.error else (
                f"recall {score.recall:.0%}, cause "
                f"{'correct' if score.cause_correct else 'WRONG (' + score.actual_cause + ')'}"
            )
            trace = f"  (python -m eval.explain {score.investigation_id})" if score.investigation_id else ""
            print(f"    {verdict}  [{score.duration_s:.0f}s]{trace}\n")

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

    # Record the model that actually ran. Hardcoding `ollama_model` labelled
    # every report "qwen2.5-coder" regardless of provider, so a Gemini run and a
    # local run were indistinguishable in the results — which makes comparing
    # two reports meaningless, the one thing a report is for.
    llm_used = build_llm()
    Path(args.report).write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": describe_provider(llm_used),
        "model": describe_model(llm_used),
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
