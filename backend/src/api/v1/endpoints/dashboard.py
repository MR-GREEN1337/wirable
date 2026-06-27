"""
Dashboard endpoint — returns the current state of the authenticated user's
audit + fix pipeline in a single response.
"""
import uuid as _uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.database import get_session
from ....models.client import Client
from ....models.audit import Audit, AuditStep
from ....models.mcp import MCP
from ....models.company import Company

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """
    Returns the full dashboard state for the authenticated user.

    Possible states:
    - "no_client"   — user hasn't gone through onboarding yet
    - "pending"     — client created, no audit run yet
    - "analyzing"   — audit in progress
    - "generating"  — fix agent running
    - "pr_open"     — PR is open, awaiting merge
    - "verified"    — post-merge audit verified the fix
    - "done"        — everything complete
    """
    # Look up client
    result = await db.execute(
        select(Client).where(Client.user_id == _uuid.UUID(user["sub"]))
    )
    client = result.scalar_one_or_none()
    if not client:
        return {"state": "no_client"}

    # Load company
    company_data = None
    if client.company_id:
        co_result = await db.execute(
            select(Company).where(Company.id == client.company_id)
        )
        co = co_result.scalar_one_or_none()
        if co:
            company_data = {
                "id": str(co.id),
                "domain": co.domain,
                "name": co.name,
                "founder_name": co.founder_name,
                "founder_email": co.founder_email,
                "outbound_status": co.outbound_status,
            }

    # Load latest audit
    audit_data = None
    if client.company_id:
        audit_result = await db.execute(
            select(Audit)
            .where(Audit.company_id == client.company_id)
            .order_by(Audit.created_at.desc())
        )
        audit = audit_result.scalar_one_or_none()
        if audit:
            steps_result = await db.execute(
                select(AuditStep).where(AuditStep.audit_id == audit.id)
            )
            steps = steps_result.scalars().all()
            audit_data = {
                "id": str(audit.id),
                "score": audit.score,
                "confidence": audit.confidence,
                "n_agents": audit.n_agents,
                "report_url": audit.report_url,
                "is_post_fix": audit.is_post_fix,
                "created_at": audit.created_at.isoformat(),
                "dimensions": [
                    {
                        "dimension": s.dimension,
                        "passed": s.passed,
                        "confidence": s.confidence,
                        "weight": s.weight,
                        "evidence": s.evidence,
                    }
                    for s in steps
                ],
            }

    # Load latest MCP record
    fix_data = None
    mcp_result = await db.execute(
        select(MCP)
        .where(MCP.client_id == client.id)
        .order_by(MCP.created_at.desc())
    )
    mcp = mcp_result.scalar_one_or_none()
    if mcp:
        fix_data = {
            "id": str(mcp.id),
            "audit_id": str(mcp.audit_id) if mcp.audit_id else None,
            "pr_url": mcp.pr_url,
            "pr_number": mcp.pr_number,
            "pr_files": [],
            "status": mcp.pr_status or "pending",
            "before_score": audit_data["score"] if audit_data else 0,
            "after_score": mcp.projected_score or 0,
            "before_dims": {s["dimension"]: {"passed": s["passed"]} for s in (audit_data.get("dimensions") or [])} if audit_data else {},
            "after_dims": (
                {d: {"passed": True, "needs_live": False} for d in (mcp.verified_dims or [])}
                | {d: {"passed": False, "needs_live": True} for d in (mcp.unverified_dims or [])}
            ),
        }

    # Attach domain to audit_data for the frontend
    if audit_data and company_data:
        audit_data["domain"] = company_data["domain"]

    return {
        "state": client.fix_status or "pending",
        "company": company_data,
        "audit": audit_data,
        "fix": fix_data,
        "github_connected": bool(client.github_token),
        "github_repo": client.github_repo,
        "recent_audits": [],
    }
