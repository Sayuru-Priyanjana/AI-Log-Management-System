from __future__ import annotations

import logging
from datetime import datetime

from app.config import settings
from app.models.domain import TimeWindow, ensure_utc
from app.models.evidence import EventEvidence, K8sEvent
from app.models.plan import InvestigationPlan
from app.sources.opensearch import OpenSearchClient, OpenSearchError

logger = logging.getLogger(__name__)


def _dig(source: dict, path: str):
    node = source
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


class EventTool:
    """Kubernetes events, with their aggregation preserved.

    A single event document can stand for hundreds of occurrences spread over
    hours: `count` says how many, `first_timestamp` when it started, and
    `@timestamp` only when it last fired. Reading just the document timestamp
    would place a long-running problem *after* the effects it caused, which
    inverts the causal ordering the whole pipeline depends on.
    """

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client
        self._index = settings.opensearch_event_index

    @staticmethod
    def _filters(plan: InvestigationPlan, window: TimeWindow) -> list[dict]:
        filters: list[dict] = [
            {"term": {"system.id": plan.system_id}},
            {"term": {"environment": plan.environment}},
            # Active during the window: it last fired inside it, or it started
            # inside it. Either way it is relevant.
            {"bool": {"should": [
                {"range": {"event.last_timestamp": {"gte": window.start.isoformat(),
                                                    "lte": window.end.isoformat()}}},
                {"range": {"event.first_timestamp": {"gte": window.start.isoformat(),
                                                     "lte": window.end.isoformat()}}},
            ], "minimum_should_match": 1}},
        ]
        if plan.namespaces:
            filters.append({"terms": {"kubernetes.namespace": plan.namespaces}})
        return filters

    @staticmethod
    def _to_event(hit: dict) -> K8sEvent | None:
        source = hit.get("_source", {})
        reason = _dig(source, "event.reason")
        if not reason:
            return None
        pod = _dig(source, "kubernetes.pod.name")
        return K8sEvent(
            id=f"evt:{pod or _dig(source, 'involved_object.name') or 'cluster'}:{reason}",
            reason=reason,
            type=_dig(source, "event.type") or "Normal",
            severity=_dig(source, "event.severity") or "info",
            message=(_dig(source, "event.message") or "")[:600],
            count=int(_dig(source, "event.count") or 1),
            first_timestamp=_parse(_dig(source, "event.first_timestamp")),
            last_timestamp=_parse(_dig(source, "event.last_timestamp"))
            or _parse(_dig(source, "@timestamp")),
            namespace=_dig(source, "kubernetes.namespace"),
            pod=pod,
            container=_dig(source, "kubernetes.container.name"),
            node=_dig(source, "kubernetes.node.name"),
            service=_dig(source, "service.name"),
            involved_kind=_dig(source, "involved_object.kind"),
            involved_name=_dig(source, "involved_object.name"),
        )

    async def collect(self, plan: InvestigationPlan, incident: TimeWindow,
                      baseline: TimeWindow | None) -> EventEvidence:
        evidence = EventEvidence()
        try:
            body = {
                "size": settings.max_events,
                "query": {"bool": {"filter": self._filters(plan, incident)}},
                # Warnings first: a window full of routine Pulled/Started events
                # should not push the one FailedScheduling off the end.
                "sort": [
                    {"event.severity": {"order": "desc",
                                        "unmapped_type": "keyword"}},
                    {"event.last_timestamp": {"order": "desc",
                                              "unmapped_type": "date"}},
                ],
            }
            result = await self._client.search(self._index, body)
            events = [
                event for event in (self._to_event(hit)
                                    for hit in result.get("hits", {}).get("hits", []))
                if event is not None
            ]
            # Deduplicate: the same reason on the same pod is one condition, and
            # keeping the highest count preserves how persistent it was.
            merged: dict[str, K8sEvent] = {}
            for event in events:
                current = merged.get(event.id)
                if current is None or event.count > current.count:
                    merged[event.id] = event
            evidence.events = sorted(
                merged.values(),
                key=lambda e: (e.onset or incident.start),
            )

            if baseline is not None:
                baseline_body = {
                    "size": 0,
                    "query": {"bool": {"filter": self._filters(plan, baseline)}},
                    "aggs": {"reasons": {"terms": {"field": "event.reason", "size": 30}}},
                }
                baseline_result = await self._client.search(self._index, baseline_body)
                evidence.baseline_reasons = {
                    bucket["key"]: bucket["doc_count"]
                    for bucket in baseline_result.get("aggregations", {})
                    .get("reasons", {}).get("buckets", [])
                }
        except OpenSearchError as exc:
            logger.warning("Event collection failed: %s", exc)
            evidence.status = "unavailable"
            evidence.reason = str(exc)
        return evidence
