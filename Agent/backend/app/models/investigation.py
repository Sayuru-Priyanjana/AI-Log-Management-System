from pydantic import BaseModel, Field
from typing import Optional, List, Literal

class InvestigationRequest(BaseModel):
    system_id: str
    system_name: str
    environment: str
    question: str

class TimeRange(BaseModel):
    type: Literal["relative", "absolute"]
    start: Optional[str] = None
    end: Optional[str] = None
    duration: Optional[str] = None

class InvestigationPlan(BaseModel):
    intent: str
    system_id: str
    environment: str
    service: Optional[str] = None
    time_range: TimeRange
    required_data: List[Literal["application_logs", "kubernetes_events", "metrics"]]
    investigation_goal: str
