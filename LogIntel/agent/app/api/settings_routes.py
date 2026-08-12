"""
The connection surface, editable from the UI.

Everything here answers *where*, never *how*: which OpenSearch, which model,
which time zone. Thresholds and window arithmetic are deliberately absent —
those are what the tests pin down, and a text box that quietly changed what
counts as an incident would make every stored investigation incomparable with
the next.

Kept in its own module rather than appended to the investigation routes: these
endpoints mutate the process, the others read from it, and mixing the two makes
it harder to see which is which.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes import deps
from app.config import settings
from app.llm.factory import describe_endpoint, describe_model, describe_provider
from app.store.runtime_config import validate as validate_setting
from app.util.timefmt import label as zone_label

logger = logging.getLogger(__name__)
router = APIRouter()

GROUPS = [
    {"id": "opensearch", "label": "Log storage",
     "description": "Where logs, Kubernetes events and stored investigations live."},
    {"id": "prometheus", "label": "Metrics and testbed",
     "description": "Queried live for every investigation, never mirrored."},
    {"id": "model", "label": "Model",
     "description": "Local by default. A hosted model writes better prose; it does "
                    "not change what the evidence says."},
    {"id": "display", "label": "Display",
     "description": "Presentation only. Everything is stored and compared in UTC."},
]


class SettingsPatch(BaseModel):
    # A null value clears the override and falls back to the environment, which
    # is a different intent from setting the field to an empty string.
    values: dict = Field(default_factory=dict)


def _state(request: Request) -> dict:
    container = deps(request)
    return {
        "fields": container.config.describe(),
        "groups": GROUPS,
        "persisted": container.config.persisted,
        "timezone": {"value": settings.display_timezone, "label": zone_label()},
    }


@router.get("/settings")
async def get_settings(request: Request) -> dict:
    return _state(request)


@router.put("/settings")
async def update_settings(patch: SettingsPatch, request: Request) -> dict:
    container = deps(request)

    # Validate the whole patch before applying any of it. A half-applied change
    # leaves the agent pointing at one new endpoint and one old one, which is
    # harder to reason about than a rejection.
    cleaned: dict = {}
    for name, value in patch.values.items():
        try:
            cleaned[name] = None if value is None else validate_setting(name, value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    changed = container.config.apply(cleaned)
    persisted = await container.config.save()

    if container.config.needs_rebuild(changed):
        # Imported here, not at module scope: main imports this router, so a
        # top-level import would close the cycle and the order of the two would
        # decide whether the process starts.
        from app.main import rebuild_dependencies

        await rebuild_dependencies(request.app)

    result = _state(request)
    result["changed"] = changed
    result["warning"] = None if persisted else (
        "The change is in force but could not be written to OpenSearch, so it will "
        "not survive a restart. Fix the log storage connection and save again."
    )
    return result


@router.post("/settings/test")
async def test_connection(payload: dict, request: Request) -> dict:
    """Tries a connection and reports what happened.

    Worth its own endpoint: the alternative is saving a wrong URL to discover it
    is wrong, and the wrong URL may be the one that stores the settings.
    """
    container = deps(request)
    target = str(payload.get("target") or "").strip()

    try:
        if target == "opensearch":
            info = await container.opensearch.ping()
            version = (info.get("version") or {}).get("number")
            return {"ok": True,
                    "detail": f"OpenSearch {version} at {container.opensearch.describe()}"}

        if target == "prometheus":
            ready = await container.prometheus.ready()
            state = "answered" if ready else "did not report ready"
            return {"ok": ready, "detail": f"{container.prometheus.base_url} {state}"}

        if target == "model":
            available = await container.llm.available()
            detail = (f"{describe_model(container.llm)} via "
                      f"{describe_provider(container.llm)} at "
                      f"{describe_endpoint(container.llm)}")
            if not available:
                detail += " - not reachable, or the model is absent from that endpoint"
            return {"ok": available, "detail": detail}

        if target == "incidents":
            reachable = await container.incidents.reachable()
            return {"ok": reachable, "detail": container.incidents.base_url}
    except Exception as exc:                # noqa: BLE001 - the failure is the answer
        return {"ok": False, "detail": str(exc)[:400]}

    raise HTTPException(status_code=400, detail=f"unknown target: {target}")


@router.get("/clusters")
async def list_clusters(request: Request) -> dict:
    """The clusters shipping data in, and what a new one has to send.

    Nothing is connected from this end: a cluster joins by writing documents, and
    appears here once it has. So this reports what arrived and states the field
    contract that makes a new one usable, rather than offering a registration
    button that would misrepresent where the coupling actually is.
    """
    container = deps(request)
    try:
        systems = await container.registry.all()
    except Exception as exc:                # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"registry unavailable: {exc}") from exc

    return {
        "clusters": [
            {
                "id": system.id,
                "name": system.name,
                "environments": system.environments,
                "namespaces": system.namespaces,
                "services": [{"name": service.name, "log_count": service.log_count}
                             for service in system.services],
                "discovered_at": system.discovered_at.isoformat(),
            }
            for system in systems
        ],
        "ingest": {
            "opensearch_url": settings.opensearch_url,
            "log_index": settings.opensearch_log_index,
            "event_index": settings.opensearch_event_index,
            # The fields the pipeline filters on. A cluster that ships logs
            # without these is visible in Discover and invisible to the agent,
            # which looks exactly like a healthy system with no logs.
            "required_fields": [
                {"field": "@timestamp", "note": "RFC3339, UTC"},
                {"field": "system.id",
                 "note": "keyword - identifies the cluster, and becomes the system "
                         "you investigate"},
                {"field": "service.name", "note": "keyword - one per workload"},
                {"field": "environment", "note": "keyword - staging, production, ..."},
                {"field": "level", "note": "keyword - ERROR / WARN / INFO"},
                {"field": "message", "note": "text"},
                {"field": "dependency.name",
                 "note": "keyword, optional - the call graph is built from this, and "
                         "root-cause depth with it"},
            ],
        },
    }
