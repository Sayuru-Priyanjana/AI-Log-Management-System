from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agents.tool_bindings import ToolBindings, ToolResult
from app.config import settings
from app.llm.base import LLMClient, LLMUnavailable, PromptTruncated
from app.models.analysis import Candidate, InvestigationWindows
from app.models.answer import MODE_BY_INTENT, AnswerMode
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan
from app.models.signals import Signal

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = ("nothing matched", "no signals crossed", "no metric series",
                  "no log patterns matched", "no warning-level", "no metrics found",
                  "no log data to count", "no time-ordered events")


def _found_nothing(observation: str) -> bool:
    lowered = observation.lower()
    return any(marker in lowered for marker in _EMPTY_MARKERS)

SYSTEM_PROMPT = """You are a site reliability engineer answering a question about a running system.

You work in a loop: think, call a tool, read the observation, repeat. Stop as soon
as you can answer — extra steps cost time and add nothing.

Available tools:
{tools}

## Rules

- Reply with JSON only. No prose outside it.
- Cite evidence by the IDs shown in observations (sig:, pat:, evt:, met:, cand:).
  Copy them character for character. An ID you were not shown will be rejected,
  and inventing one is worse than admitting uncertainty. If an observation
  reported that nothing was found, cite nothing — an empty list is the honest
  answer and costs you nothing. Never emit a placeholder such as "sig:...".
- Never state a number you were not given. The tools have already measured
  everything against a baseline; your arithmetic is not needed and is not trusted.
- Absence of evidence is not evidence of absence. If a tool returns nothing,
  call get_investigation_scope before concluding that nothing happened.
- Logs are one source of three. If signals were detected, they are measurements
  from metrics and Kubernetes events, and an answer that discusses only logs
  while ignoring them is wrong however well written. Never report "no logs
  matched" as a conclusion when signals are present — explain the signals.
- Failures propagate upward through the call graph. If a dependency is broken,
  the services calling it are symptoms. Name the deepest failing component.
- Prefer the explanation that started first. An effect cannot precede its cause.

## Response schema

{{
  "thought": "what you now know and what you need next",
  "action": "tool name, or null when finished",
  "action_input": {{"param": "value"}},
  "is_finished": false,
  "answer": null
}}

When finished, set "is_finished": true, "action": null, and fill "answer":

{answer_schema}
"""

MODE_GUIDANCE: dict[AnswerMode, str] = {
    AnswerMode.ROOT_CAUSE: (
        "The user wants to know WHY something broke. Establish the causal chain and "
        "name the single component at its root. The signals are already in front of "
        "you; every one of them is a fact this answer has to account for, whether it "
        "came from a log, a Kubernetes event or a metric. Use get_dependencies to tell "
        "cause from symptom and get_timeline to confirm the ordering, and reach for "
        "get_service_events and get_service_metrics on the services the signals name — "
        "a restart, an eviction or a request-rate step change is often the cause the "
        "logs only describe the effects of. `headline` must name the failing component "
        "and what it did, and `detail` must say what the metrics and events showed, not "
        "only the logs."
    ),
    AnswerMode.DATA_EXTRACTION: (
        "The user wants to SEE specific records, not an analysis of them. Retrieve "
        "them and return them in `table`, one row per item, with the evidence ID in "
        "the first column. Which tool depends on what was asked for: search_logs for "
        "log lines, get_service_events for Kubernetes events, get_service_metrics or "
        "the signals already shown for metric movements and spikes. `headline` states "
        "what was found and how much of it. Do not write a root-cause analysis — it "
        "was not asked for."
    ),
    AnswerMode.AGGREGATION: (
        "The user wants a NUMBER or a breakdown. Use count_logs, or read the figures "
        "from get_signals and get_service_metrics. Put the breakdown in `table` and "
        "the number in `headline`. Never compute a total yourself — call the tool."
    ),
    AnswerMode.HEALTH_CHECK: (
        "The user wants to know whether the system is healthy right now. Call "
        "get_signals, and get_investigation_scope so you can say what was checked. "
        "If nothing crossed a threshold, say so plainly — that is a real answer, "
        "not a failure to find something."
    ),
    AnswerMode.EXPLANATION: (
        "The user wants something explained. Ground the explanation in what was "
        "actually observed in this system, and cite it."
    ),
}

ANSWER_SCHEMA_HINT = """{
  "headline": "one sentence that directly answers the question",
  "detail": "a short paragraph of supporting explanation",
  "root_cause_service": "service name, or null when not a root-cause question",
  "reasoning": [
    {"claim": "what you concluded",
     "because": "why it follows",
     "evidence_ids": ["COPY IDs EXACTLY FROM OBSERVATIONS, e.g. sig:OOM_KILL:payment-api-abc12.
                       If an observation gave you no IDs, use an empty list [].
                       Never write a placeholder, an ellipsis, or an ID you did not see."],
     "kind": "observation | inference | elimination"}
  ],
  "assumptions": [
    {"statement": "what you assumed",
     "basis": "why it is reasonable",
     "impact_if_wrong": "what the conclusion loses if it is not true"}
  ],
  "confidence": 0.0,
  "limitations": ["what this answer does not establish"],
  "next_steps": [
    {"label": "what to do next, in plain words",
     "tool": "OPTIONAL: a tool from the list above that would do it, so the reader
              can run it with one click. Omit it if no tool fits — the step is
              then offered as a fresh investigation instead.",
     "tool_input": {"service_name": "..."}}
  ]
}"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": ["string", "null"]},
        "action_input": {"type": ["object", "null"]},
        "is_finished": {"type": "boolean"},
        "answer": {
            "type": ["object", "null"],
            "properties": {
                "headline": {"type": "string"},
                "detail": {"type": "string"},
                "root_cause_service": {"type": ["string", "null"]},
                "reasoning": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "because": {"type": "string"},
                            "evidence_ids": {"type": "array", "items": {"type": "string"}},
                            "kind": {"type": "string"},
                        },
                        "required": ["claim"],
                    },
                },
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "basis": {"type": "string"},
                            "impact_if_wrong": {"type": "string"},
                        },
                        "required": ["statement"],
                    },
                },
                "confidence": {"type": "number"},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "next_steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "tool": {"type": ["string", "null"]},
                            "tool_input": {"type": ["object", "null"]},
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["headline"],
        },
    },
    "required": ["thought", "is_finished"],
}


class ReActAgent:
    """The reasoning loop.

    Its job is to decide what to look at next and to assemble an answer from what
    the tools return. It deliberately does not measure anything itself: the tools
    hand it figures that have already been compared against a baseline, and any
    number it produces on its own is treated as unsupported.
    """

    def __init__(self, llm: LLMClient, max_steps: int | None = None) -> None:
        self._llm = llm
        self.max_steps = max_steps or settings.react_max_steps

    async def run(self, plan: InvestigationPlan, windows: InvestigationWindows,
                  evidence: EvidenceBundle, signals: list[Signal],
                  candidates: list[Candidate]) -> AsyncIterator[dict]:
        bindings = ToolBindings(plan, windows, evidence, signals, candidates)
        mode = MODE_BY_INTENT.get(plan.intent.value, AnswerMode.ROOT_CAUSE)

        system = SYSTEM_PROMPT.format(
            tools=bindings.schema(), answer_schema=ANSWER_SCHEMA_HINT
        )

        transcript = [
            f"Question: {plan.goal}",
            f"System: {plan.system_id} / {plan.environment}",
            f"Focus service: {plan.service or 'whole system'}",
            f"Known services: {', '.join(self._known_services(evidence)) or 'none observed'}",
            f"Window analysed: {windows.incident}",
            "",
            f"TASK TYPE: {mode.value}. {MODE_GUIDANCE[mode]}",
            "",
            "--- investigation log ---",
        ]

        # The measured facts are put in front of the model before it chooses
        # anything. Left to itself it opens with a log search, gets nothing, and
        # concludes — one live run answered "what are the metric spikes?" with
        # "no log patterns matched" while six signals, three of them traffic
        # surges, sat uncollected. Signals are the whole point of the
        # deterministic layer; whether the loop consults them is not a decision
        # worth delegating.
        opening_input = {"service_name": "all"}
        opening = bindings.execute("get_signals", opening_input)
        opening_text = opening.text
        if opening.evidence_ids:
            # Naming what the list *is* matters as much as showing it. Asked to
            # list the metric spikes, the loop ran three log searches for the
            # words "metric spike", found nothing, and reported none — with the
            # spikes sitting in the observation above. A spike is a measurement,
            # not a phrase that appears in a log line.
            opening_text += (
                "\n\nThese ARE the detected spikes, anomalies and departures from "
                "normal in this window, measured from logs, Kubernetes events and "
                "metrics alike. If the question asks which spikes, anomalies or "
                "signals occurred, this list is the answer — do not search the logs "
                "for the words 'spike' or 'anomaly', they do not appear in log text."
            )
            if mode in (AnswerMode.DATA_EXTRACTION, AnswerMode.AGGREGATION):
                opening_text += (" Put one row per signal in `table`, leading with "
                                 "its evidence ID.")
        transcript.append("Observation (provided automatically, before you asked): "
                          + opening_text)
        yield {"type": "observation", "step": 0, "text": opening_text,
               "evidence_ids": opening.evidence_ids, "table": opening.table,
               "automatic": True}

        # Registered as already-called so re-asking for it is caught by the repeat
        # guard instead of burning a step to re-read what is already on screen.
        seen_calls: set[str] = {
            f"get_signals:{json.dumps(opening_input, sort_keys=True)}"
        }
        empty_calls: dict[str, int] = {}
        repeated = 0

        for step in range(1, self.max_steps + 1):
            remaining = self.max_steps - step
            budget = (f"\n[Step {step} of {self.max_steps}. "
                      + ("Finish now — this is your last step; answer from what you "
                         "already have." if remaining == 0 else
                         f"{remaining} step(s) left.") + "]")
            prompt = "\n".join(transcript) + budget + "\n\nJSON:"

            try:
                response = await self._llm.generate(
                    system=system, prompt=prompt, schema=RESPONSE_SCHEMA
                )
            except PromptTruncated as exc:
                # The transcript outgrew the context window, so the model is now
                # reasoning from a fragment of its own investigation. Anything it
                # says next is unmoored; stop rather than accept it.
                logger.error("ReAct prompt truncated at step %d: %s", step, exc)
                yield {"type": "error", "code": "prompt_truncated", "message": str(exc)}
                return
            except LLMUnavailable as exc:
                logger.warning("ReAct LLM unavailable at step %d: %s", step, exc)
                yield {"type": "error", "code": "llm_unavailable", "message": str(exc)}
                return

            parsed = self._parse(response.text)
            if parsed is None:
                # One malformed reply is recoverable: tell the model what went
                # wrong and let it try again rather than abandoning the run.
                yield {"type": "note", "step": step,
                       "message": "The model returned unparseable JSON; asking it to retry."}
                transcript.append(
                    "System: your last reply was not valid JSON. Reply with the JSON "
                    "object only, no prose, no code fences."
                )
                continue

            thought = (parsed.get("thought") or "").strip()
            action = parsed.get("action")
            action_input = parsed.get("action_input") or {}
            is_finished = bool(parsed.get("is_finished"))
            answer = parsed.get("answer")

            if thought:
                yield {"type": "thought", "step": step, "text": thought}
                transcript.append(f"Thought: {thought}")

            # A reply with no action and no answer is a stall. Treat it as being
            # finished only if something usable came with it.
            if not action and not is_finished and not answer:
                transcript.append(
                    "System: you gave neither an action nor an answer. Either call a "
                    "tool or set is_finished with a filled-in answer."
                )
                continue

            if is_finished or (not action and answer):
                yield {"type": "answer", "step": step, "mode": mode.value,
                       "answer": answer or {}, "exposed_ids": sorted(bindings.exposed_ids),
                       "steps_used": step, "tool_calls": len(bindings.call_log)}
                return

            signature = f"{action}:{json.dumps(action_input, sort_keys=True)}"
            if signature in seen_calls:
                repeated += 1
                # Repeating a call cannot produce new information — the tools are
                # pure functions of an evidence set that does not change mid-run.
                observation = ToolResult(
                    f"You already called {action} with these arguments and got the same "
                    f"answer. It will not change. Use a different tool or different "
                    f"arguments, or finish with what you have."
                )
                if repeated >= 2:
                    transcript.append(
                        "System: you are repeating tool calls. Produce your answer now "
                        "from the evidence already gathered."
                    )
            else:
                seen_calls.add(signature)
                yield {"type": "action", "step": step, "tool": action, "input": action_input}
                observation = bindings.execute(action, action_input)

                # Varying one argument and re-running a tool that keeps finding
                # nothing is the same dead end reached by a different route. One
                # live run spent four steps re-searching the same term at every
                # log level in turn; the exact-duplicate check never fired.
                if _found_nothing(observation.text):
                    empty_calls[action] = empty_calls.get(action, 0) + 1
                    if empty_calls[action] >= 3:
                        transcript.append(
                            f"System: {action} has now returned nothing {empty_calls[action]} "
                            f"times with different arguments. Stop varying the arguments — "
                            f"either use a different tool or report that nothing was found."
                        )

            yield {"type": "observation", "step": step, "text": observation.text,
                   "evidence_ids": observation.evidence_ids,
                   "table": observation.table}
            transcript.append(f"Action: {action} {json.dumps(action_input)}")
            transcript.append(f"Observation: {observation.text}")

        # Out of steps. What was gathered is still worth reporting, so hand back
        # the transcript rather than nothing.
        yield {"type": "exhausted", "steps_used": self.max_steps,
               "exposed_ids": sorted(bindings.exposed_ids),
               "tool_calls": len(bindings.call_log),
               "message": (f"Reached the {self.max_steps}-step limit without a "
                           f"conclusion. The evidence gathered is reported below.")}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _known_services(evidence: EvidenceBundle) -> list[str]:
        names = {p.service for p in evidence.logs.patterns if p.service}
        names |= {s.service for s in evidence.metrics.series if s.service}
        names |= set(evidence.logs.dependency_edges)
        names |= {c for callees in evidence.logs.dependency_edges.values() for c in callees}
        return sorted(n for n in names if n)

    @staticmethod
    def _parse(text: str) -> dict | None:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)
        if not cleaned.startswith("{"):
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if not match:
                return None
            cleaned = match.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
