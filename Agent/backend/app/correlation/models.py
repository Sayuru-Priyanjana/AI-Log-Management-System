from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from app.models.investigation import TimeRange

class TimelineEvidence(BaseModel):
    id: str
    timestamp: datetime
    
    source_type: Literal["application_log", "kubernetes_event", "metric"]
    
    system_id: str
    environment: str
    
    service_name: Optional[str] = None
    namespace: Optional[str] = None
    pod_name: Optional[str] = None
    pod_uid: Optional[str] = None
    container_name: Optional[str] = None
    node_name: Optional[str] = None
    
    severity: Optional[str] = None
    event_type: Optional[str] = None
    event_category: Optional[str] = None
    event_action: Optional[str] = None
    
    title: str
    message: Optional[str] = None
    
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metric_unit: Optional[str] = None
    
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceRelationship(BaseModel):
    source_id: str
    target_id: str
    
    relationship_type: Literal["temporal", "infrastructure", "metric_change"]
    score: float
    time_delta_seconds: float
    reasons: List[str]


class CorrelationGroup(BaseModel):
    id: str
    start_time: datetime
    end_time: datetime
    evidence_ids: List[str]
    signals: List[str]
    summary: Optional[str] = None


class OperationalSignal(BaseModel):
    type: str  # e.g., ERROR_BURST, KUBERNETES_BACKOFF, CPU_SPIKE
    severity: str  # high, medium, low
    timestamp: datetime
    
    # Relevant infrastructure
    service: Optional[str] = None
    pod: Optional[str] = None
    
    # Specific signal details
    count: Optional[int] = None
    window_seconds: Optional[int] = None
    increase: Optional[float] = None
    baseline: Optional[float] = None
    peak: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class CorrelatedEvidence(BaseModel):
    timeline: List[TimelineEvidence]
    relationships: List[EvidenceRelationship]
    groups: List[CorrelationGroup]
    signals: List[OperationalSignal]
    statistics: Dict[str, Any]
    investigation_window: TimeRange
