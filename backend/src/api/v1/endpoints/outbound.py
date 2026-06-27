"""
Outbound endpoint — triggers cold audit emails to prospects.
"""
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_session
from ....core.auth import get_current_user
from ....models.company import Company
from ....models.audit import Audit, AuditStep
from ....models.outbound import OutboundEmail
from ....services.outbound_service import send_audit_email

router = APIRouter(prefix="/outbound", tags=["outbound"])


class SendRequest(BaseModel):
    company_id: str


@router.post("/send")
async def trigger_outbound(
    body: SendRequest,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Trigger outbound email for a company after its audit."""
    co_result = await db.execute(select(Company).where(Company.id == uuid.UUID(body.company_id)))
    company = co_result.scalar_one_or_none()
    if not company:
        raise HTTPException(404, "Company not found")
    if not company.founder_email:
        raise HTTPException(400, "No founder email on file for this company")

    # Get latest audit
    audit_result = await db.execute(
        select(Audit).where(Audit.company_id == company.id).order_by(Audit.created_at.desc())
    )
    audit = audit_result.scalar_one_or_none()
    if not audit or not audit.score:
        raise HTTPException(400, "No completed audit for this company")

    # Get worst-performing dimension as the hook
    steps_result = await db.execute(select(AuditStep).where(AuditStep.audit_id == audit.id))
    steps = steps_result.scalars().all()
    failing = [s for s in steps if not s.passed]
    top_fail = failing[0] if failing else None

    report_url = f"{os.environ.get('REPORT_BASE_URL', 'http://localhost:3000')}/report/{company.id}"

    # Mint the tracking token up front so the embedded pixel matches the row we persist.
    token = uuid.uuid4().hex

    sent, subject, body = await send_audit_email(
        to_email=company.founder_email,
        to_name=company.founder_name or "",
        domain=company.domain,
        score=audit.score,
        report_url=report_url,
        failing_dim=top_fail.dimension if top_fail else "multiple dimensions",
        evidence=(top_fail.evidence or {}).get("message", "—") if top_fail else "—",
        token=token,
    )

    if sent:
        # Log the outbound attempt with the real rendered body + tracking token.
        email_log = OutboundEmail(
            id=uuid.uuid4(),
            company_id=company.id,
            subject=subject,
            body=body,
            report_url=report_url,
            token=token,
        )
        db.add(email_log)
        company.outbound_status = "sent"
        await db.commit()

    return {"sent": sent, "to": company.founder_email, "score": audit.score}
