import httpx
import logging
from datetime import datetime
from typing import Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)

class PrometheusClient:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or settings.PROMETHEUS_URL
        self.timeout = 30.0

    async def query(self, query: str) -> Dict[str, Any]:
        """
        Executes an instant query.
        """
        url = f"{self.base_url}/api/v1/query"
        params = {"query": query}
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if data.get("status") != "success":
                    raise ValueError(f"Prometheus query failed: {data.get('error')}")
                    
                return data.get("data", {})
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Prometheus: {e}")
            raise ConnectionError(f"Failed to connect to Prometheus: {e}")
        except Exception as e:
            logger.error(f"Error querying Prometheus: {e}")
            raise

    async def query_range(self, query: str, start: datetime, end: datetime, step: str) -> Dict[str, Any]:
        """
        Executes a range query.
        """
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if data.get("status") != "success":
                    raise ValueError(f"Prometheus range query failed: {data.get('error')}")
                    
                return data.get("data", {})
                
        except httpx.HTTPError as e:
            logger.error(f"HTTP error communicating with Prometheus: {e}")
            raise ConnectionError(f"Failed to connect to Prometheus: {e}")
        except Exception as e:
            logger.error(f"Error querying Prometheus range: {e}")
            raise
