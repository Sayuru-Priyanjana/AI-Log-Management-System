import logging
import os
from typing import Optional

from app.llm.interface import LLMInterface
from app.models.investigation import InvestigationPlan
from app.correlation.models import CorrelatedEvidence
from .models import InvestigationAnalysis
from .prompts import ANALYSIS_SYSTEM_PROMPT, build_analysis_context
from .parser import AnalysisParser

logger = logging.getLogger(__name__)

class AnalysisAgent:
    def __init__(self, llm: LLMInterface):
        self.llm = llm
        self.parser = AnalysisParser()
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))

    async def analyze(
        self,
        plan: InvestigationPlan,
        evidence: CorrelatedEvidence
    ) -> tuple[InvestigationAnalysis, str]:
        
        logger.info("Analysis Agent: Context building started.")
        context = build_analysis_context(plan, evidence)
        schema_reminder = """
CRITICAL REMINDER: You MUST return ONLY valid JSON matching this exact structure:
{
  "incident_detected": true,
  "severity": "low", 
  "summary": "...",
  "incident_timeline": ["..."],
  "likely_causes": [{"description": "...", "confidence": 0.8, "evidence_ids": ["..."], "reasoning": "..."}],
  "contributing_factors": [{"factor": "...", "confidence": 0.5, "evidence_ids": ["..."], "explanation": "..."}],
  "supporting_evidence": ["..."],
  "conflicting_evidence": ["..."],
  "missing_evidence": ["..."],
  "recommended_next_steps": ["..."],
  "overall_confidence": 0.9
}
DO NOT return lists of objects for timeline or evidence. DO NOT use uppercase for severity.
"""
        user_prompt = f"Analyze the following correlated evidence according to the system instructions:\n\n{context}\n\n{schema_reminder}"
        
        for attempt in range(self.max_retries + 1):
            try:
                logger.info(f"Analysis Agent: LLM request started (Attempt {attempt + 1}/{self.max_retries + 1})")
                raw_response = await self.llm.generate(
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    json_format=True
                )
                logger.info("Analysis Agent: LLM request completed.")
                
                analysis = self.parser.parse(raw_response)
                logger.info("Analysis Agent: LLM response parsed successfully.")
                return analysis, user_prompt
                
            except Exception as e:
                logger.error(f"Analysis Agent: LLM parsing or request failure on attempt {attempt + 1}: {e}")
                if attempt < self.max_retries:
                    user_prompt += f"\n\nYour previous response failed to parse as valid JSON. Error: {e}. Please try again and return ONLY valid JSON."
                else:
                    logger.error("Analysis Agent: Max retries exceeded.")
                    raise RuntimeError(f"Analysis Agent failed to generate valid analysis after {self.max_retries + 1} attempts. Last error: {e}")
