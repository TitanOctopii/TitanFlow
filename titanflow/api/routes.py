"""TitanFlow API Routes — health checks, status, module control."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api", tags=["titanflow"])


def get_engine():
    """Dependency injection for the engine. Set at startup."""
    from titanflow.main import _engine
    return _engine


def require_api_key(request: Request, engine=Depends(get_engine)):
    """Verify X-API-Key header for protected routes."""
    configured_key = engine.config.api_key
    if not configured_key:
        return  # No key configured = auth disabled (dev mode)
    provided = request.headers.get("X-API-Key", "")
    if provided != configured_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "titanflow"}


@router.get("/status")
async def status(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return engine.status()


@router.get("/modules")
async def modules(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return {
        name: {
            "enabled": m.enabled,
            "description": m.description,
        }
        for name, m in engine.modules.items()
    }


@router.get("/llm/health")
async def llm_health(engine=Depends(get_engine), _=Depends(require_api_key)) -> dict[str, Any]:
    return await engine.llm.health_check()


@router.get("/jobs")
async def scheduled_jobs(engine=Depends(get_engine), _=Depends(require_api_key)) -> list[dict[str, Any]]:
    return engine.scheduler.list_jobs()
