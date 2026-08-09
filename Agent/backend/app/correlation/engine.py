from typing import List
import uuid

from app.models.investigation import InvestigationPlan
from app.models.evidence import InvestigationEvidence
from app.correlation.models import CorrelatedEvidence, CorrelationGroup
from app.correlation.normalizer import EvidenceNormalizer
from app.correlation.timeline import TimelineBuilder
from app.correlation.matcher import EvidenceMatcher
from app.correlation.signals import SignalDetector

class CorrelationEngine:
    async def correlate(
        self,
        plan: InvestigationPlan,
        evidence: InvestigationEvidence
    ) -> CorrelatedEvidence:
        """
        Orchestrates the correlation process.
        """
        # 1. Normalize Evidence
        normalized_timeline = EvidenceNormalizer.normalize_all(
            application_logs=evidence.application_logs,
            kubernetes_events=evidence.kubernetes_events,
            metrics=evidence.metrics,
            system_id=plan.system_id,
            environment=plan.environment
        )
        
        # 2. Build Timeline (Chronological Sort)
        timeline = TimelineBuilder.build_timeline(normalized_timeline)
        
        # 3. Find Relationships
        relationships = EvidenceMatcher.find_relationships(timeline)
        
        # 4. Detect Signals
        signals = SignalDetector.detect_signals(timeline, evidence.metrics)
        
        # 5. Build Correlation Groups (Simple clustering by overlapping relationships for Phase 3)
        groups = self._build_correlation_groups(timeline, relationships, signals)
        
        # 6. Generate Statistics
        statistics = {
            "total_logs": len(evidence.application_logs),
            "total_events": len(evidence.kubernetes_events),
            "total_metrics_series": len(evidence.metrics),
            "total_relationships_found": len(relationships),
            "total_signals_detected": len(signals)
        }
        
        return CorrelatedEvidence(
            timeline=timeline,
            relationships=relationships,
            groups=groups,
            signals=signals,
            statistics=statistics,
            investigation_window=plan.time_range
        )
        
    def _build_correlation_groups(self, timeline, relationships, signals) -> List[CorrelationGroup]:
        """
        Creates basic groups based on closely related evidence items.
        """
        # This is a simplified group builder. 
        # In a real system, you'd use a graph algorithm (like connected components).
        groups = []
        if not timeline:
            return groups
            
        # Example: group by 60s windows containing signals or highly correlated items
        # We will just put everything in one group if it's a short window, or create a single summary group for Phase 3
        # To make it deterministic and simple:
        
        group_id = f"group-{uuid.uuid4().hex[:8]}"
        start_time = timeline[0].timestamp if timeline else None
        end_time = timeline[-1].timestamp if timeline else None
        
        evidence_ids = [e.id for e in timeline]
        signal_types = [s.type for s in signals]
        
        groups.append(CorrelationGroup(
            id=group_id,
            start_time=start_time,
            end_time=end_time,
            evidence_ids=evidence_ids,
            signals=signal_types,
            summary=f"Found {len(timeline)} related evidence items across multiple sources."
        ))
        
        return groups
