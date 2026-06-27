"""
Report endpoint — public, no auth required.

Returns the full audit report for a company by its UUID.
Intended to be embedded in the cold outbound email link.
"""
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_session
from ....models.audit import Audit, AuditStep
from ....models.company import Company
from ....models.mcp import MCP

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/{company_id}")
async def get_report(
    company_id: str,
    db: AsyncSession = Depends(get_session),
):
    """
    Public report for a company.

    Includes the company profile, the latest audit with per-dimension breakdown,
    and the MCP fix status if one has been opened.
    """
    # Validate UUID format
    try:
        cid = _uuid.UUID(company_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid company_id format")

    result = await db.execute(select(Company).where(Company.id == cid))
    company = result.scalar_one_or_none()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    # Latest audit
    audit_result = await db.execute(
        select(Audit)
        .where(Audit.company_id == company.id)
        .order_by(Audit.created_at.desc())
    )
    audit = audit_result.scalar_one_or_none()

    dimensions = []
    if audit:
        steps_result = await db.execute(
            select(AuditStep).where(AuditStep.audit_id == audit.id)
        )
        for s in steps_result.scalars().all():
            dimensions.append(
                {
                    "dimension": s.dimension,
                    "passed": s.passed,
                    "confidence": s.confidence,
                    "weight": s.weight,
                    "evidence": s.evidence,
                }
            )

    return {
        "id": str(company.id),
        "domain": company.domain,
        "name": company.name,
        "score": audit.score if audit else None,
        "confidence": audit.confidence if audit else None,
        "created_at": audit.created_at.isoformat() if audit else None,
        "is_post_fix": audit.is_post_fix if audit else False,
        "dimensions": dimensions,
    }
