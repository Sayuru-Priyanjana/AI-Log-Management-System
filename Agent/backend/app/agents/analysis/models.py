from pydantic import BaseModel, Field
from typing import List, Literal

class CauseHypothesis(BaseModel):
    description: str
    confidence: float
    evidence_ids: List[str]
    reasoning: str

class ContributingFactor(BaseModel):
    factor: str
    confidence: float
    evidence_ids: List[str]
    explanation: str

class InvestigationAnalysis(BaseModel):
    incident_detected: bool
    severity: Literal["low", "medium", "high", "critical", "unknown"]
    summary: str
    incident_timeline: List[str]
    likely_causes: List[CauseHypothesis]
    contributing_factors: List[ContributingFactor]
    supporting_evidence: List[str]
    conflicting_evidence: List[str]
    missing_evidence: List[str]
    recommended_next_steps: List[str]
    overall_confidence: float
