from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ApplicationLogEvidence(BaseModel):
    timestamp: datetime
    system_id: str
    environment: str
    service_name: Optional[str] = None
    namespace: Optional[str] = None
    pod_name: Optional[str] = None
    pod_uid: Optional[str] = None
    node_name: Optional[str] = None
    level: Optional[str] = None
    message: str
    event_category: Optional[str] = None
    event_action: Optional[str] = None
    event_outcome: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None

class KubernetesEventEvidence(BaseModel):
    timestamp: datetime
    system_id: str
    environment: str
    namespace: Optional[str] = None
    pod_name: Optional[str] = None
    pod_uid: Optional[str] = None
    node_name: Optional[str] = None

    reason: Optional[str] = None
    event_type: Optional[str] = None
    action: Optional[str] = None
    message: str

    source_component: Optional[str] = None
    outcome: Optional[str] = None

class MetricSample(BaseModel):
    timestamp: datetime
    value: float

class MetricSummary(BaseModel):
    average: Optional[float] = None
    maximum: Optional[float] = None
    minimum: Optional[float] = None
    initial: Optional[float] = None
    final: Optional[float] = None
    increase: Optional[float] = None

class MetricEvidence(BaseModel):
    metric_name: str
    metric_type: str  # e.g., "gauge", "counter"
    unit: str
    status: str = "success"  # "success" or "unavailable"
    reason: Optional[str] = None
    labels: Dict[str, str] = {}
    samples: List[MetricSample] = []
    summary: Optional[MetricSummary] = None

class InvestigationEvidence(BaseModel):
    application_logs: List[ApplicationLogEvidence] = []
    kubernetes_events: List[KubernetesEventEvidence] = []
    metrics: List[MetricEvidence] = []
    
    # We can track statuses of the queries to report partial failures to the frontend
    # Example: {"application_logs": "success", "kubernetes_events": "error: connection refused"}
    status: Dict[str, str] = {}
    
    # Store the raw queries executed for transparency in UI
    queries: Dict[str, Any] = {}
