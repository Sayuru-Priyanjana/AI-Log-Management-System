from __future__ import annotations

import logging

from app.config import settings
from app.models.analysis import InvestigationResult
from app.sources.opensearch import OpenSearchClient, OpenSearchError

logger = logging.getLogger(__name__)


class InvestigationStore:
    """Persists every run.

    This is what turns tuning from guesswork into measurement: with the plan,
    the signals, the candidates and the final answer all stored, a change can be
    shown to have helped or not. It is also the audit trail for any conclusion
    the system produced.
    """

    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client
        self._index = settings.opensearch_investigation_index

    async def save(self, result: InvestigationResult) -> bool:
        if not settings.persist_investigations:
            return False
        try:
            await self._client.index_document(
                self._index, result.model_dump(mode="json"), doc_id=result.id
            )
            return True
        except OpenSearchError as exc:
            # Losing the record must never lose the answer the user is waiting for.
            logger.warning("Could not persist investigation %s: %s", result.id, exc)
            return False

    async def get(self, investigation_id: str) -> dict | None:
        return await self._client.get_document(self._index, investigation_id)

    async def recent(self, limit: int = 20, system_id: str | None = None) -> list[dict]:
        query: dict = {"match_all": {}}
        if system_id:
            query = {"term": {"plan.system_id": system_id}}
        try:
            result = await self._client.search(self._index, {
                "size": limit,
                "query": query,
                "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}],
                "_source": {"includes": [
                    "id", "created_at", "question", "plan.system_id", "plan.environment",
                    "plan.service", "analysis.incident_detected", "analysis.severity",
                    "analysis.category", "analysis.confidence", "analysis.cause_summary",
                    "analysis.agrees_with_engine", "timings_ms",
                ]},
            })
        except OpenSearchError as exc:
            logger.warning("Could not list investigations: %s", exc)
            return []
        return [hit.get("_source", {}) for hit in result.get("hits", {}).get("hits", [])]
