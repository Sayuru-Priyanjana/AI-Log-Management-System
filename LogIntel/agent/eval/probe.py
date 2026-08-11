"""
Diagnostic probe — inspects what each layer of the pipeline actually sees.

The evaluation harness scores the final answer. This scores the *inputs*: what
is in the indices right now, what the individual tools return for a given
window, and which signals fire. When a run produces a surprising conclusion,
this is how you find out whether the problem is retrieval, feature extraction,
or the model.

    python -m eval.probe state              # index counts, error rates, events
    python -m eval.probe tools [minutes]    # run every tool for a window
    python -m eval.probe prom               # check each PromQL template
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta

from app.config import settings
from app.models.domain import TimeWindow, utcnow
from app.models.plan import Intent, InvestigationPlan
from app.pipeline.signals import SignalEngine
from app.pipeline.windows import WindowResolver
from app.models.analysis import InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.registry.systems import SystemRegistry
from app.sources.opensearch import OpenSearchClient
from app.sources.prometheus import PrometheusClient
from app.tools.events import EventTool
from app.tools.logs import LogTool
from app.tools.metrics import SPECS, MetricTool


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


async def probe_state(minutes: int = 30) -> None:
    client = OpenSearchClient()
    try:
        rule(f"INDEX STATE (last {minutes}m)")
        for index in (settings.opensearch_log_index, settings.opensearch_event_index,
                      "logintel-metrics-mirror-*", settings.opensearch_investigation_index):
            try:
                total = await client.count(index)
                print(f"  {index:<36} {total:>8} docs (all time)")
            except Exception as exc:
                print(f"  {index:<36} ERROR: {exc}")

        rule(f"LOG LEVELS BY SERVICE (last {minutes}m, shopdemo)")
        result = await client.search(settings.opensearch_log_index, {
            "size": 0,
            "query": {"bool": {"filter": [
                {"term": {"system.id": "shopdemo"}},
                {"range": {"@timestamp": {"gte": f"now-{minutes}m"}}},
            ]}},
            "aggs": {
                "svc": {
                    "terms": {"field": "service.name", "size": 10},
                    "aggs": {"lvl": {"terms": {"field": "log.level", "size": 6}}},
                },
                "lvl": {"terms": {"field": "log.level", "size": 6}},
                "unparsed": {"filter": {"term": {"parse.failed": True}}},
            },
        })
        aggs = result.get("aggregations", {})
        print(f"  total docs: {result['hits']['total']['value']}   "
              f"unparsed: {aggs.get('unparsed', {}).get('doc_count', 0)}")
        print("  overall by level: " + ", ".join(
            f"{b['key']}={b['doc_count']}" for b in aggs.get("lvl", {}).get("buckets", [])) or "none")
        for svc in aggs.get("svc", {}).get("buckets", []):
            levels = ", ".join(f"{b['key']}={b['doc_count']}"
                               for b in svc.get("lvl", {}).get("buckets", []))
            print(f"    {svc['key']:<16} {svc['doc_count']:>7}  ({levels})")

        rule(f"KUBERNETES EVENTS (last {minutes}m)")
        result = await client.search(settings.opensearch_event_index, {
            "size": 20,
            "query": {"bool": {"filter": [
                {"range": {"event.last_timestamp": {"gte": f"now-{minutes}m"}}},
            ]}},
            "sort": [{"event.last_timestamp": {"order": "desc", "unmapped_type": "date"}}],
        })
        hits = result.get("hits", {}).get("hits", [])
        if not hits:
            print("  (none)")
        for hit in hits:
            src = hit["_source"]
            ev = src.get("event", {})
            pod = src.get("kubernetes", {}).get("pod", {}).get("name")
            print(f"  {ev.get('last_timestamp')}  {ev.get('type'):<8} {ev.get('reason'):<22} "
                  f"x{ev.get('count'):<4} {pod}")
            print(f"      severity={ev.get('severity')}  first={ev.get('first_timestamp')}")
    finally:
        await client.close()


async def probe_tools(minutes: int = 30) -> None:
    client = OpenSearchClient()
    prom = PrometheusClient()
    registry = SystemRegistry(client)
    try:
        system = await registry.require("shopdemo")
        plan = InvestigationPlan(
            intent=Intent.INCIDENT_INVESTIGATION,
            system_id="shopdemo", system_name=system.name, environment="staging",
            service=None, namespaces=system.namespaces,
            requested_window=TimeWindow.last(f"{minutes}m"),
            tools=["logs", "events", "metrics"], goal="probe",
        )

        rule("WINDOW RESOLUTION")
        resolver = WindowResolver(LogTool(client), prometheus=prom)
        windows, buckets = await resolver.resolve(plan)
        print(f"  requested : {windows.requested}")
        print(f"  incident  : {windows.incident}")
        print(f"  baseline  : {windows.baseline or 'NONE'}")
        print(f"  onset     : {windows.onset}  detected={windows.onset_detected} "
              f"before_window={windows.onset_before_window}")
        print(f"  method    : {windows.method}")
        counts = [b.errors for b in buckets]
        print(f"  error buckets ({len(counts)}): {counts}")

        rule("LOG TOOL")
        logs = await LogTool(client).collect(plan, windows.incident, windows.baseline)
        print(f"  status={logs.status} docs={logs.total_documents} "
              f"baseline_docs={logs.baseline_documents} unparsed={logs.unparsed_documents}")
        print(f"  levels: {logs.totals_by_level}   baseline: {logs.baseline_totals_by_level}")
        print(f"  dependency graph (from logs): {logs.dependency_edges or 'none observed'}")
        if logs.dependency_edges:
            services = set(logs.dependency_edges) | {
                d for v in logs.dependency_edges.values() for d in v}
            print("  depth (higher = further up the call chain): " + ", ".join(
                f"{s}={logs.depth_of(s)}" for s in sorted(services, key=logs.depth_of, reverse=True)))
        print(f"  patterns ({len(logs.patterns)}):")
        for p in logs.patterns[:12]:
            flag = "NEW " if p.is_new else "    "
            print(f"    {flag}{p.level:<6} {str(p.service):<14} x{p.count:<5} "
                  f"base={p.baseline_count:<5} {p.example[:70]}")

        rule("EVENT TOOL")
        events = await EventTool(client).collect(plan, windows.incident, windows.baseline)
        print(f"  status={events.status}  events={len(events.events)}")
        for e in events.events:
            print(f"    {e.severity:<9} {e.reason:<22} x{e.count:<4} pod={e.pod} "
                  f"onset={e.onset}")
        print(f"  baseline reasons: {events.baseline_reasons}")

        rule("METRIC TOOL")
        metrics = await MetricTool(prom).collect(plan, windows.incident, windows.baseline)
        print(f"  status={metrics.status} reason={metrics.reason}")
        print(f"  series={len(metrics.series)}  unavailable={list(metrics.unavailable)}")
        by_metric: dict[str, list] = {}
        for s in metrics.series:
            by_metric.setdefault(s.metric, []).append(s)
        for name in sorted(by_metric):
            series_list = by_metric[name]
            print(f"    {name} ({len(series_list)} series)")
            for s in series_list[:4]:
                scope = s.pod or s.service or "-"
                base = (f"{s.baseline.average:.4g}"
                        if s.baseline and s.baseline.average is not None else "n/a")
                print(f"      {scope:<34} avg={s.incident.average:<10.4g} "
                      f"max={s.incident.maximum:<10.4g} base={base}")

        rule("SIGNAL ENGINE")
        bundle = EvidenceBundle(logs=logs, events=events, metrics=metrics)
        signals = SignalEngine(known_services=system.service_names).detect(plan, windows, bundle)
        if not signals:
            print("  (no signals fired)")
        for s in signals:
            mag = s.magnitude.describe() if s.magnitude else ""
            print(f"  {s.severity.value:<9} {s.type.value:<24} svc={str(s.service):<14} "
                  f"onset={s.first_seen}  {mag}")
        print(f"\n  evidence gaps: {bundle.gaps()}")
    finally:
        await client.close()
        await prom.close()


async def probe_prom() -> None:
    prom = PrometheusClient()
    try:
        rule("PROMQL TEMPLATES (instant query, namespace=shopdemo)")
        window = TimeWindow(start=utcnow() - timedelta(minutes=10), end=utcnow())
        for spec in SPECS:
            expression = spec.expr.format(ns='namespace="shopdemo"')
            try:
                instant = await prom.query(expression)
                ranged = await prom.query_range(expression, window, prom.step_for(window))
                status = "ok " if instant else "EMPTY"
                sample = ""
                if instant:
                    labels = {k: v for k, v in instant[0].get("metric", {}).items()
                              if k != "__name__"}
                    sample = f"  e.g. {labels} = {instant[0].get('value', ['', '?'])[1]}"
                print(f"  {status} {spec.metric:<28} instant={len(instant):<3} "
                      f"range={len(ranged):<3}{sample}")
            except Exception as exc:
                print(f"  ERR {spec.metric:<28} {exc}")
    finally:
        await prom.close()


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "state"
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    if command == "state":
        await probe_state(minutes)
    elif command == "tools":
        await probe_tools(minutes)
    elif command == "prom":
        await probe_prom()
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
