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

class InvestigationEvidence(BaseModel):
    application_logs: List[ApplicationLogEvidence] = []
    kubernetes_events: List[KubernetesEventEvidence] = []
    
    # We can track statuses of the queries to report partial failures to the frontend
    # Example: {"application_logs": "success", "kubernetes_events": "error: connection refused"}
    status: Dict[str, str] = {}
    
    # Store the raw queries executed for transparency in UI
    queries: Dict[str, Any] = {}
