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

    async def delete(self, investigation_id: str) -> bool:
        return await self._client.delete_document(self._index, investigation_id)

    async def by_ids(self, ids: list[str]) -> list[dict]:
        """Every turn of a conversation, oldest first.

        Fetched in one query rather than one request per turn: reopening a
        seven-question thread should not be seven round trips through the
        gateway, and the order the turns are replayed in is the order they were
        asked.
        """
        if not ids:
            return []
        try:
            result = await self._client.search(self._index, {
                "size": min(len(ids), 100),
                "query": {"ids": {"values": ids[:100]}},
                "sort": [{"created_at": {"order": "asc", "unmapped_type": "date"}}],
            })
        except OpenSearchError as exc:
            logger.warning("Could not load investigations %s: %s", ids[:3], exc)
            return []
        return [hit.get("_source", {}) for hit in result.get("hits", {}).get("hits", [])]

    @staticmethod
    def _opening(doc: dict) -> tuple[str, int]:
        """The question a stored run's conversation began with, and how far into
        that conversation this run sits.

        A follow-up carries the whole exchange so far in `plan.chat_history`, so
        the first user message names the conversation and the length of the
        history is the turn's position in it: 0 for the run that opened it, then
        2, 4, 6 as each question and answer is appended.
        """
        plan = doc.get("plan") or {}
        history = plan.get("chat_history") or []
        opening = next((m.get("content") for m in history if m.get("role") == "user"), None)
        opening = (opening or doc.get("question") or "").strip()[:200]
        return f"{plan.get('system_id', '')}|{opening}", len(history)

    def _group(self, docs: list[dict]) -> list[dict]:
        """Folds stored runs into the conversations they belong to.

        `thread_id` settles it for anything written since conversations were
        given one. Everything before that is reconstructed, and the naive
        version of that — group by the opening question — was wrong on real
        data: "Something is wrong. What is the root cause?" is the question the
        alert button and half the examples suggest, so thirty-four unrelated
        investigations spanning two days collapsed into one 34-turn
        "conversation".

        The history length is what distinguishes them. A run with no history
        *opened* a conversation, whatever it was asked; a follow-up attaches to
        the most recent conversation that shares its opening question and is
        still shorter than it is. Two people asking the same opening question an
        hour apart therefore get two threads, which is what happened.
        """
        threads: list[dict] = []
        by_id: dict[str, dict] = {}

        for doc in docs:                                    # oldest first
            plan = doc.get("plan") or {}
            stored_thread = (doc.get("thread_id") or "").strip()

            thread = None
            if stored_thread:
                thread = by_id.get(stored_thread)
            else:
                key, depth = self._opening(doc)
                if depth > 0:
                    thread = next(
                        (t for t in reversed(threads)
                         if t["_key"] == key and t["_legacy"] and t["_depth"] < depth),
                        None,
                    )

            if thread is None:
                key, depth = self._opening(doc)
                thread = {
                    "id": stored_thread or doc.get("id"),
                    "thread_id": stored_thread or doc.get("id"),
                    "ids": [],
                    "question": doc.get("question"),
                    "system_id": plan.get("system_id"),
                    "environment": plan.get("environment"),
                    "service": plan.get("service"),
                    "plan": {"system_id": plan.get("system_id")},
                    "started_at": doc.get("created_at"),
                    "turn_count": 0,
                    "_key": key,
                    "_legacy": not stored_thread,
                    "_depth": depth,
                }
                threads.append(thread)
                if stored_thread:
                    by_id[stored_thread] = thread

            thread["ids"].append(doc.get("id"))
            thread["turn_count"] += 1
            thread["_depth"] = max(thread["_depth"], self._opening(doc)[1])
            # The newest turn supplies what the row reports about the state of
            # the conversation; the oldest supplies its name.
            thread["created_at"] = doc.get("created_at")
            thread["last_question"] = doc.get("question")
            thread["analysis"] = doc.get("analysis") or {}

        for thread in threads:
            thread.pop("_key", None)
            thread.pop("_legacy", None)
            thread.pop("_depth", None)
        threads.reverse()                                   # newest conversation first
        return threads

    async def recent(self, limit: int = 20, system_id: str | None = None) -> list[dict]:
        """Recent *conversations*, not recent turns.

        The sidebar is called "Recent chats" and used to list every follow-up as
        its own entry, so a single seven-question investigation filled the panel
        with seven near-identical rows and hid the conversations before it.

        Grouping happens here rather than in an OpenSearch aggregation because
        reconstructing a pre-`thread_id` conversation depends on the order and
        depth of the runs, which no terms bucket expresses. A wider page is read
        than the number of conversations asked for, since several rows collapse
        into one.
        """
        query: dict = {"match_all": {}}
        if system_id:
            query = {"term": {"plan.system_id": system_id}}
        try:
            result = await self._client.search(self._index, {
                "size": min(limit * 6, 400),
                "query": query,
                "sort": [{"created_at": {"order": "desc", "unmapped_type": "date"}}],
                "_source": {"includes": [
                    "id", "thread_id", "created_at", "question",
                    "plan.system_id", "plan.environment", "plan.service",
                    "plan.chat_history",
                    "analysis.incident_detected", "analysis.severity",
                    "analysis.category", "analysis.confidence", "analysis.cause_summary",
                    "analysis.agrees_with_engine", "timings_ms",
                ]},
            })
        except OpenSearchError as exc:
            logger.warning("Could not list investigations: %s", exc)
            return []

        docs = [hit.get("_source", {}) for hit in result.get("hits", {}).get("hits", [])]
        docs.reverse()                                      # oldest first, to walk forward
        return self._group(docs)[:limit]
