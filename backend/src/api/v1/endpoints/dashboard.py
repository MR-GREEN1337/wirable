"""
Dashboard endpoint (Wirable) — the list of targets the user has run, each with
its latest test score and proxy status.

Shape (per target):
  {
    "company_id", "domain", "name",
    "score", "confidence", "last_run_at",
    "proxy_status": "none" | "ready",   # ready once a proxy has been generated
    "mcp_url": str | null,
  }

Reuses the Company/Audit/MCP tables (no schema churn). The MCP row, when
present, represents the generated proxy config + hosted endpoint.
"""
import uuid as _uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.database import get_session
from ....models.client import Client
from ....models.audit import Audit
from ....models.mcp import MCP
from ....models.company import Company
from ....services import proxy_runtime

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Return ALL the authenticated user's test targets (one row per company,
    its latest run) with score + proxy status. Runs are tied to the user via
    Audit.user_id (set in run.start_run)."""
    uid = _uuid.UUID(user["sub"])

    # Every audit this user ran, newest first → keep the latest per company.
    audit_res = await db.execute(
        select(Audit)
        .where(Audit.user_id == uid)
        .order_by(Audit.created_at.desc())
    )
    latest_by_company: dict = {}
    for a in audit_res.scalars().all():
        if a.company_id not in latest_by_company:
            latest_by_company[a.company_id] = a

    if not latest_by_company:
        return {"state": "ok", "targets": []}

    targets: list[dict] = []
    for company_id, audit in latest_by_company.items():
        co = await db.get(Company, company_id)
        if not co:
            continue

        # A hosted proxy for this company (proxy clients are company-scoped).
        mcp_res = await db.execute(
            select(MCP)
            .join(Client, MCP.client_id == Client.id)
            .where(
                Client.company_id == company_id,
                MCP.pr_status.in_(("hosted", "verified")),
            )
            .order_by(MCP.created_at.desc())
        )
        mcp = mcp_res.scalars().first()

        mcp_url = None
        agent_calls = 0
        tool_count = 0
        if mcp is not None:
            meta = await proxy_runtime.get_proxy_meta(str(mcp.id))
            if meta:
                mcp_url = meta.get("mcp_url") or proxy_runtime.mcp_url_for(str(mcp.id))
                agent_calls = meta.get("agent_calls", 0)
                tool_count = meta.get("tool_count", 0)

        targets.append(
            {
                "company_id": str(co.id),
                "domain": co.domain,
                "name": co.name,
                "score": audit.score if audit.score is not None else co.score,
                "confidence": audit.confidence if audit else co.confidence,
                "last_run_at": audit.created_at.isoformat() if audit else None,
                "proxy_status": "ready" if mcp else "none",
                "mcp_url": mcp_url,
                "agent_calls": agent_calls,
                "tool_count": tool_count,
            }
        )

    return {"state": "ok", "targets": targets}
