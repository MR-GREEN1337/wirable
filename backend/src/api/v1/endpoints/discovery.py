"""
Discovery endpoints — drive + observe the autonomous agency loop (the scout).

POST /discovery/scout    fire one scout cycle in the background.
GET  /discovery/targets  list recent scout-sourced companies for the console UI.
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.database import get_session
from ....models.company import Company
from ....services.scout import run_scout

router = APIRouter(prefix="/discovery", tags=["discovery"])


class ScoutRequest(BaseModel):
    category: str = "developer tools"
    count: int = 3


@router.post("/scout")
async def trigger_scout(
    body: ScoutRequest,
    bg: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    """Fire one autonomous scout cycle (discover → audit → enrich → contact).

    Runs in the background so the request returns immediately; poll
    GET /discovery/targets to watch the pipeline progress.
    """
    count = max(1, min(body.count, 25))  # bound the batch defensively

    async def _run() -> None:
        from loguru import logger

        try:
            await run_scout(body.category, count)
        except Exception as exc:  # background tasks must never bubble
            logger.warning(f"[discovery] scout run failed: {exc}")

    bg.add_task(_run)
    return {"started": True, "category": body.category, "count": count}


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


@router.get("/targets")
async def list_targets(
    scout_only: bool = Query(
        False, description="When true, only companies sourced by the scout."
    ),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List recent companies in the pipeline, newest first.

    Powers the autonomous-agency console. By default returns ALL companies;
    pass ``?scout_only=true`` to restrict to scout-sourced rows.
    """
    stmt = select(Company).order_by(Company.created_at.desc()).limit(limit)
    if scout_only:
        stmt = (
            select(Company)
            .where(Company.source == "scout")
            .order_by(Company.created_at.desc())
            .limit(limit)
        )
    result = await db.execute(stmt)
    companies = result.scalars().all()

    return {
        "targets": [
            {
                "id": str(c.id),
                "domain": c.domain,
                "name": c.name,
                "score": c.score,
                "confidence": c.confidence,
                "outbound_status": c.outbound_status,
                "founder_name": c.founder_name,
                "founder_email": c.founder_email,
                "founder_title": c.founder_title,
                "enrichment_confidence": c.enrichment_confidence,
                "source": c.source,
                "reason": c.discovery_reason,
                "created_at": _iso(c.created_at),
            }
            for c in companies
        ]
    }
