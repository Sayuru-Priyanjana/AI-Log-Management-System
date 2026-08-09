from typing import List, Dict, Any
from app.correlation.models import TimelineEvidence, OperationalSignal
from app.correlation.config import correlation_settings
from app.models.evidence import MetricEvidence

class SignalDetector:
    @staticmethod
    def detect_signals(timeline: List[TimelineEvidence], metrics: List[MetricEvidence] = None) -> List[OperationalSignal]:
        signals = []
        
        signals.extend(SignalDetector._detect_error_bursts(timeline))
        signals.extend(SignalDetector._detect_kubernetes_signals(timeline))
        if metrics:
            signals.extend(SignalDetector._detect_metric_spikes(metrics))
            
        # Add basic POD_RESTART signal detection if restart metric or specific kubernetes event exists
        
        # Sort signals by timestamp
        return sorted(signals, key=lambda x: x.timestamp)

    @staticmethod
    def _detect_error_bursts(timeline: List[TimelineEvidence]) -> List[OperationalSignal]:
        signals = []
        error_logs = [e for e in timeline if e.source_type == "application_log" and e.severity in ("ERROR", "CRITICAL", "FATAL")]
        
        if not error_logs:
            return signals

        # Group by service to detect bursts per service
        services = set(e.service_name for e in error_logs if e.service_name)
        if not services:
            services = {None}

        for service in services:
            svc_errors = [e for e in error_logs if e.service_name == service]
            
            i = 0
            while i < len(svc_errors):
                start_log = svc_errors[i]
                window_end = start_log.timestamp.timestamp() + correlation_settings.ERROR_BURST_WINDOW_SECONDS
                
                burst_group = [start_log]
                j = i + 1
                while j < len(svc_errors) and svc_errors[j].timestamp.timestamp() <= window_end:
                    burst_group.append(svc_errors[j])
                    j += 1
                
                if len(burst_group) >= correlation_settings.ERROR_BURST_THRESHOLD:
                    signals.append(OperationalSignal(
                        type="ERROR_BURST",
                        severity="high",
                        timestamp=start_log.timestamp,
                        service=service,
                        count=len(burst_group),
                        window_seconds=correlation_settings.ERROR_BURST_WINDOW_SECONDS
                    ))
                    i = j # Skip past the burst
                else:
                    i += 1
                    
        return signals

    @staticmethod
    def _detect_kubernetes_signals(timeline: List[TimelineEvidence]) -> List[OperationalSignal]:
        signals = []
        for e in timeline:
            if e.source_type == "kubernetes_event":
                action_or_reason = (e.event_action or "").lower()
                
                if "backoff" in action_or_reason:
                    signals.append(OperationalSignal(
                        type="KUBERNETES_BACKOFF",
                        severity="high",
                        timestamp=e.timestamp,
                        pod=e.pod_name,
                        details={"message": e.message}
                    ))
                elif "oomkilled" in action_or_reason or "oom" in action_or_reason:
                    signals.append(OperationalSignal(
                        type="KUBERNETES_OOM",
                        severity="high",
                        timestamp=e.timestamp,
                        pod=e.pod_name,
                        details={"message": e.message}
                    ))
        return signals

    @staticmethod
    def _detect_metric_spikes(metrics: List[MetricEvidence]) -> List[OperationalSignal]:
        signals = []
        for metric in metrics:
            if metric.status != "success" or not metric.samples:
                continue
                
            name = metric.metric_name.lower()
            if "cpu" in name or "memory" in name:
                # Basic baseline calculation: average of first half vs max of second half, or just overall max vs threshold
                # For Phase 3, we keep it simple: if peak > threshold, signal
                values = [s.value for s in metric.samples]
                peak = max(values)
                baseline = sum(values) / len(values)
                increase = peak - baseline
                
                is_cpu = "cpu" in name
                is_mem = "memory" in name
                
                threshold = correlation_settings.CPU_SPIKE_PERCENT if is_cpu else correlation_settings.MEMORY_SPIKE_PERCENT
                
                # Assuming metric is percentage or normalized. If it's absolute cores/bytes, threshold comparison needs adjustment.
                # Here we just check if it's over the configured percent if it's already a percentage, or just flag significant increases.
                # This logic is simplified for Phase 3.
                if peak > threshold and increase > (baseline * 0.2): # Peak is high and at least 20% increase
                    signal_type = "CPU_SPIKE" if is_cpu else "MEMORY_SPIKE"
                    signals.append(OperationalSignal(
                        type=signal_type,
                        severity="medium",
                        timestamp=metric.samples[-1].timestamp, # approximate time of peak or end of window
                        pod=metric.labels.get("pod"),
                        baseline=baseline,
                        peak=peak,
                        increase=increase
                    ))
                    
            if "restart" in name:
                if metric.summary and metric.summary.increase and metric.summary.increase > 0:
                    signals.append(OperationalSignal(
                        type="POD_RESTART",
                        severity="high",
                        timestamp=metric.samples[-1].timestamp,
                        pod=metric.labels.get("pod"),
                        increase=metric.summary.increase,
                        baseline=metric.summary.initial,
                        peak=metric.summary.final
                    ))
                    
        return signals
