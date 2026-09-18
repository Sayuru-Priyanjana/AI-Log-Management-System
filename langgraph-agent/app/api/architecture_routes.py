"""The gateway restricts these editing routes to administrators."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.routes import deps
from app.store.architecture import Architecture

router = APIRouter()


@router.get("/systems/{system_id}/architecture")
async def get_architecture(system_id: str, environment: str, request: Request) -> dict:
    return await deps(request).architecture.get(system_id, environment)


@router.put("/systems/{system_id}/architecture")
async def save_architecture(system_id: str, environment: str, draft: Architecture,
                            request: Request) -> dict:
    return await deps(request).architecture.save(system_id, environment, draft)


@router.post("/systems/{system_id}/architecture/publish")
async def publish_architecture(system_id: str, environment: str, request: Request) -> dict:
    return await deps(request).architecture.publish(system_id, environment)
