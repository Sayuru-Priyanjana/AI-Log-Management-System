from __future__ import annotations

import logging
from datetime import datetime

from app.config import settings
from app.models.domain import TimeWindow, ensure_utc
from app.models.evidence import LogBucket, LogEvidence, LogPattern, LogSample
from app.models.plan import InvestigationPlan
from app.sources.opensearch import OpenSearchClient, OpenSearchError
from app.tools.fingerprint import fingerprint, pattern_id

logger = logging.getLogger(__name__)

ERROR_LEVELS = ("ERROR", "FATAL", "CRITICAL")

_SAMPLE_FIELDS = [
    "@timestamp", "log.level", "log.message", "service.name", "kubernetes.pod.name",
    "http.status_code", "error.type", "trace.id",
]


def _dig(source: dict, path: str):
    node = source
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


class LogTool:
    """Aggregation-first log retrieval.

    Nothing here bulk-fetches raw documents. A 30-minute window can hold tens of
    thousands of lines; asking for the "first 500" of those returns the first
    three minutes and misses the incident entirely. Instead the index does the
    counting and grouping, and only a handful of representative lines are pulled
    back.
    """

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client
        self._index = settings.opensearch_log_index

    # -- filters -----------------------------------------------------------
    @staticmethod
    def base_filters(plan: InvestigationPlan, window: TimeWindow) -> list[dict]:
        """Scope is the *system*, never the single focus service.

        Narrowing to the service named in the question would hide the tier
        underneath it — and in a dependency failure that tier is the answer.
        """
        filters: list[dict] = [
            {"term": {"system.id": plan.system_id}},
            {"term": {"environment": plan.environment}},
            {"range": {"@timestamp": {"gte": window.start.isoformat(),
                                      "lte": window.end.isoformat()}}},
        ]
        if plan.namespaces:
            filters.append({"terms": {"kubernetes.namespace": plan.namespaces}})
        return filters

    # -- histogram ---------------------------------------------------------
    async def histogram(self, plan: InvestigationPlan, window: TimeWindow,
                        interval: str = "60s") -> list[LogBucket]:
        body = {
            "size": 0,
            "query": {"bool": {"filter": self.base_filters(plan, window)}},
            "aggs": {
                "over_time": {
                    "date_histogram": {
                        "field": "@timestamp",
                        "fixed_interval": interval,
                        "min_doc_count": 0,
                        "extended_bounds": {
                            "min": window.start.isoformat(),
                            "max": window.end.isoformat(),
                        },
                    },
                    "aggs": {"levels": {"terms": {"field": "log.level", "size": 10}}},
                }
            },
        }
        result = await self._client.search(self._index, body)
        buckets = []
        for raw in result.get("aggregations", {}).get("over_time", {}).get("buckets", []):
            levels = {
                level["key"]: level["doc_count"]
                for level in raw.get("levels", {}).get("buckets", [])
            }
            buckets.append(LogBucket(
                timestamp=datetime.fromisoformat(raw["key_as_string"].replace("Z", "+00:00")),
                total=raw["doc_count"],
                by_level=levels,
            ))
        return buckets

    # -- patterns ----------------------------------------------------------
    async def _pattern_query(self, plan: InvestigationPlan, window: TimeWindow,
                             with_examples: bool) -> dict:
        message_aggs: dict = {
            "first": {"min": {"field": "@timestamp"}},
            "last": {"max": {"field": "@timestamp"}},
            "levels": {"terms": {"field": "log.level", "size": 3}},
        }
        if with_examples:
            message_aggs["example"] = {
                "top_hits": {"size": 1, "_source": {"includes": _SAMPLE_FIELDS}}
            }

        body = {
            "size": 0,
            # OpenSearch stops counting at 10,000 by default, so without this the
            # reported document count silently pins to 10000 and understates a
            # busy window by any amount.
            "track_total_hits": True,
            "query": {"bool": {"filter": self.base_filters(plan, window)}},
            "aggs": {
                "levels": {"terms": {"field": "log.level", "size": 10}},
                "unparsed": {"filter": {"term": {"parse.failed": True}}},
                # Who calls whom, taken from the services' own dependency logs.
                # This is what lets root-cause attribution follow the call graph
                # instead of guessing from onset times.
                "dependencies": {
                    "filter": {"exists": {"field": "dependency.name"}},
                    "aggs": {
                        "caller": {
                            "terms": {"field": "service.name", "size": 20},
                            "aggs": {
                                "calls": {"terms": {"field": "dependency.name", "size": 20}},
                            },
                        },
                    },
                },
                "parsed": {
                    # Lines Fluent Bit could not parse are counted but excluded
                    # from pattern analysis; they are noise, not evidence.
                    "filter": {"bool": {"must_not": [{"term": {"parse.failed": True}}]}},
                    "aggs": {
                        "by_service": {
                            "terms": {"field": "service.name", "size": 20},
                            "aggs": {
                                "by_message": {
                                    "terms": {
                                        "field": "log.message.keyword",
                                        "size": settings.max_log_patterns * 2,
                                    },
                                    "aggs": message_aggs,
                                }
                            },
                        }
                    },
                },
            },
        }
        return await self._client.search(self._index, body)

    @staticmethod
    def _dependency_edges(result: dict) -> dict[str, list[str]]:
        edges: dict[str, list[str]] = {}
        callers = (result.get("aggregations", {}).get("dependencies", {})
                   .get("caller", {}).get("buckets", []))
        for caller in callers:
            names = [c["key"] for c in caller.get("calls", {}).get("buckets", [])]
            if names:
                edges[caller["key"]] = names
        return edges

    @staticmethod
    def _collapse(result: dict, with_examples: bool) -> tuple[dict[str, LogPattern], dict[str, int], int, int]:
        """Folds exact-message buckets into fingerprint-keyed patterns."""
        patterns: dict[str, LogPattern] = {}
        aggregations = result.get("aggregations", {})
        totals = {
            level["key"]: level["doc_count"]
            for level in aggregations.get("levels", {}).get("buckets", [])
        }
        unparsed = aggregations.get("unparsed", {}).get("doc_count", 0)
        total_documents = result.get("hits", {}).get("total", {}).get("value", 0)

        for service_bucket in (
            aggregations.get("parsed", {}).get("by_service", {}).get("buckets", [])
        ):
            service = service_bucket["key"]
            for message_bucket in service_bucket.get("by_message", {}).get("buckets", []):
                message = message_bucket["key"]
                template = fingerprint(message)
                key = pattern_id(service, template)
                levels = message_bucket.get("levels", {}).get("buckets", [])
                level = levels[0]["key"] if levels else "UNKNOWN"

                first_raw = message_bucket.get("first", {}).get("value_as_string")
                last_raw = message_bucket.get("last", {}).get("value_as_string")
                first = datetime.fromisoformat(first_raw.replace("Z", "+00:00")) if first_raw else None
                last = datetime.fromisoformat(last_raw.replace("Z", "+00:00")) if last_raw else None

                existing = patterns.get(key)
                if existing:
                    existing.count += message_bucket["doc_count"]
                    if first and (existing.first_seen is None or first < existing.first_seen):
                        existing.first_seen = first
                    if last and (existing.last_seen is None or last > existing.last_seen):
                        existing.last_seen = last
                    continue

                example = message
                if with_examples:
                    hits = (message_bucket.get("example", {})
                            .get("hits", {}).get("hits", []))
                    if hits:
                        example = _dig(hits[0].get("_source", {}), "log.message") or message

                patterns[key] = LogPattern(
                    id=key, template=template, example=example, level=level,
                    service=service, count=message_bucket["doc_count"],
                    first_seen=first, last_seen=last,
                )
        return patterns, totals, unparsed, total_documents

    # -- samples -----------------------------------------------------------
    async def samples(self, plan: InvestigationPlan, window: TimeWindow, size: int) -> list[LogSample]:
        """A few raw error lines from the start of the incident.

        Sorted ascending from the incident start, because the useful ones are the
        *first* failures, not the most recent repetitions of them.
        """
        body = {
            "size": size,
            "_source": {"includes": _SAMPLE_FIELDS},
            "query": {"bool": {
                "filter": [*self.base_filters(plan, window),
                           {"terms": {"log.level": list(ERROR_LEVELS)}}],
            }},
            "sort": [{"@timestamp": {"order": "asc"}}],
        }
        result = await self._client.search(self._index, body)
        samples = []
        for hit in result.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            raw_ts = _dig(source, "@timestamp")
            if not raw_ts:
                continue
            samples.append(LogSample(
                id=f"log:{hit.get('_id', '')[:10]}",
                timestamp=ensure_utc(datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))),
                level=_dig(source, "log.level") or "UNKNOWN",
                service=_dig(source, "service.name"),
                pod=_dig(source, "kubernetes.pod.name"),
                message=(_dig(source, "log.message") or "")[:500],
                http_status=_dig(source, "http.status_code"),
                error_type=_dig(source, "error.type"),
                trace_id=_dig(source, "trace.id"),
            ))
        return samples

    # -- collect -----------------------------------------------------------
    async def collect(self, plan: InvestigationPlan, incident: TimeWindow,
                      baseline: TimeWindow | None) -> LogEvidence:
        evidence = LogEvidence()
        try:
            incident_result = await self._pattern_query(plan, incident, with_examples=True)
            patterns, totals, unparsed, total = self._collapse(incident_result, with_examples=True)

            if baseline is not None:
                baseline_result = await self._pattern_query(plan, baseline, with_examples=False)
                baseline_patterns, baseline_totals, _, baseline_total = self._collapse(
                    baseline_result, with_examples=False
                )
                for key, pattern in patterns.items():
                    matched = baseline_patterns.get(key)
                    pattern.baseline_count = matched.count if matched else 0
                evidence.baseline_totals_by_level = baseline_totals
                evidence.baseline_documents = baseline_total

            ranked = sorted(
                patterns.values(),
                # New error patterns first, then error severity, then volume.
                # Frequency alone would bury the one line that explains everything
                # under a thousand routine successes.
                key=lambda p: (
                    0 if (p.is_new and p.level in ERROR_LEVELS) else
                    1 if p.level in ERROR_LEVELS else
                    2 if p.level == "WARN" else 3,
                    -p.count,
                ),
            )[: settings.max_log_patterns]

            evidence.patterns = ranked
            evidence.totals_by_level = totals
            evidence.total_documents = total
            evidence.unparsed_documents = unparsed
            evidence.dependency_edges = self._dependency_edges(incident_result)
            evidence.histogram = await self.histogram(plan, incident, interval="60s")
            evidence.samples = await self.samples(
                plan, incident, size=settings.max_log_patterns
            )
        except OpenSearchError as exc:
            logger.warning("Log collection failed: %s", exc)
            evidence.status = "unavailable"
            evidence.reason = str(exc)
        return evidence
