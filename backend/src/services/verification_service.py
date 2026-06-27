"""
Verification service — the before/after re-test that EARNS the moat.

# TODO(Wave2): REWORK to emit the canonical `verify` run-event (core.contracts)
#   and to re-test THROUGH the generated proxy (not just re-run the raw target).
#   Currently orphaned: its old caller (the /fix endpoint) was removed in Wave 1.
#   Kept as the reference implementation for the real verify phase.

After a proxy is deployed, we re-run the test engine against the target and
persist the result as a NEW Audit row with is_post_fix=True (no new table — we
reuse the Audit model). The "before" score is the latest is_post_fix=False run;
the "after" score is this new is_post_fix=True run.

SSE: verification streams over the SAME bus (test_service.emit / subscribe).
"""
import uuid
from datetime import datetime

from sqlalchemy import select

from ..core.database import AsyncSessionLocal
from ..models.audit import Audit, AuditStep
from ..models.client import Client
from ..models.company import Company
from ..models.mcp import MCP
from ..services import test_service as audit_service


async def run_verification(company_id: str, domain: str, job_id: str) -> dict:
    """Re-run the audit engine and persist a post-fix Audit; flip statuses.

    Emits on the audit SSE bus (so /audit/{job_id}/stream works), then a final
    "verification" summary event. Returns {before_score, after_score, delta}.
    """
    company_uuid = uuid.UUID(company_id)

    # Capture the "before" score = latest pre-fix audit, before we run again.
    async with AsyncSessionLocal() as db:
        before_res = await db.execute(
            select(Audit)
            .where(Audit.company_id == company_uuid, Audit.is_post_fix.is_(False))
            .order_by(Audit.created_at.desc())
        )
        before_audit = before_res.scalars().first()
        before_score = (before_audit.score if before_audit else None) or 0

    # Run the SAME audit engine; it streams "line"/"score" on job_id. We
    # suppress its terminal "done" (emit_done=False) so we can append our
    # "verification" summary and emit the terminal "done" ourselves — otherwise
    # subscribers would close on the audit's done and miss the summary.
    agg = await audit_service.run_audit(
        domain, job_id, report_id=company_id, emit_done=False
    )

    after_score = agg.get("score", 0)

    # Persist the post-fix audit + steps, update company, flip client/MCP status.
    async with AsyncSessionLocal() as db:
        post_audit = Audit(
            id=uuid.uuid4(),
            company_id=company_uuid,
            score=after_score,
            confidence=agg.get("confidence"),
            is_post_fix=True,
        )
        db.add(post_audit)
        await db.flush()  # assign post_audit.id for the steps' FK

        for dim, v in agg.get("dimensions", {}).items():
            db.add(AuditStep(
                id=uuid.uuid4(),
                audit_id=post_audit.id,
                dimension=dim,
                passed=v.get("passed", False),
                confidence=v.get("confidence", 0.0),
                weight=v.get("weight"),
                evidence={"message": v.get("evidence", "")},
                agent_votes=None,
            ))

        company = await db.get(Company, company_uuid)
        if company:
            company.score = after_score
            company.confidence = agg.get("confidence")
            company.last_audited_at = datetime.utcnow()

        # Flip the owning client's fix_status -> done, and mark MCP verified.
        client_res = await db.execute(
            select(Client).where(Client.company_id == company_uuid)
        )
        client = client_res.scalars().first()
        if client:
            client.fix_status = "done"
            mcp_res = await db.execute(
                select(MCP)
                .where(MCP.client_id == client.id)
                .order_by(MCP.created_at.desc())
            )
            mcp = mcp_res.scalars().first()
            if mcp:
                mcp.pr_status = "verified"

        await db.commit()

    delta = after_score - before_score
    # Summary first, then the terminal "done" (which closes the SSE stream).
    await audit_service.emit(job_id, {
        "type": "verification",
        "before_score": before_score,
        "after_score": after_score,
        "delta": delta,
        "report_id": company_id,
    })
    await audit_service.emit(job_id, {
        "type": "done",
        "score": after_score,
        "confidence": agg.get("confidence"),
        "report_id": company_id,
        "before_score": before_score,
        "after_score": after_score,
        "delta": delta,
    })
    return {"before_score": before_score, "after_score": after_score, "delta": delta}
