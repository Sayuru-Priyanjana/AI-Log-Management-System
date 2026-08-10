import json
from typing import Any

from app.models.evidence import EvidenceBundle
from app.models.analysis import InvestigationWindows
from app.models.plan import InvestigationPlan


class ToolBindings:
    """Wraps bulk evidence into targeted text-based tool responses for the LLM."""

    def __init__(self, plan: InvestigationPlan, windows: InvestigationWindows,
                 evidence: EvidenceBundle):
        self.plan = plan
        self.windows = windows
        self.evidence = evidence

    def get_service_logs(self, service_name: str) -> str:
        """Returns aggregated log patterns for a specific service."""
        if not self.evidence.logs or self.evidence.logs.status != "ok":
            return f"Log data unavailable: {getattr(self.evidence.logs, 'reason', 'unknown')}"
        
        patterns = [
            p for p in self.evidence.logs.patterns
            if service_name == "all" or p.service == service_name
        ]
        
        if not patterns:
            return f"No significant error/warning log patterns found for service '{service_name}' in the incident window."
        
        lines = [f"Log Patterns for {service_name}:"]
        for p in patterns:
            first = p.first_seen.strftime("%H:%M:%S") if p.first_seen else "unknown"
            last = p.last_seen.strftime("%H:%M:%S") if p.last_seen else "unknown"
            lines.append(
                f"- [{p.level}] {p.example}\n"
                f"  Occurred {p.count} times. First seen: {first}, Last seen: {last}"
            )
        
        return "\n".join(lines)

    def get_service_metrics(self, service_name: str) -> str:
        """Returns metric anomalies for a specific service."""
        if not self.evidence.metrics or self.evidence.metrics.status != "ok":
            return f"Metric data unavailable: {getattr(self.evidence.metrics, 'reason', 'unknown')}"
            
        series = [
            s for s in self.evidence.metrics.series
            if service_name == "all" or s.service == service_name
        ]
        
        if not series:
            return f"No metrics found for service '{service_name}'."
            
        lines = [f"Metrics for {service_name}:"]
        has_anomalies = False
        for s in series:
            ratio = s.ratio_to_baseline()
            status = "Normal"
            if ratio is not None:
                if ratio > 1.5: status = "Spike"
                elif ratio < 0.5: status = "Drop"
                
            # If querying 'all', filter out Normal metrics to avoid flooding the context.
            if service_name == "all" and status == "Normal":
                continue
                
            inc_avg = s.incident.average if s.incident and s.incident.average is not None else 0.0
            base_avg = s.baseline.average if s.baseline and s.baseline.average is not None else 0.0
            
            svc = s.service or "unknown"
            lines.append(f"- {s.metric} [{svc}]: {status} (mean: {inc_avg:.2f}, baseline: {base_avg:.2f})")
            has_anomalies = True
            
        if service_name == "all" and not has_anomalies:
            return "No metric anomalies (spikes or drops) found across any services."
            
        return "\n".join(lines)

    def get_service_events(self, service_name: str) -> str:
        """Returns Kubernetes events for a specific service."""
        if not self.evidence.events or self.evidence.events.status != "ok":
            return f"Event data unavailable: {getattr(self.evidence.events, 'reason', 'unknown')}"
            
        events = [
            e for e in self.evidence.events.events
            if service_name == "all" or e.service == service_name or (e.pod and e.pod.startswith(service_name))
        ]
        
        if not events:
            return f"No Kubernetes events found for service '{service_name}'."
            
        lines = [f"Kubernetes Events for {service_name}:"]
        for e in events:
            # Skip noise
            if e.reason in ("SuccessfulCreate", "Scheduled", "Created", "Started", "Pulling", "Pulled"):
                continue
            first = e.first_timestamp.strftime("%H:%M:%S") if e.first_timestamp else "unknown"
            lines.append(f"- [{e.type}/{e.reason}] {e.message} (x{e.count}, starting {first})")
            
        if len(lines) == 1:
            return f"No significant/abnormal Kubernetes events found for service '{service_name}' (only normal startup noise)."
            
        return "\n".join(lines)
        
    def get_dependencies(self, service_name: str) -> str:
        """Returns services that the given service calls."""
        if not self.evidence.logs or self.evidence.logs.status != "ok":
            return "Dependency data unavailable."
            
        if service_name == "all":
            if not self.evidence.logs.dependency_edges:
                return "No dependency data found in logs."
            lines = ["Dependencies across all services:"]
            for caller, targets in self.evidence.logs.dependency_edges.items():
                lines.append(f"- {caller} calls: {', '.join(targets)}")
            return "\n".join(lines)

        edges = self.evidence.logs.dependency_edges.get(service_name, [])
        if not edges:
            return f"Service '{service_name}' has no known outgoing calls to other services in the logs."
        return f"Service '{service_name}' calls: {', '.join(edges)}"

    def execute(self, action: str, inputs: dict[str, Any]) -> str:
        """Dispatches to the correct tool method based on action name."""
        try:
            if action == "get_service_logs":
                return self.get_service_logs(inputs.get("service_name", ""))
            elif action == "get_service_metrics":
                return self.get_service_metrics(inputs.get("service_name", ""))
            elif action == "get_service_events":
                return self.get_service_events(inputs.get("service_name", ""))
            elif action == "get_dependencies":
                return self.get_dependencies(inputs.get("service_name", ""))
            else:
                return f"Error: Unknown tool '{action}'"
        except Exception as e:
            return f"Tool execution failed: {e}"

    @classmethod
    def schema(cls) -> str:
        return json.dumps([
            {
                "name": "get_service_logs",
                "description": "Get aggregated log patterns and errors for a specific service. Use 'all' to get logs across all services.",
                "parameters": {"service_name": "The name of the service, or 'all'"}
            },
            {
                "name": "get_service_metrics",
                "description": "Get metric anomalies (spikes/drops) for a specific service. Use 'all' to get metrics across all services.",
                "parameters": {"service_name": "The name of the service, or 'all'"}
            },
            {
                "name": "get_service_events",
                "description": "Get Kubernetes events (crashes, restarts, scaling) for a specific service. Use 'all' to get events across all services.",
                "parameters": {"service_name": "The name of the service, or 'all'"}
            },
            {
                "name": "get_dependencies",
                "description": "Find out which downstream services a given service calls. Use 'all' to see the entire dependency graph.",
                "parameters": {"service_name": "The name of the service, or 'all'"}
            }
        ], indent=2)
