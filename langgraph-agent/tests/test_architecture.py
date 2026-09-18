from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.store.architecture import Architecture, ArchitectureStore


class MemoryDocuments:
    def __init__(self):
        self.docs = {}

    async def get_document(self, index, doc_id):
        return self.docs.get((index, doc_id))

    async def index_document(self, index, document, doc_id):
        self.docs[(index, doc_id)] = document
        return {"result": "updated"}


@pytest.mark.asyncio
async def test_namespaces_round_trip_and_old_maps_remain_valid():
    store = ArchitectureStore(MemoryDocuments())
    old_map = Architecture.model_validate({"services": [{"name": "legacy", "x": 100, "y": 500}]})
    assert old_map.namespaces == []
    assert old_map.services[0].namespace is None
    map_with_lanes = Architecture.model_validate({
        "namespaces": ["production", "monitoring"],
        "services": [{"name": "api", "namespace": "production", "x": 100, "y": 640},
                     {"name": "prometheus", "namespace": "monitoring", "x": 100, "y": 850}],
        "edges": [{"source": "api", "target": "prometheus"}],
    })
    await store.save("system-a", "prod", map_with_lanes)
    await store.publish("system-a", "prod")
    published = await store.published("system-a", "prod")
    assert published.namespaces == ["production", "monitoring"]
    assert published.services[0].namespace == "production"


@pytest.mark.parametrize("payload", [
    {"namespaces": ["production", "production"]},
    {"namespaces": ["Invalid_Name"]},
    {"services": [{"name": "api", "namespace": "missing"}]},
])
def test_invalid_namespace_maps_are_rejected(payload):
    with pytest.raises(ValidationError):
        Architecture.model_validate(payload)


@pytest.mark.asyncio
async def test_draft_is_inert_until_published_and_scoped_to_environment():
    store = ArchitectureStore(MemoryDocuments())
    draft = Architecture.model_validate({
        "services": [{"name": "checkout", "x": 100, "y": 100},
                     {"name": "payment", "x": 350, "y": 100}],
        "edges": [{"source": "checkout", "target": "payment"}],
        "playbooks": [{"id": "payment-errors", "name": "Payment errors",
                       "service": "payment", "signal_types": ["ERROR_RATE_SPIKE"],
                       "checks": ["Check payment logs at onset"],
                       "metric_queries": ["payment_queue_depth"]}],
    })
    await store.save("system-a", "prod", draft)
    assert not (await store.published("system-a", "prod")).services
    await store.publish("system-a", "prod")
    assert (await store.published("system-a", "prod")).edges[0].target == "payment"
    assert not (await store.published("system-a", "stage")).services
    assert not (await store.published("system-b", "prod")).services


def test_architecture_rejects_broken_references_and_unsafe_metric_hints():
    with pytest.raises(ValidationError):
        Architecture.model_validate({"services": [{"name": "api"}],
                                     "edges": [{"source": "api", "target": "unknown"}]})
    with pytest.raises(ValidationError):
        Architecture.model_validate({"services": [{"name": "api"}],
                                     "playbooks": [{"id": "one", "name": "One",
                                                    "service": "api",
                                                    "metric_queries": ["up{system_id=~\".*\"}"]}]})
