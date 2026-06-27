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
    """Return the authenticated user's targets with score + proxy status."""
    result = await db.execute(
        select(Client).where(Client.user_id == _uuid.UUID(user["sub"]))
    )
    client = result.scalar_one_or_none()
    if not client:
        return {"state": "no_client", "targets": []}

    targets: list[dict] = []

    if client.company_id:
        co_res = await db.execute(
            select(Company).where(Company.id == client.company_id)
        )
        co = co_res.scalar_one_or_none()
        if co:
            # Latest test run for this target.
            audit_res = await db.execute(
                select(Audit)
                .where(Audit.company_id == co.id)
                .order_by(Audit.created_at.desc())
            )
            audit = audit_res.scalars().first()

            # Latest generated proxy (MCP row), if any.
            mcp_res = await db.execute(
                select(MCP)
                .where(MCP.client_id == client.id)
                .order_by(MCP.created_at.desc())
            )
            mcp = mcp_res.scalars().first()

            # Hosted proxy details (mcp_url + agent-call count) from the runtime,
            # which reads the persisted ProxyConfig + counters off the MCP row.
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
                    "score": audit.score if audit else co.score,
                    "confidence": audit.confidence if audit else co.confidence,
                    "last_run_at": (
                        audit.created_at.isoformat() if audit else None
                    ),
                    "proxy_status": "ready" if mcp else "none",
                    "mcp_url": mcp_url,
                    "agent_calls": agent_calls,
                    "tool_count": tool_count,
                }
            )

    return {"state": "ok", "targets": targets}
