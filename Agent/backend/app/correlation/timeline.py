from typing import List
from app.correlation.models import TimelineEvidence

class TimelineBuilder:
    @staticmethod
    def build_timeline(evidence: List[TimelineEvidence]) -> List[TimelineEvidence]:
        """
        Sorts the normalized evidence chronologically.
        """
        return sorted(evidence, key=lambda x: x.timestamp)
