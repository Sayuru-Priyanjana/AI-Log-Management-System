from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class IncidentControllerError(RuntimeError):
    pass


class IncidentControllerClient:
    """Talks to the incident injector running in the testbed VM.

    This exists so the browser never has to reach the VM directly. The agent
    already has network access to it (INCIDENT_CONTROLLER_URL), and proxying
    through the agent's own API means the React UI only ever talks to one
    origin — the same one that answers investigations and health checks.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.incident_controller_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str) -> dict:
        try:
            response = await self._client.request(method, path)
        except httpx.HTTPError as exc:
            raise IncidentControllerError(
                f"Cannot reach the incident controller at {self.base_url}: {exc}. "
                f"Is the testbed VM up? (vagrant status)"
            ) from exc
        if response.status_code >= 400:
            raise IncidentControllerError(
                f"Incident controller {method} {path} -> {response.status_code}: "
                f"{response.text[:300]}"
            )
        return response.json()

    async def reachable(self) -> bool:
        try:
            await self._request("GET", "/incidents")
            return True
        except IncidentControllerError:
            return False

    async def catalogue(self) -> dict:
        """Scenario definitions plus which ones are currently active."""
        return await self._request("GET", "/incidents")

    async def start(self, scenario_id: str) -> dict:
        return await self._request("POST", f"/incidents/{scenario_id}/start")

    async def stop(self, scenario_id: str) -> dict:
        return await self._request("POST", f"/incidents/{scenario_id}/stop")

    async def reset_all(self) -> dict:
        return await self._request("POST", "/incidents/reset-all")
