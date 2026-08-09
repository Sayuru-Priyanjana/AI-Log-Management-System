from typing import List, Tuple
from app.correlation.models import TimelineEvidence, EvidenceRelationship
from app.correlation.config import correlation_settings

class EvidenceMatcher:
    @staticmethod
    def calculate_correlation_score(a: TimelineEvidence, b: TimelineEvidence) -> Tuple[float, List[str]]:
        """
        Calculates a deterministic correlation score between two pieces of evidence.
        Returns the score and the reasons for the score.
        """
        score = 0.0
        reasons = []

        # Infrastructure scoring (strongest to weakest)
        if a.pod_name and b.pod_name and a.pod_name == b.pod_name:
            if a.container_name and b.container_name and a.container_name == b.container_name:
                score += 5.0
                reasons.append("same_container")
            else:
                score += 5.0
                reasons.append("same_pod")
        elif a.service_name and b.service_name and a.service_name == b.service_name:
            score += 4.0
            reasons.append("same_service")
        elif a.namespace and b.namespace and a.namespace == b.namespace:
            score += 2.0
            reasons.append("same_namespace")
        elif a.node_name and b.node_name and a.node_name == b.node_name:
            score += 1.0
            reasons.append("same_node")
            
        # Temporal scoring
        time_delta = abs((a.timestamp - b.timestamp).total_seconds())
        
        if time_delta <= 5:
            score += 5.0
            reasons.append("within_5_seconds")
        elif time_delta <= 15:
            score += 3.0
            reasons.append("within_15_seconds")
        elif time_delta <= correlation_settings.CORRELATION_TIME_WINDOW_SECONDS:
            score += 1.0
            reasons.append(f"within_{correlation_settings.CORRELATION_TIME_WINDOW_SECONDS}_seconds")
            
        return score, reasons

    @staticmethod
    def find_relationships(timeline: List[TimelineEvidence]) -> List[EvidenceRelationship]:
        """
        Finds temporal and infrastructure relationships between timeline items.
        """
        relationships = []
        n = len(timeline)
        
        for i in range(n):
            a = timeline[i]
            
            # Since timeline is sorted, we only need to look forward up to the time window
            for j in range(i + 1, n):
                b = timeline[j]
                time_delta = (b.timestamp - a.timestamp).total_seconds()
                
                if time_delta > correlation_settings.CORRELATION_TIME_WINDOW_SECONDS:
                    break  # No more items within the correlation window
                    
                score, reasons = EvidenceMatcher.calculate_correlation_score(a, b)
                
                # Only create relationship if there's a meaningful score (e.g., > 0)
                if score > 0:
                    rel_type = "infrastructure" if any(r.startswith("same_") for r in reasons) else "temporal"
                    relationships.append(EvidenceRelationship(
                        source_id=a.id,
                        target_id=b.id,
                        relationship_type=rel_type,
                        score=score,
                        time_delta_seconds=time_delta,
                        reasons=reasons
                    ))
                    
        return relationships
