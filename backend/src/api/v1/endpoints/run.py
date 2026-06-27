"""
Run endpoints (Wirable) — start a workflow test run and stream live progress.

A "run" tests whether an AI agent can complete real workflows on a target, then
scores it. The proxy (the fix) is generated/deployed/verified separately via
POST /run/{id}/proxy (see endpoints/proxy.py).

  POST /api/v1/run                 body {url}        -> {run_id}
  GET  /api/v1/run/{run_id}/stream                   -> SSE of run-events

The run_id keys both a Company/Audit row (reused tables) and the in-process SSE
bus. The SSE event vocabulary is defined in core.contracts.
"""
import json
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_session, AsyncSessionLocal
from ....models.company import Company
from ....models.audit import Audit
from ....services import test_service, orchestrator

router = APIRouter(prefix="/run", tags=["run"])

# Strong refs to in-flight run tasks so the event loop doesn't GC them mid-run.
_RUN_TASKS: set = set()


class RunRequest(BaseModel):
    url: str


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


@router.post("")
async def start_run(
    body: RunRequest,
    bg: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
):
    """Kick off a new test run for a URL.

    Returns immediately with a run_id; the client then opens
    GET /run/{run_id}/stream to receive live SSE progress.
    """
    run_id = str(uuid.uuid4())
    domain = _normalise_domain(body.url)

    # Reuse the Company/Audit tables to anchor the run (no schema churn).
    result = await db.execute(select(Company).where(Company.domain == domain))
    company = result.scalar_one_or_none()
    if not company:
        company = Company(domain=domain)
        db.add(company)
        await db.commit()
        await db.refresh(company)

    audit = Audit(company_id=company.id)
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    company_id = company.id
    audit_id = audit.id

    async def _run_and_persist():
        agg = await orchestrator.run_workflow(run_id, body.url)
        if not agg:
            return
        # Persist the test result onto the reused Audit + Company rows.
        async with AsyncSessionLocal() as fresh_db:
            await test_service.persist_test_result(
                fresh_db, audit_id, company_id, agg
            )

    # Run on the live event loop (same loop the SSE stream observes) and start
    # immediately, not after the response. Keep a reference so it isn't GC'd.
    import asyncio
    task = asyncio.create_task(_run_and_persist())
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)

    return {
        "run_id": run_id,
        "url": body.url,
        "domain": domain,
        "audit_id": str(audit_id),
        "company_id": str(company_id),
    }


@router.get("/{run_id}/stream")
async def stream_run(run_id: str):
    """SSE stream of run-events. Closes when type='done' or type='error'."""

    async def generator():
        # Initial comment flushes response headers immediately (so EventSource
        # opens) before the first event arrives.
        yield ": connected\n\n"
        async for event in test_service.subscribe(run_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
