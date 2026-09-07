"""
Recent chats lists conversations, and reopening one brings back every turn.

Every run used to be stored as an unrelated document. A seven-question
conversation therefore came back as seven separate entries in the sidebar, and
opening any of them replayed that turn alone — which is exactly what "yesterday
I had a whole conversation, today only the last one is left" looks like from the
outside. The thread itself lived only in the browser tab that created it.
"""
from __future__ import annotations

import pytest

from app.store.investigations import InvestigationStore


class FakeClient:
    """Returns a fixed page of hits, newest first, as OpenSearch would."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs
        self.last_body: dict | None = None

    async def search(self, index: str, body: dict) -> dict:
        self.last_body = body
        docs = self.docs
        ids = (body.get("query") or {}).get("ids")
        if ids:
            wanted = set(ids["values"])
            docs = [d for d in docs if d["id"] in wanted]
            docs = sorted(docs, key=lambda d: d["created_at"])
        return {"hits": {"hits": [{"_source": d} for d in docs]}}


def turn(doc_id, created_at, question, *, thread_id=None, history=(), system="shopdemo"):
    plan = {"system_id": system, "chat_history": [
        {"role": role, "content": content} for role, content in history
    ]}
    doc = {"id": doc_id, "created_at": created_at, "question": question,
           "plan": plan, "analysis": {}}
    if thread_id:
        doc["thread_id"] = thread_id
    return doc


@pytest.mark.asyncio
async def test_turns_of_one_conversation_collapse_into_a_single_entry():
    store = InvestigationStore(FakeClient([
        turn("inv-3", "2026-09-06T21:42", "and the third?", thread_id="thr-a"),
        turn("inv-2", "2026-09-06T21:41", "and then?", thread_id="thr-a"),
        turn("inv-1", "2026-09-06T21:40", "what broke?", thread_id="thr-a"),
        turn("inv-x", "2026-09-06T21:00", "unrelated question", thread_id="thr-b"),
    ]))

    threads = await store.recent(limit=10)

    assert len(threads) == 2, "three turns of one conversation are one entry"
    first = threads[0]
    assert first["turn_count"] == 3
    # Named by the question that opened it, not by the latest follow-up: the
    # sidebar is a list of conversations and "and the third?" names nothing.
    assert first["question"] == "what broke?"
    # Ordered oldest first, because that is the order they are replayed in.
    assert first["ids"] == ["inv-1", "inv-2", "inv-3"]
    # The most recent activity is what sorts the list.
    assert first["created_at"] == "2026-09-06T21:42"


@pytest.mark.asyncio
async def test_runs_stored_before_thread_ids_existed_are_still_grouped():
    """The recovery path for everything already in the index.

    A follow-up carries the whole exchange in `plan.chat_history`, so every turn
    of one conversation shares the same first user message and the run that
    opened it has no history at all.
    """
    store = InvestigationStore(FakeClient([
        turn("inv-c", "2026-09-06T21:42", "i mean the last 20 error logs",
             history=[("user", "filter me last 20 errors"), ("assistant", "here"),
                      ("user", "i need error logs not info"), ("assistant", "ok")]),
        turn("inv-b", "2026-09-06T21:41", "i need error logs not info",
             history=[("user", "filter me last 20 errors"), ("assistant", "here")]),
        turn("inv-a", "2026-09-06T21:40", "filter me last 20 errors"),
        turn("inv-solo", "2026-09-06T20:00", "something else entirely"),
    ]))

    threads = await store.recent(limit=10)

    assert len(threads) == 2
    conversation = threads[0]
    assert conversation["turn_count"] == 3
    assert conversation["ids"] == ["inv-a", "inv-b", "inv-c"]
    assert conversation["question"] == "filter me last 20 errors"
    assert threads[1]["turn_count"] == 1


@pytest.mark.asyncio
async def test_a_thread_id_wins_over_the_legacy_grouping():
    """Two conversations that happen to open with the same words stay apart once
    they carry real ids."""
    store = InvestigationStore(FakeClient([
        turn("inv-2", "2026-09-06T22:00", "what broke?", thread_id="thr-b"),
        turn("inv-1", "2026-09-06T21:00", "what broke?", thread_id="thr-a"),
    ]))

    threads = await store.recent(limit=10)
    assert len(threads) == 2
    assert {t["turn_count"] for t in threads} == {1}


@pytest.mark.asyncio
async def test_a_conversation_is_reopened_oldest_turn_first():
    client = FakeClient([
        turn("inv-1", "2026-09-06T21:40", "what broke?", thread_id="thr-a"),
        turn("inv-2", "2026-09-06T21:41", "and then?", thread_id="thr-a"),
    ])
    store = InvestigationStore(client)

    turns = await store.by_ids(["inv-2", "inv-1"])

    assert [t["id"] for t in turns] == ["inv-1", "inv-2"]
    # One query, not one per turn: reopening a long thread should not be a
    # round trip per question.
    assert client.last_body["query"]["ids"]["values"] == ["inv-2", "inv-1"]


@pytest.mark.asyncio
async def test_asking_for_no_turns_does_not_query_at_all():
    client = FakeClient([])
    assert await InvestigationStore(client).by_ids([]) == []
    assert client.last_body is None


@pytest.mark.asyncio
async def test_a_common_opening_question_does_not_merge_unrelated_conversations():
    """Taken from the live index, where this went wrong.

    "Something is wrong. What is the root cause?" is the question the alert
    button and most of the examples suggest, so it opens many unrelated
    investigations. Grouping legacy runs by their opening question alone folded
    thirty-four of them, spanning two days, into a single 34-turn
    "conversation". A run that carries no history opened its own conversation,
    whatever it was asked.
    """
    store = InvestigationStore(FakeClient([
        # A genuine three-question conversation, newest first...
        turn("inv-c", "2026-09-06T21:37", "and the pods?",
             history=[("user", "Something is wrong. What is the root cause?"), ("assistant", "a"),
                      ("user", "which service?"), ("assistant", "b")]),
        turn("inv-b", "2026-09-06T21:33", "which service?",
             history=[("user", "Something is wrong. What is the root cause?"), ("assistant", "a")]),
        turn("inv-a", "2026-09-06T21:32", "Something is wrong. What is the root cause?"),
        # ...and two later one-off runs asked with exactly the same words.
        turn("inv-solo2", "2026-09-06T20:42", "Something is wrong. What is the root cause?"),
        turn("inv-solo1", "2026-08-24T08:57", "Something is wrong. What is the root cause?"),
    ]))

    threads = await store.recent(limit=10)

    assert len(threads) == 3, "one conversation and two standalone questions"
    counts = sorted(t["turn_count"] for t in threads)
    assert counts == [1, 1, 3]
    conversation = next(t for t in threads if t["turn_count"] == 3)
    assert conversation["ids"] == ["inv-a", "inv-b", "inv-c"]


@pytest.mark.asyncio
async def test_a_follow_up_attaches_to_the_most_recent_matching_conversation():
    """Two conversations opened with identical wording, interleaved in the index.

    The later follow-up belongs to the later conversation: the most recent
    thread that shares its opening question and is still shorter than it is.
    """
    store = InvestigationStore(FakeClient([
        turn("inv-2b", "2026-09-06T22:10", "and then?",
             history=[("user", "what broke?"), ("assistant", "x")]),
        turn("inv-2a", "2026-09-06T22:00", "what broke?"),
        turn("inv-1b", "2026-09-06T21:10", "and then?",
             history=[("user", "what broke?"), ("assistant", "x")]),
        turn("inv-1a", "2026-09-06T21:00", "what broke?"),
    ]))

    threads = await store.recent(limit=10)

    assert len(threads) == 2
    assert [t["ids"] for t in threads] == [["inv-2a", "inv-2b"], ["inv-1a", "inv-1b"]]
