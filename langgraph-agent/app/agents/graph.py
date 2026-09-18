"""
The investigation as an explicit LangGraph state machine.

What was here before was a placeholder: a single node that returned the string
"I am the LangGraph Agent.", a checkpointer pointed at a SQLite file, and no
caller anywhere in the process. Selecting "LangGraph Agent" in the configuration
panel therefore ran exactly the same deterministic pipeline as the custom agent
— the two backends differed only in which container answered.

This replaces it with a graph that actually runs the investigation. The nodes
are the stages that were already there; making them nodes buys three things the
straight-line `for` loop could not give:

  * the routing is data, not control flow. `TOPOLOGY` below is the same
    structure the graph is compiled from and the same structure the UI draws, so
    the picture cannot drift from the machine.
  * the branches are recorded. Every conditional edge writes why it went the way
    it did into `decisions`, which is what turns "here is the answer" into "here
    is the path taken to reach it".
  * a stage that finds nothing can route around the rest instead of feeding an
    empty evidence bundle to a model that will confabulate over it.

Streaming is not done through `astream`. The reasoning node emits a dozen
thoughts, tool calls and observations that the UI shows as they happen, and a
node return value can only carry the last of them; events are pushed onto an
`asyncio.Queue` as they occur and drained by `InvestigationPipeline.run`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, TypedDict

# `InMemorySaver` is the name in langgraph-checkpoint >= 2.1; the version this
# image pins (2.0.9) calls the same class `MemorySaver`. Importing the new name
# only works on a machine that happens to have the newer package — which is how
# this shipped broken: the local venv had it, the built image did not.
try:  # pragma: no cover - one branch runs per installed version
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------- shape
# Drawn by the UI and compiled into the graph below, in that order of authority:
# `build_graph` reads the edges from here, so a node added to one is a node
# added to both. `kind` drives the shape drawn; `emits` names the stage event a
# node streams, which is how the UI lights a node up while it is running.
TOPOLOGY: dict[str, Any] = {
    "nodes": [
        {"id": "plan", "label": "Plan", "kind": "llm", "row": 0,
         "emits": "plan",
         "detail": "Classify the question into an intent, a service and a window. "
                   "One LLM call; every value it returns is checked against the registry."},
        {"id": "windows", "label": "Resolve window", "kind": "deterministic", "row": 1,
         "emits": "windows",
         "detail": "Sweep the whole period asked about for every elevated stretch, pick the one to analyse in depth, find a clean baseline for it, and measure the system's current status. Pure Python — no model involved."},
        {"id": "evidence", "label": "Collect evidence", "kind": "io", "row": 2,
         "emits": "evidence",
         "detail": "Logs, Kubernetes events and metrics, gathered concurrently."},
        {"id": "signals", "label": "Detect signals", "kind": "deterministic", "row": 3,
         "emits": "signals",
         "detail": "Measure departures from baseline. Runs before the model, so "
                   "nothing it says can change them."},
        {"id": "topology", "label": "Operational topology", "kind": "deterministic", "row": 4,
         "emits": "topology",
         "detail": "Observed service calls and pod placement, with time-ordered propagation leads."},
        {"id": "fingerprint", "label": "Incident fingerprint", "kind": "deterministic", "row": 5,
         "emits": "fingerprint",
         "detail": "Canonical signals, sequence, log templates, metrics and dependencies."},
        {"id": "historical_retrieval", "label": "Similar incidents", "kind": "io", "row": 6,
         "emits": "historical_retrieval",
         "detail": "Search completed investigations in the same system and environment."},
        {"id": "playbooks", "label": "Match playbooks", "kind": "deterministic", "row": 7,
         "emits": "playbooks",
         "detail": "Match published admin guidance to the selected service and measured signal types. Guidance is never treated as evidence."},
        {"id": "candidates", "label": "Rank candidates", "kind": "deterministic", "row": 7,
         "emits": "candidates",
         "detail": "Rule-generated explanations scored from current telemetry."},
        {"id": "hypothesis_testing", "label": "Test hypotheses", "kind": "deterministic", "row": 8,
         "emits": "hypothesis_testing",
         "detail": "Check current support, contradictions, timing and propagation; preserve unknowns."},
        {"id": "reason", "label": "Reasoning loop", "kind": "llm", "row": 9,
         "emits": "reasoning",
         "detail": "ReAct: think, call a tool, read the observation, repeat. "
                   "One LLM round trip per step."},
        {"id": "fallback", "label": "Rule answer", "kind": "fallback", "row": 9,
         "emits": None,
         "detail": "Reached only when the loop could not run or did not finish. "
                   "Reports the deterministic ranking, labelled as such."},
        {"id": "verify", "label": "Verify answer", "kind": "guard", "row": 10,
         "emits": "answer",
         "detail": "Label resolved and unresolved citations, test the proposed cause "
                   "against current evidence, and cap confidence."},
        {"id": "finish", "label": "Assemble result", "kind": "terminal", "row": 11,
         "emits": "result",
         "detail": "Fold evidence into a timeline and persist bounded thread memory."},
    ],
    "edges": [
        {"from": "__start__", "to": "plan"},
        {"from": "plan", "to": "windows"},
        {"from": "windows", "to": "evidence"},
        {"from": "evidence", "to": "signals", "when": "some evidence was collected",
         "conditional": True},
        {"from": "evidence", "to": "fallback", "when": "every source was unavailable",
         "conditional": True},
        {"from": "signals", "to": "topology"},
        {"from": "topology", "to": "fingerprint"},
        {"from": "fingerprint", "to": "historical_retrieval"},
        {"from": "historical_retrieval", "to": "playbooks"},
        {"from": "playbooks", "to": "candidates"},
        {"from": "candidates", "to": "hypothesis_testing"},
        {"from": "hypothesis_testing", "to": "reason"},
        {"from": "reason", "to": "verify", "when": "the loop produced an answer",
         "conditional": True},
        {"from": "reason", "to": "fallback", "when": "the loop failed or ran out of steps",
         "conditional": True},
        {"from": "fallback", "to": "verify"},
        {"from": "verify", "to": "finish"},
        {"from": "finish", "to": "__end__"},
    ],
}


def _append(existing: list | None, incoming: list | None) -> list:
    """The reducer behind `memory`: turns accumulate, they do not replace.

    Written out rather than using `operator.add` so a node returning `None`
    (which every node that does not touch memory does) cannot wipe the thread.
    """
    if not incoming:
        return (existing or [])[-20:]
    return ((existing or []) + list(incoming))[-20:]


class GraphState(TypedDict, total=False):
    """Everything a node may read or write.

    Deliberately holds the real domain objects rather than dicts: the nodes are
    the same code the sequential pipeline ran, and re-serialising an evidence
    bundle between every stage would be a new way for the two backends to
    disagree about what was measured.
    """

    investigation_id: str
    request: Any
    system: Any

    # The conversation, accumulated by the graph itself.
    #
    # Every other channel is overwritten on each turn; this one appends, and the
    # checkpointer restores it when the same `thread_id` comes back. That is
    # what makes a follow-up's memory a property of the agent rather than of
    # whichever browser tab happened to send the history along with it.
    memory: Annotated[list, _append]
    effective_history: list

    # Named apart from the nodes that produce them. LangGraph refuses a node
    # whose name is also a state key ("'plan' is already being used as a state
    # key"), and the node names are the ones that surface — in TOPOLOGY, in the
    # drawn graph, and in the `graph_path` stored with every run — so the state
    # keys are what get the longer names.
    investigation_plan: Any
    mode: Any
    resolved_windows: Any
    search_histogram: Any
    # What the system is doing now, measured over the configured recent window
    # whatever period the question asked about. Produced by the windows node
    # alongside the sweep, because both are questions about time rather than
    # about evidence and both are wanted before the model is asked anything.
    recent_status: Any
    evidence_bundle: Any
    detected_signals: Any
    operational_topology: Any
    incident_fingerprint: Any
    historical_matches: Any
    published_architecture: Any
    matched_playbooks: Any
    ranked_candidates: Any
    hypothesis_tests: Any
    causal_roles: Any

    raw_answer: dict
    exposed_ids: set
    table: Any
    steps_used: int
    degraded: str | None

    answer: Any
    result: Any

    errors: list
    timings_ms: dict
    visited: list
    decisions: list


def build_graph(nodes: "GraphNodes", checkpointer: Any = None) -> Any:
    """Compiles `TOPOLOGY` against a set of bound node callables.

    `nodes` carries the pipeline's tools and clients; keeping it separate from
    the topology is what lets the same shape be served to the UI from a process
    that has not started an investigation.
    """
    workflow = StateGraph(GraphState)

    # Compile from the same declaration served to the UI. Previously these
    # edges were listed twice and a diagram edit could silently drift from the
    # executable graph.
    for entry in TOPOLOGY["nodes"]:
        workflow.add_node(entry["id"], getattr(nodes, entry["id"]))
    routes = {"evidence": nodes.route_after_evidence,
              "reason": nodes.route_after_reason}
    for source, router in routes.items():
        options = {edge["to"]: edge["to"] for edge in TOPOLOGY["edges"]
                   if edge["from"] == source and edge.get("conditional")}
        workflow.add_conditional_edges(source, router, options)
    for edge in TOPOLOGY["edges"]:
        if edge.get("conditional"):
            continue
        source = START if edge["from"] == "__start__" else edge["from"]
        target = END if edge["to"] == "__end__" else edge["to"]
        workflow.add_edge(source, target)

    # The checkpointer provides fast per-process memory. GraphNodes.plan also
    # restores scoped thread summaries from OpenSearch after a restart or when
    # a follow-up lands on another replica.
    return workflow.compile(checkpointer=checkpointer or InMemorySaver())


class EventQueue:
    """The channel between graph nodes and the HTTP stream.

    A node returns state; it cannot yield. Anything the reader should see while
    a node is still running — every ReAct thought, every tool call — goes here
    instead, and `InvestigationPipeline.run` drains it concurrently with the
    graph's own execution.
    """

    _DONE = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    async def put(self, stage: str, data: dict) -> None:
        await self._queue.put((stage, data))

    async def close(self) -> None:
        await self._queue.put(self._DONE)

    async def drain(self):
        while True:
            item = await self._queue.get()
            if item is self._DONE:
                return
            yield item
