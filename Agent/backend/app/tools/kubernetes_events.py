from typing import List
from datetime import datetime

from app.tools.base import InvestigationTool
from app.models.investigation import InvestigationPlan
from app.models.evidence import KubernetesEventEvidence
from app.opensearch.client import OpenSearchClient
from app.config import settings

class KubernetesEventTool(InvestigationTool):
    def __init__(self, client: OpenSearchClient):
        self.client = client
        self.index = settings.OPENSEARCH_KUBERNETES_EVENT_INDEX

    async def execute(self, plan: InvestigationPlan) -> tuple[List[KubernetesEventEvidence], dict]:
        start_time, end_time = self.resolve_time_range(plan.time_range)
        
        # Build OpenSearch Query
        must_clauses = [
            {"term": {"system.id.keyword": plan.system_id}},
            {"term": {"environment.keyword": plan.environment}}
        ]
        
        # If service is specified, we try to match namespace or pod using wildcard since
        # kubernetes events might not have a direct "service" label.
        # Example: pod name usually contains the service name (payment-api-xxx-yyy)
        if plan.service:
            must_clauses.append({
                "wildcard": {
                    "kubernetes.pod.name.keyword": f"*{plan.service}*"
                }
            })
            
        must_clauses.append({
            "range": {
                "@timestamp": {
                    "gte": start_time.isoformat(),
                    "lte": end_time.isoformat()
                }
            }
        })
        
        query = {
            "query": {
                "bool": {
                    "filter": must_clauses
                }
            },
            "sort": [
                {"@timestamp": {"order": "asc"}}
            ]
        }
        
        response = await self.client.search(index=self.index, query=query)
        hits = response.get("hits", {}).get("hits", [])
        
        evidence_list = []
        for hit in hits:
            source = hit.get("_source", {})
            
            # Parse timestamp safely
            try:
                timestamp_str = source.get("@timestamp", "")
                ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except Exception:
                continue
                
            evidence = KubernetesEventEvidence(
                timestamp=ts,
                system_id=source.get("system", {}).get("id", ""),
                environment=source.get("environment", ""),
                namespace=source.get("kubernetes", {}).get("namespace"),
                pod_name=source.get("kubernetes", {}).get("pod", {}).get("name"),
                pod_uid=source.get("kubernetes", {}).get("pod", {}).get("uid"),
                node_name=source.get("kubernetes", {}).get("node", {}).get("name"),
                reason=source.get("event", {}).get("reason"),
                event_type=source.get("event", {}).get("type"),
                action=source.get("event", {}).get("action"),
                message=source.get("message", ""),
                source_component=source.get("event", {}).get("source", {}).get("component"),
                outcome=source.get("event", {}).get("outcome")
            )
            evidence_list.append(evidence)
            
        return evidence_list, query
