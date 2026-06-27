"""
Fix endpoints — start a code-fix run and stream live progress via SSE.
"""
import json
import uuid
import uuid as _uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ....core.auth import get_current_user
from ....core.database import get_session
from ....models.audit import Audit, AuditStep
from ....models.client import Client
from ....models.company import Company
from ....services import fix_service, verification_service

router = APIRouter(prefix="/fix", tags=["fix"])


class FixRequest(BaseModel):
    repo: str  # "owner/repo"


class VerifyRequest(BaseModel):
    pass  # uses the authenticated user's client -> company


@router.post("/start")
async def start_fix(
    body: FixRequest,
    bg: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """
    Start the fix agent for the authenticated user's connected repo.

    Returns a job_id; the client opens GET /fix/{job_id}/stream for SSE.
    """
    # Look up the client record for this user
    result = await db.execute(
        select(Client).where(Client.user_id == _uuid.UUID(user["sub"]))
    )
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Connect GitHub first (/github/connect)")

    if not client.github_token:
        raise HTTPException(status_code=400, detail="GitHub token missing — reconnect via /github/connect")

    # Get the most recent audit for this client's company
    before_dims: dict = {}
    before_score: int = 0
    audit_id: str | None = None

    if client.company_id:
        audit_result = await db.execute(
            select(Audit)
            .where(Audit.company_id == client.company_id)
            .order_by(Audit.created_at.desc())
        )
        audit = audit_result.scalars().first()

        if audit:
            audit_id = str(audit.id)
            steps_result = await db.execute(
                select(AuditStep).where(AuditStep.audit_id == audit.id)
            )
            steps = steps_result.scalars().all()
            before_dims = {
                s.dimension: {
                    "passed": s.passed,
                    "confidence": s.confidence,
                    "evidence": (s.evidence or {}).get("message", ""),
                }
                for s in steps
            }
            before_score = audit.score or 0

    job_id = str(uuid.uuid4())
    domain = ""
    if client.company_id:
        from ....models.company import Company
        co_result = await db.execute(
            select(Company).where(Company.id == client.company_id)
        )
        co = co_result.scalar_one_or_none()
        domain = co.domain if co else ""

    bg.add_task(
        fix_service.run_fix,
        str(client.id),
        body.repo,
        domain,
        client.github_token,
        before_dims,
        before_score,
        job_id,
        audit_id,
    )

    return {"job_id": job_id}


@router.post("/verify")
async def verify_fix(
    body: VerifyRequest,
    bg: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Kick off the post-fix verification re-audit for the user's company.

    Returns a job_id. The verification service emits over the AUDIT SSE bus, so
    the frontend streams it via GET /audit/{job_id}/stream (NOT a fix stream) —
    reusing the audit terminal. The final event is type='verification' followed
    by the terminal type='done' carrying before_score/after_score/delta.
    """
    result = await db.execute(
        select(Client).where(Client.user_id == uuid.UUID(user["sub"]))
    )
    client = result.scalar_one_or_none()
    if not client or not client.company_id:
        raise HTTPException(
            status_code=404,
            detail="No claimed company — claim one via /onboarding/claim first",
        )

    co_res = await db.execute(select(Company).where(Company.id == client.company_id))
    company = co_res.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    job_id = str(uuid.uuid4())
    bg.add_task(
        verification_service.run_verification,
        str(company.id),
        company.domain,
        job_id,
    )
    return {"job_id": job_id}


@router.get("/{job_id}/stream")
async def stream_fix(job_id: str):
    """SSE stream for fix-agent progress. Closes on type='done' or type='error'."""

    async def generator():
        async for event in fix_service.subscribe_fix(job_id):
            yield {"data": json.dumps(event)}

    return EventSourceResponse(generator())
