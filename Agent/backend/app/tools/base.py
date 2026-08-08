from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Dict
from datetime import datetime, timezone, timedelta
from app.models.investigation import InvestigationPlan, TimeRange
import re

class InvestigationTool(ABC):
    @abstractmethod
    async def execute(self, plan: InvestigationPlan) -> Tuple[List, Dict]:
        """Execute the tool based on the investigation plan. Returns (evidence_list, raw_query)."""
        pass
        
    def resolve_time_range(self, time_range: TimeRange) -> Tuple[datetime, datetime]:
        """
        Resolves the time_range provided by the Orchestrator into UTC start and end datetimes.
        """
        now = datetime.now(timezone.utc)
        
        if time_range.type == "absolute":
            # Assume start and end are provided in ISO format. 
            # In a real system, you'd add robust parsing here.
            try:
                start = datetime.fromisoformat(time_range.start.replace('Z', '+00:00')) if time_range.start else (now - timedelta(hours=24))
                end = datetime.fromisoformat(time_range.end.replace('Z', '+00:00')) if time_range.end else now
                return start, end
            except ValueError:
                # Fallback to 1 hour
                return now - timedelta(hours=1), now
                
        # Handle relative time
        duration_str = time_range.duration
        if not duration_str:
            duration_str = "30m" # Default fallback
            
        match = re.match(r'^(\d+)([mhdw])$', duration_str)
        if match:
            val = int(match.group(1))
            unit = match.group(2)
            if unit == 'm':
                delta = timedelta(minutes=val)
            elif unit == 'h':
                delta = timedelta(hours=val)
            elif unit == 'd':
                delta = timedelta(days=val)
            elif unit == 'w':
                delta = timedelta(weeks=val)
            else:
                delta = timedelta(minutes=30)
        else:
            delta = timedelta(minutes=30)
            
        return now - delta, now
