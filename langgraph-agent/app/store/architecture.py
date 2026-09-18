"""Admin authored expectations, kept separate from measured incident evidence."""
from __future__ import annotations

import re
from pydantic import BaseModel, Field, model_validator

from app.sources.opensearch import OpenSearchClient

INDEX = "logintel-architecture"


class ServiceNode(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    x: float = Field(default=100, ge=0, le=2000)
    y: float = Field(default=100, ge=0, le=20000)
    namespace: str | None = None


class ServiceEdge(BaseModel):
    source: str
    target: str
    kind: str = Field(default="calls", pattern="^(calls|depends_on)$")


class Playbook(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    service: str
    signal_types: list[str] = Field(default_factory=list, max_length=20)
    checks: list[str] = Field(default_factory=list, max_length=20)
    metric_queries: list[str] = Field(default_factory=list, max_length=10)


class Architecture(BaseModel):
    namespaces: list[str] = Field(default_factory=list, max_length=30)
    services: list[ServiceNode] = Field(default_factory=list, max_length=100)
    edges: list[ServiceEdge] = Field(default_factory=list, max_length=200)
    playbooks: list[Playbook] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.namespaces) != len(set(self.namespaces)):
            raise ValueError("namespace names must be unique")
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name)
               for name in self.namespaces):
            raise ValueError("namespace names must be Kubernetes DNS labels")
        names = [service.name for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("service names must be unique")
        for service in self.services:
            if service.namespace and service.namespace not in self.namespaces:
                raise ValueError(f"service {service.name} references a missing namespace")
        ids = [playbook.id for playbook in self.playbooks]
        if len(ids) != len(set(ids)):
            raise ValueError("playbook ids must be unique")
        for edge in self.edges:
            if edge.source not in names or edge.target not in names:
                raise ValueError("an edge references a service missing from the map")
            if edge.source == edge.target:
                raise ValueError("a service cannot depend on itself")
        for playbook in self.playbooks:
            if playbook.service not in names:
                raise ValueError(f"playbook {playbook.id} references a missing service")
            if any(len(check) > 500 for check in playbook.checks):
                raise ValueError("playbook checks must be 500 characters or shorter")
            if any(not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]{0,199}", metric)
                   for metric in playbook.metric_queries):
                raise ValueError("metric hints must be Prometheus metric names")
        return self


class ArchitectureStore:
    def __init__(self, client: OpenSearchClient) -> None:
        self._client = client

    @staticmethod
    def _id(system_id: str, environment: str) -> str:
        return f"{system_id}:{environment}"

    async def get(self, system_id: str, environment: str) -> dict:
        raw = await self._client.get_document(INDEX, self._id(system_id, environment)) or {}
        return {
            "system_id": system_id,
            "environment": environment,
            "revision": raw.get("revision", 0),
            "published_revision": raw.get("published_revision", 0),
            "draft": Architecture.model_validate(raw.get("draft") or {}).model_dump(),
            "published": Architecture.model_validate(raw.get("published") or {}).model_dump(),
        }

    async def save(self, system_id: str, environment: str, draft: Architecture) -> dict:
        current = await self.get(system_id, environment)
        current["revision"] += 1
        current["draft"] = draft.model_dump()
        await self._client.index_document(INDEX, current, doc_id=self._id(system_id, environment))
        return current

    async def publish(self, system_id: str, environment: str) -> dict:
        current = await self.get(system_id, environment)
        current["published"] = current["draft"]
        current["published_revision"] = current["revision"]
        await self._client.index_document(INDEX, current, doc_id=self._id(system_id, environment))
        return current

    async def published(self, system_id: str, environment: str) -> Architecture:
        current = await self.get(system_id, environment)
        return Architecture.model_validate(current["published"])
