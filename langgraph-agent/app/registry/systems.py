from __future__ import annotations

import logging
import time

from app.config import settings
from app.models.domain import ServiceDescriptor, SystemDescriptor
from app.sources.opensearch import OpenSearchClient, OpenSearchError

logger = logging.getLogger(__name__)


class SystemRegistry:
    """Discovers which systems, environments, namespaces and services exist.

    This is what closes the biggest accuracy hole in a naive design: without it
    the planner invents a service name, the resulting term filter matches nothing,
    and the investigation confidently reports that all is well. Here the planner
    may only choose from names that are known to exist.
    """

    def __init__(self, client: OpenSearchClient, ttl_seconds: int = 180) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._cache: dict[str, SystemDescriptor] = {}
        self._fetched_at: float = 0.0

    async def refresh(self, lookback: str | None = None) -> dict[str, SystemDescriptor]:
        lookback = lookback or f"now-{settings.system_active_lookback_hours}h"
        body = {
            "size": 0,
            "query": {"range": {"@timestamp": {"gte": lookback}}},
            "aggs": {
                "systems": {
                    "terms": {"field": "system.id", "size": 50},
                    "aggs": {
                        "name": {"terms": {"field": "system.name", "size": 1}},
                        "environments": {"terms": {"field": "environment", "size": 20}},
                        "namespaces": {"terms": {"field": "kubernetes.namespace", "size": 50}},
                        "services": {
                            "terms": {"field": "service.name", "size": 100},
                            "aggs": {
                                "namespaces": {"terms": {"field": "kubernetes.namespace", "size": 10}},
                                "tier": {"terms": {"field": "service.tier", "size": 1}},
                            },
                        },
                    },
                }
            },
        }

        result = await self._client.search(settings.opensearch_log_index, body)
        buckets = result.get("aggregations", {}).get("systems", {}).get("buckets", [])

        discovered: dict[str, SystemDescriptor] = {}
        for bucket in buckets:
            system_id = bucket["key"]
            names = bucket.get("name", {}).get("buckets", [])
            services = [
                ServiceDescriptor(
                    name=service["key"],
                    log_count=service["doc_count"],
                    namespaces=[n["key"] for n in service.get("namespaces", {}).get("buckets", [])],
                    tier=next(
                        (t["key"] for t in service.get("tier", {}).get("buckets", [])), None
                    ),
                )
                for service in bucket.get("services", {}).get("buckets", [])
            ]
            discovered[system_id] = SystemDescriptor(
                id=system_id,
                name=names[0]["key"] if names else system_id,
                environments=[e["key"] for e in bucket.get("environments", {}).get("buckets", [])],
                namespaces=[n["key"] for n in bucket.get("namespaces", {}).get("buckets", [])],
                services=sorted(services, key=lambda s: -s.log_count),
            )

        self._cache = discovered
        self._fetched_at = time.monotonic()
        logger.info(
            "Registry refreshed: %d system(s) — %s",
            len(discovered),
            ", ".join(f"{s.id}({len(s.services)} services)" for s in discovered.values()) or "none",
        )
        return discovered

    async def all(self) -> list[SystemDescriptor]:
        if not self._cache or (time.monotonic() - self._fetched_at) > self._ttl:
            try:
                await self.refresh()
            except OpenSearchError as exc:
                # Stale knowledge beats none; an investigation can still run.
                logger.warning("Registry refresh failed (%s); serving cache", exc)
                if not self._cache:
                    raise
        return sorted(self._cache.values(), key=lambda s: s.id)

    async def get(self, system_id: str) -> SystemDescriptor | None:
        await self.all()
        return self._cache.get(system_id)

    async def require(self, system_id: str) -> SystemDescriptor:
        system = await self.get(system_id)
        if system is None:
            known = ", ".join(sorted(self._cache)) or "none discovered"
            raise LookupError(
                f"unknown system '{system_id}'. Known systems: {known}. "
                f"A system appears here once it has shipped logs carrying system.id."
            )
        return system
