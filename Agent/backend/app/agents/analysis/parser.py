import json
import re
from pydantic import ValidationError
from .models import InvestigationAnalysis
import logging

logger = logging.getLogger(__name__)

class AnalysisParser:
    def parse(self, raw_response: str) -> InvestigationAnalysis:
        # Strip potential markdown code fences
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Extract content between the first and last ```
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
            else:
                # If there's no closing block, just strip the first line
                cleaned = "\n".join(cleaned.split("\n")[1:])

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response: {e}\nRaw Response: {raw_response}")
            raise ValueError(f"LLM did not return valid JSON. Error: {e}")

        try:
            analysis = InvestigationAnalysis(**data)
            return analysis
        except ValidationError as e:
            logger.error(f"Failed to validate InvestigationAnalysis from JSON: {e}\nParsed Data: {data}")
            raise ValueError(f"LLM returned invalid structure. Error: {e}")
