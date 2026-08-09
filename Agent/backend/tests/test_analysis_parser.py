import pytest
from app.agents.analysis.parser import AnalysisParser
from app.agents.analysis.models import InvestigationAnalysis

def test_parse_valid_json():
    parser = AnalysisParser()
    valid_json = """
    {
      "incident_detected": true,
      "severity": "high",
      "summary": "Database connectivity failure.",
      "incident_timeline": ["Event 1"],
      "likely_causes": [
        {
          "description": "DB Failure",
          "confidence": 0.9,
          "evidence_ids": ["log-1"],
          "reasoning": "Logs say so."
        }
      ],
      "contributing_factors": [],
      "supporting_evidence": ["log-1"],
      "conflicting_evidence": [],
      "missing_evidence": [],
      "recommended_next_steps": [],
      "overall_confidence": 0.9
    }
    """
    analysis = parser.parse(valid_json)
    assert isinstance(analysis, InvestigationAnalysis)
    assert analysis.severity == "high"
    assert len(analysis.likely_causes) == 1

def test_parse_markdown_json():
    parser = AnalysisParser()
    markdown_json = """
    Here is the analysis:
    ```json
    {
      "incident_detected": true,
      "severity": "high",
      "summary": "Database connectivity failure.",
      "incident_timeline": ["Event 1"],
      "likely_causes": [],
      "contributing_factors": [],
      "supporting_evidence": [],
      "conflicting_evidence": [],
      "missing_evidence": [],
      "recommended_next_steps": [],
      "overall_confidence": 0.9
    }
    ```
    """
    analysis = parser.parse(markdown_json)
    assert isinstance(analysis, InvestigationAnalysis)

def test_parse_invalid_json():
    parser = AnalysisParser()
    with pytest.raises(ValueError, match="LLM did not return valid JSON"):
        parser.parse("this is not json")

def test_parse_missing_fields():
    parser = AnalysisParser()
    invalid_structure = """
    {
      "incident_detected": true,
      "summary": "Missing severity field"
    }
    """
    with pytest.raises(ValueError, match="LLM returned invalid structure"):
        parser.parse(invalid_structure)
