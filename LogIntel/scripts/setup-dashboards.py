#!/usr/bin/env python3
"""
Sets up OpenSearch Dashboards so logs, Kubernetes events, and the metrics
mirror are all visible from one place — Discover for raw browsing, plus a
small overview dashboard.

Idempotent: every saved object below has a fixed id and is created with
overwrite=true, so re-running this after changing a query just updates the
existing object instead of duplicating it.

Standard library only. Run from WSL, where localhost:5601 is reachable:

    python3 scripts/setup-dashboards.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DASHBOARDS_URL = os.getenv("OSD_URL", "http://localhost:5601").rstrip("/")

INDEX_PATTERNS = {
    "logintel-logs-*": "ip-logintel-logs",
    "logintel-events-*": "ip-logintel-events",
    "logintel-metrics-mirror-*": "ip-logintel-metrics-mirror",
}


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{DASHBOARDS_URL}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "osd-xsrf": "true"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail[:400]}") from exc


def upsert(saved_type: str, object_id: str, attributes: dict,
          references: list[dict] | None = None) -> None:
    call("POST", f"/api/saved_objects/{saved_type}/{object_id}?overwrite=true", {
        "attributes": attributes,
        "references": references or [],
    })
    print(f"  ok  {saved_type:<14} {object_id}")


def index_pattern(object_id: str, title: str) -> None:
    upsert("index-pattern", object_id, {"title": title, "timeFieldName": "@timestamp"})


def visualization(object_id: str, title: str, index_pattern_id: str, vis_state: dict,
                  query: str = "", language: str = "kuery") -> None:
    upsert("visualization", object_id, {
        "title": title,
        "visState": json.dumps(vis_state),
        "uiStateJSON": "{}",
        "description": "",
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": json.dumps({
                "query": {"query": query, "language": language},
                "filter": [],
                "indexRefName": "kibanaSavedObjectMeta.searchSourceJSON.index",
            }),
        },
    }, references=[{
        "name": "kibanaSavedObjectMeta.searchSourceJSON.index",
        "type": "index-pattern", "id": index_pattern_id,
    }])


def dashboard(object_id: str, title: str, description: str,
             panels: list[tuple[str, int, int, int, int]]) -> None:
    """panels: list of (visualization_id, x, y, w, h). Grid is 48 units wide."""
    panels_json, references = [], []
    for index, (viz_id, x, y, w, h) in enumerate(panels, start=1):
        ref_name = f"panel_{index}"
        panels_json.append({
            "version": "2.19.0",
            "gridData": {"x": x, "y": y, "w": w, "h": h, "i": str(index)},
            "panelIndex": str(index),
            "embeddableConfig": {},
            "panelRefName": ref_name,
        })
        references.append({"name": ref_name, "type": "visualization", "id": viz_id})

    upsert("dashboard", object_id, {
        "title": title,
        "description": description,
        "hits": 0,
        "panelsJSON": json.dumps(panels_json),
        "optionsJSON": json.dumps({"useMargins": True, "hidePanelTitles": False}),
        "timeRestore": True,
        "timeTo": "now",
        "timeFrom": "now-3h",
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": json.dumps({"query": {"query": "", "language": "kuery"}, "filter": []}),
        },
    }, references=references)


def main() -> int:
    print(f"Setting up OpenSearch Dashboards at {DASHBOARDS_URL}\n")
    try:
        call("GET", "/api/status")
    except RuntimeError as exc:
        print(f"Cannot reach OpenSearch Dashboards: {exc}")
        return 2

    print("Index patterns:")
    for title, object_id in INDEX_PATTERNS.items():
        index_pattern(object_id, title)

    logs_id = INDEX_PATTERNS["logintel-logs-*"]
    events_id = INDEX_PATTERNS["logintel-events-*"]
    metrics_id = INDEX_PATTERNS["logintel-metrics-mirror-*"]

    print("\nVisualizations:")

    # -- log volume by level, over time -----------------------------------
    visualization("viz-log-volume-by-level", "Log volume by level", logs_id, {
        "title": "Log volume by level",
        "type": "histogram",
        "params": {
            "type": "histogram", "grid": {"categoryLines": False},
            "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom",
                              "show": True, "style": {}, "scale": {"type": "linear"},
                              "labels": {"show": True, "truncate": 100}, "title": {}}],
            "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
                           "position": "left", "show": True, "style": {},
                           "scale": {"type": "linear", "mode": "normal"},
                           "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                           "title": {"text": "Count"}}],
            "seriesParams": [{"show": True, "type": "histogram", "mode": "stacked",
                              "data": {"label": "Count", "id": "1"}, "valueAxis": "ValueAxis-1",
                              "drawLinesBetweenPoints": True, "lineWidth": 2, "showCircles": True}],
            "addTooltip": True, "addLegend": True, "legendPosition": "right",
            "times": [], "addTimeMarker": False,
        },
        "aggs": [
            {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "date_histogram", "schema": "segment",
             "params": {"field": "@timestamp", "interval": "auto", "min_doc_count": 1}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "group",
             "params": {"field": "log.level", "size": 6, "order": "desc", "orderBy": "1"}},
        ],
    })

    # -- top error message patterns -----------------------------------------
    visualization("viz-top-error-messages", "Top error messages", logs_id, {
        "title": "Top error messages",
        "type": "table",
        "params": {"perPage": 10, "showPartialRows": False, "showMetricsAtAllLevels": False,
                   "showTotal": False, "totalFunc": "sum"},
        "aggs": [
            {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "log.message.keyword", "size": 10, "order": "desc", "orderBy": "1"}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "bucket",
             "params": {"field": "service.name", "size": 5, "order": "desc", "orderBy": "1"}},
        ],
    }, query='log.level: ERROR or log.level: FATAL or log.level: CRITICAL')

    # -- kubernetes event reasons --------------------------------------------
    visualization("viz-k8s-event-reasons", "Kubernetes event reasons", events_id, {
        "title": "Kubernetes event reasons",
        "type": "pie",
        "params": {"type": "pie", "addTooltip": True, "addLegend": True,
                   "legendPosition": "right", "isDonut": True,
                   "labels": {"show": True, "values": True, "last_level": True, "truncate": 100}},
        "aggs": [
            {"id": "1", "enabled": True, "type": "count", "schema": "metric", "params": {}},
            {"id": "2", "enabled": True, "type": "terms", "schema": "segment",
             "params": {"field": "event.reason", "size": 12, "order": "desc", "orderBy": "1"}},
        ],
    })

    # -- resource usage (from the metrics mirror) ----------------------------
    visualization("viz-cpu-usage", "CPU usage (cores) by pod", metrics_id, {
        "title": "CPU usage (cores) by pod",
        "type": "line",
        "params": {
            "type": "line", "grid": {"categoryLines": False},
            "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom",
                              "show": True, "style": {}, "scale": {"type": "linear"},
                              "labels": {"show": True, "truncate": 100}, "title": {}}],
            "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
                           "position": "left", "show": True, "style": {},
                           "scale": {"type": "linear", "mode": "normal"},
                           "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                           "title": {"text": "cores"}}],
            "seriesParams": [{"show": True, "type": "line", "mode": "normal",
                              "data": {"label": "Average value", "id": "1"}, "valueAxis": "ValueAxis-1",
                              "drawLinesBetweenPoints": True, "lineWidth": 2, "showCircles": True}],
            "addTooltip": True, "addLegend": True, "legendPosition": "right",
            "times": [], "addTimeMarker": False,
        },
        "aggs": [
            {"id": "1", "enabled": True, "type": "avg", "schema": "metric", "params": {"field": "value"}},
            {"id": "2", "enabled": True, "type": "date_histogram", "schema": "segment",
             "params": {"field": "@timestamp", "interval": "auto", "min_doc_count": 1}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "group",
             "params": {"field": "pod", "size": 6, "order": "desc", "orderBy": "1"}},
        ],
    }, query="metric: cpu_usage_cores")

    visualization("viz-http-error-rate", "HTTP error rate by service", metrics_id, {
        "title": "HTTP error rate by service",
        "type": "line",
        "params": {
            "type": "line", "grid": {"categoryLines": False},
            "categoryAxes": [{"id": "CategoryAxis-1", "type": "category", "position": "bottom",
                              "show": True, "style": {}, "scale": {"type": "linear"},
                              "labels": {"show": True, "truncate": 100}, "title": {}}],
            "valueAxes": [{"id": "ValueAxis-1", "name": "LeftAxis-1", "type": "value",
                           "position": "left", "show": True, "style": {},
                           "scale": {"type": "linear", "mode": "normal"},
                           "labels": {"show": True, "rotate": 0, "filter": False, "truncate": 100},
                           "title": {"text": "req/s"}}],
            "seriesParams": [{"show": True, "type": "line", "mode": "normal",
                              "data": {"label": "Average value", "id": "1"}, "valueAxis": "ValueAxis-1",
                              "drawLinesBetweenPoints": True, "lineWidth": 2, "showCircles": True}],
            "addTooltip": True, "addLegend": True, "legendPosition": "right",
            "times": [], "addTimeMarker": False,
        },
        "aggs": [
            {"id": "1", "enabled": True, "type": "avg", "schema": "metric", "params": {"field": "value"}},
            {"id": "2", "enabled": True, "type": "date_histogram", "schema": "segment",
             "params": {"field": "@timestamp", "interval": "auto", "min_doc_count": 1}},
            {"id": "3", "enabled": True, "type": "terms", "schema": "group",
             "params": {"field": "service", "size": 6, "order": "desc", "orderBy": "1"}},
        ],
    }, query="metric: http_error_rate")

    print("\nDashboard:")
    dashboard("dash-logintel-overview", "LogIntel Overview",
             "Application logs, Kubernetes events, and mirrored metrics for the shopdemo "
             "system, in one place. Metrics here are a read-only mirror for browsing — "
             "the agent pipeline queries Prometheus directly (see metrics-mirror/mirror.py).",
             panels=[
                 ("viz-log-volume-by-level", 0, 0, 24, 15),
                 ("viz-k8s-event-reasons", 24, 0, 24, 15),
                 ("viz-top-error-messages", 0, 15, 24, 15),
                 ("viz-cpu-usage", 24, 15, 24, 15),
                 ("viz-http-error-rate", 0, 30, 48, 15),
             ])

    print(f"\nDone. Open: {DASHBOARDS_URL}/app/dashboards#/view/dash-logintel-overview")
    print(f"Or browse raw documents: {DASHBOARDS_URL}/app/data-explorer/discover")
    return 0


if __name__ == "__main__":
    sys.exit(main())
