"""
Audit endpoints — request an audit and stream live progress via SSE.
"""
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_session, AsyncSessionLocal
from ....models.company import Company
from ....models.audit import Audit
from ....services import audit_service
from ....services.outbound_service import send_audit_email_for_company

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditRequest(BaseModel):
    domain: str


def _normalise_domain(raw: str) -> str:
    """Strip scheme + path, lowercase."""
    return (
        raw.lower()
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .split("/")[0]
        .split("?")[0]
    )


@router.post("/request")
async def request_audit(
    body: AuditRequest,
    bg: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
):
    """
    Kick off a new audit for a domain.

    Returns immediately with a job_id; the client should then open
    GET /audit/{job_id}/stream to receive live SSE progress.
    """
    job_id = str(uuid.uuid4())
    domain = _normalise_domain(body.domain)

    # Ensure a Company row exists (or create one) so we have a stable FK
    result = await db.execute(select(Company).where(Company.domain == domain))
    company = result.scalar_one_or_none()
    if not company:
        company = Company(domain=domain)
        db.add(company)
        await db.commit()
        await db.refresh(company)

    # Pre-create an Audit row so the report endpoint works immediately
    audit = Audit(company_id=company.id)
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    async def _run_and_persist():
        # report_id = company.id so the final SSE "score"/"done" events tell the
        # landing page where the report lives (the report endpoint is keyed by
        # company id).
        agg = await audit_service.run_audit(domain, job_id, report_id=str(company.id))

        # Persist results using a fresh session (avoids SQLAlchemy 2.x bind issues).
        # The persistence + outbound-send logic is shared with the autonomous
        # scout (audit_service.persist_audit_result / outbound_service.
        # send_audit_email_for_company) so the two paths can never diverge.
        async with AsyncSessionLocal() as fresh_db:
            co = await audit_service.persist_audit_result(
                fresh_db, audit.id, company.id, agg
            )

            # --- Outbound auto-trigger ----------------------------------------
            # If we have a founder email on file, fire a cold audit email with
            # the worst failing dimension as the hook + a tracking pixel, and
            # persist the OutboundEmail row. When no email is on file we skip
            # silently (honest — no email theater).
            if co and co.founder_email:
                try:
                    await send_audit_email_for_company(fresh_db, co, agg)
                except Exception as exc:  # never let outbound break the audit
                    from loguru import logger
                    logger.warning(f"[outbound] auto-trigger failed: {exc}")

    bg.add_task(_run_and_persist)

    return {
        "job_id": job_id,
        "domain": domain,
        "audit_id": str(audit.id),
        "company_id": str(company.id),
    }


@router.get("/{job_id}/stream")
async def stream_audit(job_id: str):
    """SSE stream for audit progress. Closes when type='done' or type='error'."""

    async def generator():
        async for event in audit_service.subscribe(job_id):
            yield {"data": json.dumps(event)}

    return EventSourceResponse(generator())
