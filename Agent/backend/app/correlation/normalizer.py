from typing import List, Optional
from datetime import datetime
import uuid

from app.models.evidence import ApplicationLogEvidence, KubernetesEventEvidence, MetricEvidence
from app.correlation.models import TimelineEvidence

class EvidenceNormalizer:
    @staticmethod
    def normalize_log(log: ApplicationLogEvidence) -> TimelineEvidence:
        metadata = {}
        if log.trace_id:
            metadata["trace_id"] = log.trace_id
        if log.request_id:
            metadata["request_id"] = log.request_id
            
        # Try to infer some container_name from pod if missing but usually logs belong to primary container
        
        return TimelineEvidence(
            id=f"log-{uuid.uuid4().hex[:8]}",
            timestamp=log.timestamp,
            source_type="application_log",
            system_id=log.system_id,
            environment=log.environment,
            service_name=log.service_name,
            namespace=log.namespace,
            pod_name=log.pod_name,
            pod_uid=log.pod_uid,
            node_name=log.node_name,
            severity=log.level.upper() if log.level else None,
            event_category=log.event_category,
            event_action=log.event_action,
            title=f"Application {log.level.upper() if log.level else 'LOG'}" + (f": {log.event_action}" if log.event_action else ""),
            message=log.message,
            metadata=metadata
        )

    @staticmethod
    def normalize_event(event: KubernetesEventEvidence) -> TimelineEvidence:
        metadata = {}
        if event.source_component:
            metadata["source_component"] = event.source_component
            
        severity = "HIGH" if event.event_type == "Warning" else "INFO"
            
        return TimelineEvidence(
            id=f"event-{uuid.uuid4().hex[:8]}",
            timestamp=event.timestamp,
            source_type="kubernetes_event",
            system_id=event.system_id,
            environment=event.environment,
            namespace=event.namespace,
            pod_name=event.pod_name,
            pod_uid=event.pod_uid,
            node_name=event.node_name,
            severity=severity,
            event_type=event.event_type,
            event_action=event.action or event.reason,
            title=f"Kubernetes {event.event_type or 'Event'}: {event.reason or event.action or 'Unknown'}",
            message=event.message,
            metadata=metadata
        )

    @staticmethod
    def normalize_metrics(metrics: List[MetricEvidence], system_id: str, environment: str) -> List[TimelineEvidence]:
        timeline_items = []
        for metric in metrics:
            if metric.status != "success":
                continue
                
            # Extract common infra labels from prometheus labels if available
            namespace = metric.labels.get("namespace")
            pod = metric.labels.get("pod")
            container = metric.labels.get("container")
            
            for sample in metric.samples:
                timeline_items.append(
                    TimelineEvidence(
                        id=f"metric-{uuid.uuid4().hex[:8]}",
                        timestamp=sample.timestamp,
                        source_type="metric",
                        system_id=system_id,
                        environment=environment,
                        namespace=namespace,
                        pod_name=pod,
                        container_name=container,
                        title=f"{metric.metric_name} observation",
                        metric_name=metric.metric_name,
                        metric_value=sample.value,
                        metric_unit=metric.unit,
                        metadata={"labels": metric.labels, "metric_type": metric.metric_type}
                    )
                )
        return timeline_items

    @classmethod
    def normalize_all(cls, application_logs: List[ApplicationLogEvidence], 
                      kubernetes_events: List[KubernetesEventEvidence], 
                      metrics: List[MetricEvidence], 
                      system_id: str, environment: str) -> List[TimelineEvidence]:
        timeline = []
        for log in application_logs:
            timeline.append(cls.normalize_log(log))
            
        for event in kubernetes_events:
            timeline.append(cls.normalize_event(event))
            
        timeline.extend(cls.normalize_metrics(metrics, system_id, environment))
        
        return timeline
