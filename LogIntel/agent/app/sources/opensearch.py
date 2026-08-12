from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class OpenSearchError(RuntimeError):
    pass


# Explicit mappings rather than dynamic ones. Dynamic mapping makes every string
# a text field with a `.keyword` sub-field, which doubles the index and leaves
# query authors guessing which form to use. Here `system.id` is a keyword and is
# queried as `system.id`, full stop.
_COMMON_IDENTITY = {
    # Fluent Bit writes nanosecond precision; the default `date` format tops out
    # at milliseconds and would reject those documents outright.
    "@timestamp": {"type": "date",
                   "format": "strict_date_optional_time_nanos||strict_date_optional_time||epoch_millis"},
    "environment": {"type": "keyword"},
    "system": {"properties": {"id": {"type": "keyword"}, "name": {"type": "keyword"}}},
    "service": {"properties": {
        "name": {"type": "keyword"},
        "version": {"type": "keyword"},
        "tier": {"type": "keyword"},
    }},
    "kubernetes": {"properties": {
        "cluster": {"type": "keyword"},
        "namespace": {"type": "keyword"},
        "pod": {"properties": {"name": {"type": "keyword"}, "uid": {"type": "keyword"}}},
        "container": {"properties": {"name": {"type": "keyword"}, "image": {"type": "keyword"}}},
        "node": {"properties": {"name": {"type": "keyword"}}},
    }},
    "source": {"properties": {"type": {"type": "keyword"}, "collector": {"type": "keyword"}}},
}

LOG_TEMPLATE = {
    "index_patterns": ["logintel-logs-*"],
    "priority": 200,
    "template": {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,   # single node; avoids a permanently yellow cluster
            "refresh_interval": "5s",
        },
        "mappings": {
            "properties": {
                **_COMMON_IDENTITY,
                "log": {"properties": {
                    "level": {"type": "keyword"},
                    # text for search, keyword for the pattern aggregation
                    "message": {"type": "text", "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 2048}
                    }},
                }},
                "event": {"properties": {
                    "category": {"type": "keyword"},
                    "action": {"type": "keyword"},
                    "outcome": {"type": "keyword"},
                }},
                "http": {"properties": {
                    "method": {"type": "keyword"},
                    "route": {"type": "keyword"},
                    "status_code": {"type": "integer"},
                    "response_time_ms": {"type": "float"},
                }},
                "error": {"properties": {
                    "type": {"type": "keyword"},
                    "message": {"type": "text"},
                    "stack_trace": {"type": "text", "index": False},
                }},
                "dependency": {"properties": {
                    "name": {"type": "keyword"},
                    "outcome": {"type": "keyword"},
                    "duration_ms": {"type": "float"},
                }},
                "trace": {"properties": {"id": {"type": "keyword"}}},
                "request": {"properties": {"id": {"type": "keyword"}}},
                "parse": {"properties": {"failed": {"type": "boolean"}}},
            }
        },
    },
}

EVENT_TEMPLATE = {
    "index_patterns": ["logintel-events-*"],
    "priority": 200,
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "5s"},
        "mappings": {
            "properties": {
                **_COMMON_IDENTITY,
                "event": {"properties": {
                    "id": {"type": "keyword"},
                    "kind": {"type": "keyword"},
                    "type": {"type": "keyword"},
                    "reason": {"type": "keyword"},
                    "severity": {"type": "keyword"},
                    "message": {"type": "text", "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 2048}
                    }},
                    "count": {"type": "integer"},
                    "first_timestamp": {"type": "date"},
                    "last_timestamp": {"type": "date"},
                    "reporting_component": {"type": "keyword"},
                }},
                "involved_object": {"properties": {
                    "kind": {"type": "keyword"},
                    "name": {"type": "keyword"},
                    "uid": {"type": "keyword"},
                    "field_path": {"type": "keyword"},
                }},
            }
        },
    },
}

INVESTIGATION_TEMPLATE = {
    "index_patterns": ["logintel-investigations*"],
    "priority": 200,
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "id": {"type": "keyword"},
                "created_at": {"type": "date"},
                "question": {"type": "text"},
                "plan": {"properties": {
                    "system_id": {"type": "keyword"},
                    "environment": {"type": "keyword"},
                    "service": {"type": "keyword"},
                    "intent": {"type": "keyword"},
                }},
                "analysis": {"properties": {
                    "incident_detected": {"type": "boolean"},
                    "severity": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    "confidence": {"type": "float"},
                    "chosen_candidate_id": {"type": "keyword"},
                    "agrees_with_engine": {"type": "boolean"},
                }},
            },
            # Signals and candidates are kept for the audit trail, not queried
            # field by field. `dynamic: false` stores them in _source without
            # indexing, so a varying `detail` object cannot explode the mapping.
            "dynamic": False,
        },
    },
}


class OpenSearchClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.opensearch_url).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=settings.opensearch_auth,
            timeout=settings.opensearch_timeout,
            verify=settings.opensearch_verify_ssl,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = await self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise OpenSearchError(f"OpenSearch unreachable at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise OpenSearchError(
                f"OpenSearch {method} {path} -> {response.status_code}: {response.text[:400]}"
            )
        return response.json() if response.content else {}

    async def ping(self) -> dict:
        return await self._request("GET", "/")

    async def search(self, index: str, body: dict) -> dict:
        # allow_no_indices keeps a query against a not-yet-created daily index
        # from being an error; an empty result is the correct answer there.
        return await self._request(
            "POST", f"/{index}/_search?ignore_unavailable=true&allow_no_indices=true", body
        )

    async def count(self, index: str, query: dict | None = None) -> int:
        body = {"query": query} if query else None
        result = await self._request(
            "POST", f"/{index}/_count?ignore_unavailable=true&allow_no_indices=true", body
        )
        return int(result.get("count", 0))

    async def index_document(self, index: str, document: dict, doc_id: str | None = None) -> dict:
        path = f"/{index}/_doc/{doc_id}" if doc_id else f"/{index}/_doc"
        return await self._request("PUT" if doc_id else "POST", path, document)

    async def get_document(self, index: str, doc_id: str) -> dict | None:
        try:
            result = await self._request("GET", f"/{index}/_doc/{doc_id}")
        except OpenSearchError:
            return None
        return result.get("_source") if result.get("found") else None

    async def ensure_templates(self) -> list[str]:
        applied = []
        for name, template in (
            ("logintel-logs", LOG_TEMPLATE),
            ("logintel-events", EVENT_TEMPLATE),
            ("logintel-investigations", INVESTIGATION_TEMPLATE),
        ):
            await self._request("PUT", f"/_index_template/{name}", template)
            applied.append(name)
        logger.info("Applied index templates: %s", ", ".join(applied))
        return applied

    async def check_mapping_conflicts(self) -> list[str]:
        """Detects indices created before the templates existed.

        Dynamic mapping would have made `system.id` a text field, and every term
        filter in the pipeline would then silently match nothing. That failure is
        invisible at query time, so it is checked for explicitly.
        """
        problems: list[str] = []
        for index, field in (
            (settings.opensearch_log_index, ("system", "id")),
            (settings.opensearch_event_index, ("event", "reason")),
        ):
            try:
                mappings = await self._request("GET", f"/{index}/_mapping?ignore_unavailable=true")
            except OpenSearchError:
                continue
            for concrete, body in mappings.items():
                node: Any = body.get("mappings", {}).get("properties", {})
                for part in field[:-1]:
                    node = (node.get(part) or {}).get("properties", {})
                leaf = node.get(field[-1])
                if leaf and leaf.get("type") != "keyword":
                    problems.append(
                        f"index '{concrete}' has {'.'.join(field)} as "
                        f"'{leaf.get('type')}' instead of 'keyword' — it was created before the "
                        f"index template. Delete it and let it be recreated: "
                        f"curl -XDELETE {self.base_url}/{concrete}"
                    )
        return problems

    def describe(self) -> str:
        return self.base_url


def dump_query(body: dict) -> str:
    return json.dumps(body, separators=(",", ":"), default=str)
