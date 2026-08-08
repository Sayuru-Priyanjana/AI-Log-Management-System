from typing import List, Tuple, Dict, Any, Optional
import logging
from datetime import datetime

from app.tools.base import InvestigationTool
from app.models.investigation import InvestigationPlan
from app.models.evidence import MetricSample, MetricSummary, MetricEvidence
from app.prometheus.client import PrometheusClient
from app.config import settings

logger = logging.getLogger(__name__)

class MetricsTool(InvestigationTool):
    def __init__(self, client: PrometheusClient):
        self.client = client
        self.step = settings.PROMETHEUS_DEFAULT_STEP

    async def execute(self, plan: InvestigationPlan) -> Tuple[List[MetricEvidence], Dict[str, Any]]:
        start_time, end_time = self.resolve_time_range(plan.time_range)
        service = plan.service or ".*"
        
        # Hardcoded PromQL Templates
        # These ensure that Qwen cannot inject arbitrary PromQL.
        templates = [
            {
                "name": "pod_cpu_usage",
                "type": "gauge",
                "unit": "cores",
                "query": f'rate(container_cpu_usage_seconds_total{{{settings.PROMETHEUS_CONTAINER_LABEL}=~".*{service}.*"}}[5m])'
            },
            {
                "name": "pod_memory_usage",
                "type": "gauge",
                "unit": "bytes",
                "query": f'container_memory_working_set_bytes{{{settings.PROMETHEUS_CONTAINER_LABEL}=~".*{service}.*"}}'
            },
            {
                "name": "pod_restarts",
                "type": "counter",
                "unit": "count",
                "query": f'kube_pod_container_status_restarts_total{{{settings.PROMETHEUS_CONTAINER_LABEL}=~".*{service}.*"}}'
            },
            {
                "name": "pod_ready_status",
                "type": "gauge",
                "unit": "status",
                "query": f'kube_pod_status_ready{{{settings.PROMETHEUS_POD_LABEL}=~".*{service}.*"}}'
            }
        ]
        
        evidence_list = []
        queries_run = {}
        
        for tmpl in templates:
            query = tmpl["query"]
            queries_run[tmpl["name"]] = query
            
            try:
                result = await self.client.query_range(
                    query=query,
                    start=start_time,
                    end=end_time,
                    step=self.step
                )
                
                # result usually looks like {"resultType": "matrix", "result": [...]}
                series_data = result.get("result", [])
                
                if not series_data:
                    evidence_list.append(MetricEvidence(
                        metric_name=tmpl["name"],
                        metric_type=tmpl["type"],
                        unit=tmpl["unit"],
                        status="unavailable",
                        reason="No data found in Prometheus for this metric."
                    ))
                    continue
                    
                for series in series_data:
                    labels = series.get("metric", {})
                    values = series.get("values", [])
                    
                    samples = []
                    raw_values = []
                    
                    for val in values:
                        # Prometheus returns [timestamp, "value"]
                        ts = datetime.fromtimestamp(float(val[0]))
                        v = float(val[1])
                        samples.append(MetricSample(timestamp=ts, value=v))
                        raw_values.append(v)
                        
                    summary = self._calculate_summary(tmpl["type"], raw_values)
                    
                    evidence_list.append(MetricEvidence(
                        metric_name=tmpl["name"],
                        metric_type=tmpl["type"],
                        unit=tmpl["unit"],
                        labels=labels,
                        samples=samples,
                        summary=summary
                    ))
                    
            except Exception as e:
                logger.warning(f"Failed to fetch metric {tmpl['name']}: {e}")
                evidence_list.append(MetricEvidence(
                    metric_name=tmpl["name"],
                    metric_type=tmpl["type"],
                    unit=tmpl["unit"],
                    status="unavailable",
                    reason=f"Error fetching metric: {str(e)}"
                ))

        return evidence_list, queries_run

    def _calculate_summary(self, metric_type: str, values: List[float]) -> MetricSummary:
        if not values:
            return MetricSummary()
            
        summary = MetricSummary()
        
        if metric_type == "gauge":
            summary.average = sum(values) / len(values)
            summary.maximum = max(values)
            summary.minimum = min(values)
        elif metric_type == "counter":
            summary.initial = values[0]
            summary.final = values[-1]
            summary.increase = values[-1] - values[0]
            
        return summary
