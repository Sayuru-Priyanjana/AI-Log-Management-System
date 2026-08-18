#!/usr/bin/env python3
"""
Metrics mirror — for OpenSearch Dashboards only.

The agent pipeline never reads from this index. It queries Prometheus directly,
because rate()/histogram_quantile() need a real time-series database, and the v1
system's mistake was running metrics through two pipelines that drifted apart
(see Agent/docs/Pipeline Redesign.md, section 1.9 "two competing metric
pipelines"). That mistake is not being repeated here.

What this script does instead is narrower: poll the same handful of PromQL
queries the agent's signal engine actually looks at, and write the results into
OpenSearch purely so a human browsing Dashboards can see logs, events and
metrics side by side. It is a read-only mirror with one direction of flow —
Prometheus to OpenSearch — and nothing downstream of it ever writes back or
depends on its freshness.

Standard library only, so the container needs no image build step beyond
python:3.11-slim.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://172.23.80.1:30090").rstrip("/")
OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://opensearch:9200").rstrip("/")
INDEX_PREFIX = os.getenv("INDEX_PREFIX", "logintel-metrics-mirror")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
SYSTEM_ID = os.getenv("SYSTEM_ID", "")
NAMESPACE = os.getenv("NAMESPACE", "")

# Build a safe prefix for PromQL selectors
# e.g. 'system_id="foo",' or ''
_filters = []
if SYSTEM_ID: _filters.append(f'system_id="{SYSTEM_ID}"')
if NAMESPACE: _filters.append(f'namespace="{NAMESPACE}"')
SELECTOR = ",".join(_filters) + "," if _filters else ""

# Deliberately the same shape of signal the agent's SignalEngine reads — see
# agent/app/tools/metrics.py SPECS. Kept as a small, separate list rather than
# imported, so this container has no dependency on the agent's package layout.
QUERIES: tuple[tuple[str, str, str], ...] = (
    ("cpu_usage_cores", "cores",
     f'sum by (system_id, namespace, pod, container) (rate(container_cpu_usage_seconds_total{{{SELECTOR} container!=""}}[2m]))'),
    ("cpu_throttle_ratio", "ratio",
     f'sum by (system_id, namespace, pod, container) (rate(container_cpu_cfs_throttled_seconds_total{{{SELECTOR} container!=""}}[2m])) '
     f'/ clamp_min(sum by (system_id, namespace, pod, container) (rate(container_cpu_cfs_periods_total{{{SELECTOR} container!=""}}[2m])), 0.001)'),
    ("memory_working_set_bytes", "bytes",
     f'max by (system_id, namespace, pod, container) (container_memory_working_set_bytes{{{SELECTOR} container!=""}})'),
    ("pod_restarts_total", "count",
     f'max by (system_id, namespace, pod, container) (kube_pod_container_status_restarts_total{{{SELECTOR} job!=""}})'),
    ("pod_ready", "bool",
     f'max by (system_id, namespace, pod) (kube_pod_status_ready{{{SELECTOR} condition="true"}})'),
    ("http_request_rate", "req/s",
     f'sum by (system_id, namespace, service) (rate(http_requests_total{{{SELECTOR} job!=""}}[2m]))'),
    ("http_error_rate", "req/s",
     f'sum by (system_id, namespace, service) (rate(http_requests_total{{{SELECTOR} status=~"5.."}}[2m]))'),
    ("http_latency_p95", "seconds",
     f'histogram_quantile(0.95, sum by (system_id, namespace, service, le) (rate(http_request_duration_seconds_bucket{{{SELECTOR} job!=""}}[2m])))'),
    ("dependency_failure_rate", "req/s",
     f'sum by (system_id, namespace, service, dependency) (rate(app_dependency_requests_total{{{SELECTOR} outcome=~"failure|unreachable"}}[2m]))'),
)

INDEX_TEMPLATE = {
    "index_patterns": [f"{INDEX_PREFIX}-*"],
    "priority": 200,
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "10s"},
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "metric": {"type": "keyword"},
                "unit": {"type": "keyword"},
                "value": {"type": "double"},
                "pod": {"type": "keyword"},
                "container": {"type": "keyword"},
                "service": {"type": "keyword"},
                "dependency": {"type": "keyword"},
                "namespace": {"type": "keyword"},
                "system_id": {"type": "keyword"},
            }
        },
    },
}


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp}  {message}", flush=True)


def http_json(url: str, method: str = "GET", body: bytes | None = None,
             headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, data=body, method=method,
                                     headers=headers or {})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def ensure_template() -> None:
    body = json.dumps(INDEX_TEMPLATE).encode()
    request = urllib.request.Request(
        f"{OPENSEARCH_URL}/_index_template/{INDEX_PREFIX}",
        data=body, method="PUT", headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response.read()
    log(f"index template '{INDEX_PREFIX}' applied")


def query_prometheus(expression: str) -> list[dict]:
    from urllib.parse import urlencode
    url = f"{PROMETHEUS_URL}/api/v1/query?" + urlencode({"query": expression})
    data = http_json(url)
    if data.get("status") != "success":
        raise RuntimeError(data.get("error", "unknown Prometheus error"))
    return data.get("data", {}).get("result", [])


def bulk_index(docs: list[dict]) -> None:
    if not docs:
        return
    index = f"{INDEX_PREFIX}-{datetime.now(timezone.utc):%Y.%m.%d}"
    lines = []
    for doc in docs:
        lines.append(json.dumps({"index": {"_index": index}}))
        lines.append(json.dumps(doc, default=str))
    body = ("\n".join(lines) + "\n").encode()
    request = urllib.request.Request(
        f"{OPENSEARCH_URL}/_bulk", data=body, method="POST",
        headers={"Content-Type": "application/x-ndjson"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    if result.get("errors"):
        failed = sum(1 for item in result.get("items", []) if item.get("index", {}).get("error"))
        log(f"WARNING: {failed} document(s) rejected by OpenSearch")


def poll_once() -> int:
    now = datetime.now(timezone.utc).isoformat()
    docs: list[dict] = []
    for metric_name, unit, expression in QUERIES:
        try:
            for series in query_prometheus(expression):
                labels = series.get("metric", {})
                _, raw_value = series.get("value", [None, None])
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value != value:  # NaN
                    continue
                docs.append({
                    "@timestamp": now, "metric": metric_name, "unit": unit, "value": value,
                    "namespace": labels.get("namespace"),
                    "system_id": labels.get("system_id"),
                    "pod": labels.get("pod"), "container": labels.get("container"),
                    "service": labels.get("service"), "dependency": labels.get("dependency"),
                })
        except (urllib.error.URLError, RuntimeError) as exc:
            log(f"WARNING: query for {metric_name} failed: {exc}")
    bulk_index(docs)
    return len(docs)


def main() -> None:
    log(f"metrics-mirror starting: {PROMETHEUS_URL} -> {OPENSEARCH_URL} "
       f"(index {INDEX_PREFIX}-*, every {POLL_SECONDS}s, namespace={NAMESPACE})")
    log("this mirror is for OpenSearch Dashboards visualization only — "
       "the agent pipeline reads Prometheus directly and never uses this index")

    while True:
        try:
            ensure_template()
            break
        except (urllib.error.URLError, OSError) as exc:
            log(f"OpenSearch not ready ({exc}); retrying in 5s")
            time.sleep(5)

    while True:
        try:
            written = poll_once()
            log(f"wrote {written} metric point(s)")
        except Exception as exc:
            log(f"ERROR: poll failed: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
