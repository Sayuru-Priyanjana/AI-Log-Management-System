from typing import List
from datetime import datetime

from app.tools.base import InvestigationTool
from app.models.investigation import InvestigationPlan
from app.models.evidence import ApplicationLogEvidence
from app.opensearch.client import OpenSearchClient
from app.config import settings

class ApplicationLogTool(InvestigationTool):
    def __init__(self, client: OpenSearchClient):
        self.client = client
        self.index = settings.OPENSEARCH_APPLICATION_LOG_INDEX

    async def execute(self, plan: InvestigationPlan) -> tuple[List[ApplicationLogEvidence], dict]:
        start_time, end_time = self.resolve_time_range(plan.time_range)
        
        # Build OpenSearch Query
        must_clauses = [
            {"term": {"system.id.keyword": plan.system_id}},
            {"term": {"environment.keyword": plan.environment}}
        ]
        
        if plan.service:
            must_clauses.append({"term": {"service.name.keyword": plan.service}})
            
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
                # Standard Python fromisoformat for Z suffixed strings (Python 3.11+)
                timestamp_str = source.get("@timestamp", "")
                ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except Exception:
                # Skip logs without valid timestamps or use fallback
                continue
                
            evidence = ApplicationLogEvidence(
                timestamp=ts,
                system_id=source.get("system", {}).get("id", ""),
                environment=source.get("environment", ""),
                service_name=source.get("service", {}).get("name"),
                namespace=source.get("kubernetes", {}).get("namespace"),
                pod_name=source.get("kubernetes", {}).get("pod", {}).get("name"),
                pod_uid=source.get("kubernetes", {}).get("pod", {}).get("uid"),
                node_name=source.get("kubernetes", {}).get("node", {}).get("name"),
                level=source.get("log", {}).get("level"),
                message=source.get("log", {}).get("message", ""),
                event_category=source.get("event", {}).get("category"),
                event_action=source.get("event", {}).get("action"),
                event_outcome=source.get("event", {}).get("outcome"),
                trace_id=source.get("trace", {}).get("id"),
                request_id=source.get("http", {}).get("request", {}).get("id")
            )
            evidence_list.append(evidence)
            
        return evidence_list, query
