import asyncio
import logging
from typing import Dict, Any

from app.models.investigation import InvestigationPlan
from app.models.evidence import InvestigationEvidence
from app.tools.application_logs import ApplicationLogTool
from app.tools.kubernetes_events import KubernetesEventTool
from app.opensearch.client import OpenSearchClient

logger = logging.getLogger(__name__)

class InvestigationDispatcher:
    def __init__(self, opensearch_client: OpenSearchClient):
        self.tools = {
            "application_logs": ApplicationLogTool(opensearch_client),
            "kubernetes_events": KubernetesEventTool(opensearch_client)
        }

    async def dispatch(self, plan: InvestigationPlan) -> InvestigationEvidence:
        evidence = InvestigationEvidence()
        tasks = []
        task_names = []
        
        for req_data in plan.required_data:
            if req_data in self.tools:
                tasks.append(self.tools[req_data].execute(plan))
                task_names.append(req_data)
            else:
                logger.warning(f"Requested data source '{req_data}' is not supported yet.")
                evidence.status[req_data] = "error: unsupported data source"
                
        if not tasks:
            return evidence
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for name, result in zip(task_names, results):
            if isinstance(result, Exception):
                logger.error(f"Tool {name} failed: {result}")
                evidence.status[name] = f"error: {str(result)}"
            else:
                evidence_list, query = result
                evidence.status[name] = "success"
                evidence.queries[name] = query
                
                if name == "application_logs":
                    evidence.application_logs = evidence_list
                elif name == "kubernetes_events":
                    evidence.kubernetes_events = evidence_list
                    
        return evidence
