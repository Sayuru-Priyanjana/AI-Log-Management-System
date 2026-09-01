from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


@dataclass
class Collapsed:
    """What one pattern query yielded.

    A named structure rather than a tuple: the caller needs six things from this
    now, and positional unpacking of six was already a place to introduce a
    silent bug by transposing two of them.
    """

    patterns: dict[str, LogPattern]
    totals_by_level: dict[str, int]
    unparsed: int
    total_documents: int
    # Exact per-service, per-level counts. Not derived from `patterns`, which is
    # capped — this is what an error *rate* must be measured from.
    by_service_level: dict[str, dict[str, int]] = field(default_factory=dict)
    # Services whose message aggregation was truncated, so an absent pattern
    # bucket does not establish that the pattern was absent.
    truncated_services: set[str] = field(default_factory=set)


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
                # Exact per-service, per-level counts over the whole window.
                # Deliberately separate from the pattern tree below: that one is
                # truncated to the top few messages per service for display, and
                # summing it to get an error *rate* undercounts every service
                # with more distinct templates than the cut allows. A service
                # emitting 40 different errors had most of them dropped before
                # the rate was computed, so ERROR_RATE_SPIKE never fired.
                "service_levels": {
                    "terms": {"field": "service.name", "size": 100},
                    "aggs": {"levels": {"terms": {"field": "log.level", "size": 10}}},
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
    def _collapse(result: dict, with_examples: bool) -> Collapsed:
        """Folds exact-message buckets into fingerprint-keyed patterns."""
        patterns: dict[str, LogPattern] = {}
        aggregations = result.get("aggregations", {})
        totals = {
            level["key"]: level["doc_count"]
            for level in aggregations.get("levels", {}).get("buckets", [])
        }
        unparsed = aggregations.get("unparsed", {}).get("doc_count", 0)
        total_documents = result.get("hits", {}).get("total", {}).get("value", 0)

        # Exact counts, untouched by the pattern truncation below.
        by_service_level: dict[str, dict[str, int]] = {}
        for bucket in aggregations.get("service_levels", {}).get("buckets", []):
            by_service_level[bucket["key"]] = {
                level["key"]: level["doc_count"]
                for level in bucket.get("levels", {}).get("buckets", [])
            }

        # Services whose message aggregation hit its cap. For those, "this
        # pattern has no baseline bucket" does not mean "it never happened" —
        # it may simply have ranked below the cut.
        truncated: set[str] = set()

        for service_bucket in (
            aggregations.get("parsed", {}).get("by_service", {}).get("buckets", [])
        ):
            service = service_bucket["key"]
            if service_bucket.get("by_message", {}).get("sum_other_doc_count", 0) > 0:
                truncated.add(service)
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
        return Collapsed(patterns=patterns, totals_by_level=totals,
                         unparsed=unparsed, total_documents=total_documents,
                         by_service_level=by_service_level,
                         truncated_services=truncated)

    # -- retrieval ---------------------------------------------------------
    # The maximum a single call may return. The agent chooses `limit`, and an
    # unbounded one would put an arbitrary number of raw lines into the prompt.
    MAX_FETCH = 200

    @staticmethod
    def _to_sample(hit: dict) -> LogSample | None:
        source = hit.get("_source", {})
        raw_ts = _dig(source, "@timestamp")
        if not raw_ts:
            return None
        return LogSample(
            # Minted here, in Python, from the document's own id. Every line the
            # model is asked to cite is given an identifier it cannot invent.
            id=f"log:{hit.get('_id', '')[:10]}",
            timestamp=ensure_utc(datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))),
            level=_dig(source, "log.level") or "UNKNOWN",
            service=_dig(source, "service.name"),
            pod=_dig(source, "kubernetes.pod.name"),
            message=(_dig(source, "log.message") or "")[:500],
            http_status=_dig(source, "http.status_code"),
            error_type=_dig(source, "error.type"),
            trace_id=_dig(source, "trace.id"),
        )

    async def fetch(self, plan: InvestigationPlan, window: TimeWindow, *,
                    levels: list[str] | None = None,
                    service: str | None = None,
                    contains: str | None = None,
                    order: str = "newest",
                    limit: int = 20) -> list[LogSample]:
        """Raw log lines, on whatever terms the caller asks for.

        The generalisation of `samples`. That method could only ever return the
        *first* ERROR-level lines of the incident window, which is right for
        incident evidence and useless for anything else: "show me the last 20
        logs" and "what was this service saying just before it broke" both need
        a different level filter, a different sort, or both.

        Every argument here is a *parameter*, never a query fragment. The caller
        — including the reasoning loop — chooses what to look for; the query
        itself is assembled here, so a filter can be wrong but never malformed,
        and no field or index name is ever taken from model output.
        """
        filters = list(self.base_filters(plan, window))
        if levels:
            filters.append({"terms": {"log.level": [str(l).upper() for l in levels]}})
        if service:
            filters.append({"term": {"service.name": service}})
        if contains:
            # match_phrase against the analysed field, so the caller can pass
            # ordinary words rather than having to know about `.keyword`.
            filters.append({"match_phrase": {"log.message": contains}})

        try:
            size = max(1, min(int(limit), self.MAX_FETCH))
        except (TypeError, ValueError):
            size = 20

        body = {
            "size": size,
            "_source": {"includes": _SAMPLE_FIELDS},
            "query": {"bool": {"filter": filters}},
            "sort": [{"@timestamp": {"order": "asc" if order == "oldest" else "desc"}}],
        }
        result = await self._client.search(self._index, body)
        found = [s for s in (self._to_sample(hit)
                             for hit in result.get("hits", {}).get("hits", []))
                 if s is not None]
        # A "newest first" query is the right way to *find* the last N lines and
        # the wrong way to *read* them: causal order is chronological either way.
        return sorted(found, key=lambda s: s.timestamp)

    async def samples(self, plan: InvestigationPlan, window: TimeWindow, size: int) -> list[LogSample]:
        """A few raw error lines from the start of the incident.

        Sorted ascending from the incident start, because the useful ones are the
        *first* failures, not the most recent repetitions of them.
        """
        return await self.fetch(plan, window, levels=list(ERROR_LEVELS),
                                order="oldest", limit=size)

    # How many unverified "new error" candidates to settle with a targeted
    # lookup. Only ERROR-level patterns matter here, and a window with more
    # than this many distinct new error templates has bigger problems than the
    # precision of one signal.
    MAX_VERIFY = 30

    async def _verify_new_patterns(self, plan: InvestigationPlan, baseline: TimeWindow,
                                   patterns: dict[str, LogPattern]) -> None:
        """Settles, exactly, whether a candidate new error really is new.

        The baseline's message aggregation is capped, so a pattern below the cut
        comes back with no bucket and looks brand new. Rather than widen the cap
        and hope — the next busy service overflows whatever number is chosen —
        the small set of candidates is checked directly against the baseline
        window with a filter on those exact messages.

        One extra query, only when the aggregation was truncated, and only for
        ERROR-level patterns that are about to become a signal.
        """
        candidates = [
            p for p in patterns.values()
            if not p.baseline_verified and p.baseline_count == 0
            and p.level in ERROR_LEVELS
        ][: self.MAX_VERIFY]
        if not candidates:
            return

        by_example = {p.example: p for p in candidates if p.example}
        if not by_example:
            return

        body = {
            "size": 0,
            "query": {"bool": {"filter": [
                *self.base_filters(plan, baseline),
                {"terms": {"log.message.keyword": list(by_example)}},
            ]}},
            "aggs": {"messages": {"terms": {"field": "log.message.keyword",
                                            "size": len(by_example)}}},
        }
        try:
            result = await self._client.search(self._index, body)
        except OpenSearchError as exc:
            # The doubt stands. Leaving these unverified suppresses the signal,
            # which is the safe direction: a missed new-error pattern is a gap,
            # an invented one is a wrong answer wearing high severity.
            logger.warning("Could not verify new-pattern candidates: %s", exc)
            return

        seen = {
            bucket["key"]: bucket["doc_count"]
            for bucket in result.get("aggregations", {})
            .get("messages", {}).get("buckets", [])
        }
        for message, pattern in by_example.items():
            pattern.baseline_count = seen.get(message, 0)
            # Checked either way now: found means not new, absent from a query
            # that asked for it specifically means genuinely absent.
            pattern.baseline_verified = True

    # -- collect -----------------------------------------------------------
    async def collect(self, plan: InvestigationPlan, incident: TimeWindow,
                      baseline: TimeWindow | None) -> LogEvidence:
        evidence = LogEvidence()
        try:
            incident_result = await self._pattern_query(plan, incident, with_examples=True)
            collapsed = self._collapse(incident_result, with_examples=True)
            patterns = collapsed.patterns
            totals, unparsed = collapsed.totals_by_level, collapsed.unparsed
            total = collapsed.total_documents
            evidence.by_service_level = collapsed.by_service_level

            if baseline is not None:
                baseline_result = await self._pattern_query(plan, baseline, with_examples=False)
                base = self._collapse(baseline_result, with_examples=False)
                for key, pattern in patterns.items():
                    matched = base.patterns.get(key)
                    pattern.baseline_count = matched.count if matched else 0
                    # Absent from a truncated aggregation is not absent from the
                    # window. Left unmarked, every such pattern reads as brand new
                    # and fires a HIGH-severity NEW_ERROR_PATTERN on a line that
                    # may have been happening all day.
                    pattern.baseline_verified = bool(
                        matched or pattern.service not in base.truncated_services
                    )
                evidence.baseline_totals_by_level = base.totals_by_level
                evidence.baseline_documents = base.total_documents
                evidence.baseline_by_service_level = base.by_service_level

                await self._verify_new_patterns(plan, baseline, patterns)

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
