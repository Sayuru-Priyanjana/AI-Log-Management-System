from __future__ import annotations

import json
import logging
import re
from typing import AsyncIterator

from app.agents.tool_bindings import ToolBindings
from app.llm.base import LLMClient, LLMUnavailable, PromptTruncated
from app.models.evidence import EvidenceBundle
from app.models.analysis import InvestigationWindows
from app.models.plan import InvestigationPlan

logger = logging.getLogger(__name__)

REACT_SYSTEM_PROMPT = """You are a senior site reliability engineer investigating a system incident.

You are equipped with tools to gather evidence. You must use a cycle of Thought, Action, and Observation to diagnose the root cause.
If the evidence gives you the answer, output is_finished: true and provide a highly detailed final conclusion.

When writing your final conclusion:
- Be highly detailed and structured. Use markdown formatting if helpful.
- Include a "Root Cause" section clearly stating what broke.
- Include an "Evidence" section explicitly citing specific log patterns, metric spikes, or Kubernetes events that prove your conclusion.
- Explain the chain of events (e.g., Service A failed because it depends on Service B, which timed out).

Available tools:
{tools}

Rules:
- You MUST respond ONLY in valid JSON matching this schema:
  {{
    "thought": "your reasoning about the current state and what to do next",
    "action": "tool_name_or_null",
    "action_input": {{"param": "value"}},
    "is_finished": boolean,
    "conclusion": "highly detailed Markdown string containing 'Root Cause', 'Evidence', and 'Chain of Events' sections if is_finished is true, else null",
    "root_cause_service": "service name if is_finished is true, else null"
  }}
- Do not add prose outside the JSON.
- Never invent evidence.
"""

class ReActAgent:
    def __init__(self, llm: LLMClient, max_steps: int = 10) -> None:
        self._llm = llm
        self.max_steps = max_steps

    async def run(self, plan: InvestigationPlan, windows: InvestigationWindows,
                  evidence: EvidenceBundle) -> AsyncIterator[dict]:
        """Runs the ReAct loop, yielding steps as it goes."""
        
        bindings = ToolBindings(plan, windows, evidence)
        tools_schema = bindings.schema()
        
        system = REACT_SYSTEM_PROMPT.format(tools=tools_schema)
        
        available_services = set()
        if evidence.logs and evidence.logs.patterns:
            available_services.update(p.service for p in evidence.logs.patterns if p.service)
        if evidence.metrics and evidence.metrics.series:
            available_services.update(s.service for s in evidence.metrics.series if s.service)
            
        services_str = ", ".join(sorted(available_services)) if available_services else "none observed"

        context_messages = [
            f"Goal: {plan.goal}",
            f"System: {plan.system_id}",
            f"Focus Service: {plan.service or 'all'}",
            f"Available Services in Evidence: {services_str}",
            f"Incident Window: {windows.incident.start} to {windows.incident.end}",
            "--- Investigation Log ---"
        ]
        
        for step in range(self.max_steps):
            prompt = "\n".join(context_messages) + "\n\nJSON Output:"
            
            try:
                response = await self._llm.generate(
                    system=system,
                    prompt=prompt,
                    schema={
                        "type": "object",
                        "properties": {
                            "thought": {"type": "string"},
                            "action": {"type": ["string", "null"]},
                            "action_input": {"type": ["object", "null"]},
                            "is_finished": {"type": "boolean"},
                            "conclusion": {
                                "type": ["string", "null"],
                                "description": "Highly detailed Markdown string containing Root Cause, Evidence, and Chain of Events sections."
                            },
                            "root_cause_service": {"type": ["string", "null"]}
                        },
                        "required": ["thought", "is_finished"]
                    }
                )
            except (LLMUnavailable, PromptTruncated) as exc:
                logger.error("LLM Error during ReAct loop: %s", exc)
                yield {"type": "error", "message": str(exc)}
                return

            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
            
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                logger.error("Invalid JSON from LLM: %s", text)
                yield {"type": "error", "message": f"LLM returned invalid JSON: {exc}"}
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action")
            action_input = parsed.get("action_input") or {}
            is_finished = parsed.get("is_finished", False)
            conclusion = parsed.get("conclusion")
            
            # Forgiving fallback: if the model specifies no action, it must be finished,
            # even if it forgot to set the is_finished flag to true.
            if not action and not is_finished:
                is_finished = True
                
            # If it finishes but forgets to provide a conclusion string, use its thought as the conclusion.
            if is_finished and not conclusion:
                conclusion = thought
            
            yield {
                "type": "thought",
                "text": thought,
                "step": step + 1
            }
            context_messages.append(f"Thought: {thought}")
            
            if is_finished:
                yield {
                    "type": "conclusion",
                    "conclusion": conclusion,
                    "service": parsed.get("root_cause_service")
                }
                return
                
            if action:
                yield {
                    "type": "action",
                    "tool": action,
                    "input": action_input
                }
                context_messages.append(f"Action: {action} {json.dumps(action_input)}")
                
                observation = bindings.execute(action, action_input)
                
                yield {
                    "type": "observation",
                    "text": observation
                }
                context_messages.append(f"Observation: {observation}")
            else:
                yield {
                    "type": "error",
                    "message": "Model did not specify an action or finish."
                }
                return
                
        yield {
            "type": "error",
            "message": f"Max steps ({self.max_steps}) reached without a conclusion."
        }
