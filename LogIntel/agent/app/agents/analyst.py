from __future__ import annotations

import json
import logging
import re

from app.agents.prompts import (
    NARRATIVE_SYSTEM_PROMPT,
    SELECTION_SCHEMA,
    SELECTION_SYSTEM_PROMPT,
    build_narrative_prompt,
    build_selection_prompt,
)
from app.llm.base import LLMClient, LLMUnavailable, PromptTruncated
from app.models.analysis import AnalystChoice, Candidate, InvestigationWindows
from app.models.evidence import EvidenceBundle
from app.models.plan import InvestigationPlan
from app.models.signals import Signal

logger = logging.getLogger(__name__)


class AnalystAgent:
    """The only place an LLM touches the conclusion, and it does two narrow jobs.

    Splitting selection from narration matters on a small local model: asking for
    a choice, a confidence, a timeline, a rationale and next steps in one response
    degrades all of them. A constrained choice is something a 7B model does well;
    open-ended root-cause analysis over raw evidence is not.
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def select(self, plan: InvestigationPlan, windows: InvestigationWindows,
                     signals: list[Signal], candidates: list[Candidate],
                     evidence: EvidenceBundle) -> tuple[AnalystChoice | None, str, list[str]]:
        """Returns (choice, prompt, warnings). A None choice means the caller
        should fall back to the deterministic ranking."""
        prompt = build_selection_prompt(plan, windows, signals, candidates, evidence)
        warnings: list[str] = []

        try:
            response = await self._llm.generate(
                system=SELECTION_SYSTEM_PROMPT, prompt=prompt, schema=SELECTION_SCHEMA
            )
        except PromptTruncated as exc:
            # Never accept an answer produced from a truncated prompt: it is
            # confident-sounding output generated from a fraction of the evidence.
            # Tagged distinctly from an outage because the fix is different —
            # raise num_ctx or shrink the budgets, rather than start Ollama.
            logger.error("Selection prompt truncated: %s", exc)
            return None, prompt, [f"prompt_truncated|{exc}"]
        except LLMUnavailable as exc:
            logger.warning("Selection LLM unavailable: %s", exc)
            return None, prompt, [f"llm_unavailable|{exc}"]

        choice = self._parse(response.text, warnings)
        if choice is None:
            return None, prompt, warnings

        valid_ids = {candidate.id for candidate in candidates}
        if choice.candidate_id not in valid_ids:
            # Occasionally the model answers with the category or the hypothesis
            # text instead of the id. Recover it if the intent is unambiguous.
            recovered = self._recover_candidate(choice.candidate_id, candidates)
            if recovered:
                warnings.append(
                    f"model returned '{choice.candidate_id}'; matched it to {recovered}"
                )
                choice.candidate_id = recovered
            else:
                warnings.append(
                    f"model chose '{choice.candidate_id}', which is not one of the "
                    f"candidates offered ({', '.join(sorted(valid_ids))})"
                )
                return None, prompt, warnings

        choice.confidence = max(0.0, min(1.0, float(choice.confidence or 0.0)))
        return choice, prompt, warnings

    async def narrate(self, plan: InvestigationPlan, chosen: Candidate | None,
                      timeline: list[str], confidence: float) -> tuple[str, list[str]]:
        prompt = build_narrative_prompt(plan, chosen, timeline, confidence)
        try:
            response = await self._llm.generate(system=NARRATIVE_SYSTEM_PROMPT, prompt=prompt)
        except (LLMUnavailable, PromptTruncated) as exc:
            logger.warning("Narrative generation failed: %s", exc)
            return "", [f"narrative unavailable: {exc}"]
        return response.text.strip(), []

    # ------------------------------------------------------------------ util
    @staticmethod
    def _parse(text: str, warnings: list[str]) -> AnalystChoice | None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.DOTALL)
        # Some models prepend a sentence before the object despite instructions.
        if not cleaned.startswith("{"):
            match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
            if match:
                cleaned = match.group(0)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            warnings.append(f"model did not return valid JSON ({exc}); "
                            f"falling back to the deterministic ranking")
            return None

        try:
            return AnalystChoice(**data)
        except Exception as exc:
            warnings.append(f"model JSON did not match the expected shape ({exc})")
            return None

    @staticmethod
    def _recover_candidate(value: str, candidates: list[Candidate]) -> str | None:
        needle = (value or "").strip().lower()
        if not needle:
            return None
        digits = re.search(r"\d+", needle)
        if digits:
            guess = f"cand:{int(digits.group(0))}"
            if any(candidate.id == guess for candidate in candidates):
                return guess
        matches = [
            candidate.id for candidate in candidates
            if candidate.category.value == needle or needle in candidate.hypothesis.lower()
        ]
        return matches[0] if len(matches) == 1 else None
