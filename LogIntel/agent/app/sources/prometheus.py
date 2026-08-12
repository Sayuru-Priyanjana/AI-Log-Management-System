from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.models.domain import TimeWindow

logger = logging.getLogger(__name__)


class PrometheusError(RuntimeError):
    pass


class PrometheusClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=settings.prometheus_timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict) -> dict:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Prometheus unreachable at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise PrometheusError(f"Prometheus {path} -> {response.status_code}: {response.text[:300]}")
        payload = response.json()
        if payload.get("status") != "success":
            raise PrometheusError(f"Prometheus query failed: {payload.get('error')}")
        return payload.get("data", {})

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/-/ready")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def query(self, expression: str, moment: datetime | None = None) -> list[dict]:
        params = {"query": expression}
        if moment is not None:
            params["time"] = str(moment.timestamp())
        data = await self._get("/api/v1/query", params)
        return data.get("result", [])

    async def query_range(self, expression: str, window: TimeWindow, step: str) -> list[dict]:
        data = await self._get("/api/v1/query_range", {
            "query": expression,
            "start": str(window.start.timestamp()),
            "end": str(window.end.timestamp()),
            "step": step,
        })
        return data.get("result", [])

    @staticmethod
    def to_points(series: dict) -> list[tuple[datetime, float]]:
        """Prometheus sample pairs -> UTC-aware points.

        `datetime.fromtimestamp` without a tzinfo yields local time, which would
        offset every metric against the logs it is meant to line up with.
        """
        points: list[tuple[datetime, float]] = []
        for raw_ts, raw_value in series.get("values", []):
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue   # Prometheus reports gaps as "NaN"
            if value != value:
                continue
            points.append((datetime.fromtimestamp(float(raw_ts), tz=timezone.utc), value))
        return points

    @staticmethod
    def step_for(window: TimeWindow, target_points: int = 60) -> str:
        """Chooses a resolution that yields roughly `target_points` samples.

        A fixed step either starves a 15-minute window or returns thousands of
        points for a 12-hour one.
        """
        step = max(15, int(window.seconds / max(target_points, 1)))
        step = min(step, 3600)
        return f"{step}s"


def label_selector(**pairs: str | list[str] | None) -> str:
    """Builds a PromQL label selector from Python values.

    Queries are always assembled here from validated inputs; no expression is
    ever taken from a model or from user text.
    """
    parts: list[str] = []
    for key, value in pairs.items():
        if value is None or value == [] or value == "":
            continue
        key = key.rstrip("_")   # allows container_= to avoid keyword clashes
        if isinstance(value, list):
            escaped = "|".join(_escape(v) for v in value)
            parts.append(f'{key}=~"{escaped}"')
        else:
            parts.append(f'{key}="{_escape(value)}"')
    return ",".join(parts)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
