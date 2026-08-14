"""
Per-system integration and automation settings.

Deliberately separate from /api/settings: that endpoint edits the agent
process's own connections (which OpenSearch, which model), the same for
everyone who opens it. This one edits a property of one cluster — its
notification channel, its scan cadence — and a different cluster's settings
must not be reachable, let alone editable, from the same form.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes import deps
from app.integrations.teams import ping_teams
from app.store.system_settings import validate as validate_system_settings

router = APIRouter()


class SystemSettingsPatch(BaseModel):
    values: dict = Field(default_factory=dict)


@router.get("/systems/{system_id}/integrations")
async def get_system_integrations(system_id: str, request: Request) -> dict:
    container = deps(request)
    values = await container.system_settings.get(system_id)
    return {"system_id": system_id, "values": values}


@router.put("/systems/{system_id}/integrations")
async def update_system_integrations(system_id: str, patch: SystemSettingsPatch,
                                     request: Request) -> dict:
    container = deps(request)
    try:
        cleaned = validate_system_settings(patch.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    persisted = await container.system_settings.save(system_id, cleaned)
    values = await container.system_settings.get(system_id)
    return {
        "system_id": system_id,
        "values": values,
        "persisted": persisted,
        "warning": None if persisted else (
            "Saved in memory but could not be written to OpenSearch, so it will "
            "not survive a restart."
        ),
    }


@router.post("/systems/{system_id}/integrations/test")
async def test_system_integrations(system_id: str, request: Request) -> dict:
    container = deps(request)
    values = await container.system_settings.get(system_id)
    return await ping_teams(values.get("teams_webhook_url", ""), values.get("teams_channel_name", ""))


class NotifyPayload(BaseModel):
    payload: dict = Field(..., description="The message card payload to send to Teams")

@router.post("/systems/{system_id}/integrations/notify")
async def notify_system_integrations(system_id: str, payload_data: NotifyPayload, request: Request) -> dict:
    from app.integrations.teams import notify_teams
    container = deps(request)
    values = await container.system_settings.get(system_id)
    return await notify_teams(values.get("teams_webhook_url", ""), payload_data.payload)
