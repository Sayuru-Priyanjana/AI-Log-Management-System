"""Measured context and prior-incident leads for one scoped investigation."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TopologyNode(BaseModel):
    id: str
    kind: str  # service | pod | node
    label: str


class TopologyEdge(BaseModel):
    source: str
    target: str
    relation: str  # calls | runs_on | belongs_to
    evidence_ids: list[str] = Field(default_factory=list)


class PropagationLink(BaseModel):
    upstream_signal_id: str
    downstream_signal_id: str
    upstream_service: str
    downstream_service: str
    lag_seconds: float
    assessment: str = "consistent_with_propagation"


class OperationalTopology(BaseModel):
    system_id: str
    environment: str
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)
    propagation: list[PropagationLink] = Field(default_factory=list)


class IncidentFingerprint(BaseModel):
    system_id: str
    environment: str
    affected_services: list[str] = Field(default_factory=list)
    signal_types: list[str] = Field(default_factory=list)
    sequence: list[str] = Field(default_factory=list)
    log_templates: list[str] = Field(default_factory=list)
    resource_state: dict[str, str] = Field(default_factory=dict)
    dependency_edges: list[str] = Field(default_factory=list)


class HistoricalMatch(BaseModel):
    investigation_id: str
    score: float
    factors: dict[str, float] = Field(default_factory=dict)
    headline: str = ""
    cause_category: str = ""
    root_cause_service: str | None = None
    created_at: str = ""
    note: str = "Prior conclusion; verify against current telemetry."


class HypothesisCheck(BaseModel):
    expectation: str
    outcome: str  # observed | contradicted | unknown
    evidence_ids: list[str] = Field(default_factory=list)


class HypothesisTest(BaseModel):
    candidate_id: str
    verdict: str  # keep | reject | uncertain
    checks: list[HypothesisCheck] = Field(default_factory=list)


class CausalRole(BaseModel):
    role: str  # root_cause | contributing_factor | symptom | impact | consequence
    label: str
    service: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    basis: str = ""
