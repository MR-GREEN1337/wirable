"""
Run endpoints (Wirable) — start a workflow test run and stream live progress.

A "run" tests whether an AI agent can complete real workflows on a target, then
scores it. The proxy (the fix) is generated/deployed/verified separately via
POST /run/{id}/proxy (see endpoints/proxy.py).

  POST /api/v1/run                 body {url}        -> {run_id}
  GET  /api/v1/run/{run_id}/stream                   -> SSE of run-events
  GET  /api/v1/run/{run_id}/state?cursor=N           -> JSON poll of run-events

The run_id keys both a Company/Audit row (reused tables) and the in-process SSE
bus. The SSE event vocabulary is defined in core.contracts.
"""
import json
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import verify_token
from ....core.config import settings
from ....core.ratelimit import rate_limit
from ....core.contracts import events
from ....core.database import get_session, AsyncSessionLocal
from ....models.client import Client
from ....models.company import Company
from ....models.audit import Audit
from ....services import test_service, orchestrator, github_harness_fix, entitlements

router = APIRouter(prefix="/run", tags=["run"])

# Strong refs to in-flight run tasks so the event loop doesn't GC them mid-run.
_RUN_TASKS: set = set()

# Self-ensuring column (migration-free) — mirrors auth._ensure_password_column.
# Ties each test run to the user who ran it so the dashboard can list it.
_AUDIT_USER_DDL = "ALTER TABLE audits ADD COLUMN IF NOT EXISTS user_id UUID"
_audit_user_col_ensured = False


async def _ensure_audit_user_column(db: AsyncSession) -> None:
    """Add audits.user_id if missing (idempotent, run once per process)."""
    global _audit_user_col_ensured
    if _audit_user_col_ensured:
        return
    try:
        await db.execute(text(_AUDIT_USER_DDL))
        await db.commit()
        _audit_user_col_ensured = True
    except Exception:
        await db.rollback()
        logger.exception("[run] could not ensure audits.user_id column")


class AccessGrant(BaseModel):
    """Optional pre-run credentials the user hands the agent so it can sign in
    and exercise the AUTHED product (not just the public marketing surface).

    mode = none     -> no credentials (default; public surface only)
    mode = password -> email + password login
    mode = api_key  -> an API key the product accepts
    mode = bearer   -> a bearer token
    All fields are optional; only what's provided is injected into the sandbox.
    """

    mode: str = "none"  # "none" | "password" | "api_key" | "bearer"
    email: Optional[str] = None
    password: Optional[str] = None
    api_key: Optional[str] = None
    token: Optional[str] = None
    notes: Optional[str] = None


class RunRequest(BaseModel):
    url: str
    access: Optional[AccessGrant] = None


class InputRequest(BaseModel):
    value: str


class FixRequest(BaseModel):
    repo: Optional[str] = None  # "owner/repo"; overrides the user's saved repo


def _user_from_auth(authorization: Optional[str]) -> Optional[str]:
    """Resolve the JWT `sub` (user UUID) from a Bearer Authorization header.

    Returns the user id string on a valid token, else None. Never raises — the
    caller decides what an unauthenticated request means.
    """
    if not authorization:
        return None
    tok = authorization.strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    if not tok:
        return None
    try:
        payload = verify_token(tok)
        return payload.get("sub")
    except Exception:
        return None


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


@router.post("", dependencies=[rate_limit("run", 15, 3600)])
async def start_run(
    body: RunRequest,
    bg: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
):
    """Kick off a new test run for a URL.

    Returns immediately with a run_id; the client then opens
    GET /run/{run_id}/stream to receive live SSE progress.

    LAUNCH GATING (each run burns real Claude + sandbox dollars):
      - When settings.WIRABLE_REQUIRE_AUTH (default), a valid Bearer JWT is
        required -> 401 {detail:"sign in to run"} otherwise.
      - The user's entitlement quota is checked -> 402 {detail:"run limit
        reached", upgrade:True, status:{...}} when exhausted (free tier).
      - On pass, the run is recorded against the user before it starts.
      - When WIRABLE_REQUIRE_AUTH is False, behaves as before (anonymous).
    """
    user_id = _user_from_auth(authorization)

    if settings.WIRABLE_REQUIRE_AUTH:
        if not user_id:
            raise HTTPException(status_code=401, detail="sign in to run")
        allowed, reason, status_snapshot = await entitlements.can_run(db, user_id)
        if not allowed:
            raise HTTPException(
                status_code=402,
                detail={
                    "detail": reason or "run limit reached",
                    "upgrade": True,
                    "status": status_snapshot,
                },
            )
        await entitlements.record_run(db, user_id)

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

    # Tie the run to the user so it shows on their dashboard.
    await _ensure_audit_user_column(db)
    audit = Audit(company_id=company.id)
    if user_id:
        try:
            audit.user_id = uuid.UUID(user_id)
        except (ValueError, TypeError):
            pass
    db.add(audit)
    await db.commit()
    await db.refresh(audit)

    company_id = company.id
    audit_id = audit.id

    # Normalize the optional access grant to a plain dict (or None) for threading
    # through the orchestrator → test_service → sandbox env. Default = no access.
    access = body.access.model_dump() if body.access is not None else None
    if access and (access.get("mode") or "none") == "none":
        access = None

    async def _run_and_persist():
        # Global concurrency cap (see test_service._get_run_sem): when all run
        # slots are busy the heavy sandbox fan-out inside the orchestrator will
        # WAIT on the semaphore. Surface that wait as a "queued" line up front so
        # the UI shows a queued state instead of a dead-looking pause. Best-effort
        # heuristic (sem.locked()); never blocks the run if it misreads.
        try:
            if test_service.run_slots_busy():
                await test_service.emit(
                    run_id,
                    events.line(
                        True,
                        "queued — other runs are ahead, starting shortly…",
                    ),
                )
        except Exception:
            logger.debug("[run] queued-line emit failed (non-fatal)", exc_info=True)

        agg = await orchestrator.run_workflow(run_id, body.url, access=access)
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


@router.get("/{run_id}/state")
async def run_state(run_id: str, cursor: int = 0):
    """Cursor-based poll of run-events — the SSE-free path that survives the proxy.

    The Next.js standalone proxy buffers SSE responses (events arrive in one burst
    at completion). This plain-JSON endpoint reads the same in-memory event history
    the SSE stream replays from, so the frontend can poll it every ~700ms and play
    frames live instead. Cheap, idempotent, safe to hammer.

    Query:
      cursor: number of events the client has already consumed (default 0).

    Returns:
      {events: [...events after `cursor`...], cursor: <new index>, done: bool}
    """
    evs, new_cursor, done = test_service.get_history(run_id, since=cursor)
    return {"events": evs, "cursor": new_cursor, "done": done}


@router.post("/{run_id}/input")
async def submit_input(run_id: str, body: InputRequest):
    """Human-in-the-loop: deliver a value the agent asked for mid-run.

    When the in-sandbox agent hits a wall it cannot pass alone (an OTP, a 2FA
    code, a login it lacks), it emits a `needs_input` SSE event and waits. The
    frontend collects the value and POSTs it here. We hand it to the in-process
    input bus; the run's "camera" loop picks it up on its next poll and writes it
    into the sandbox at /tmp/human_input.json, where the driver is polling for it.

    No auth — same as POST /run (this is the anonymous run flow).
    """
    test_service.set_human_input(run_id, body.value)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /run/{run_id}/fix — the REAL FIX harness (clone -> edit -> push -> PR)
# ---------------------------------------------------------------------------


async def _resolve_github(authorization: Optional[str], body_repo: Optional[str]):
    """Resolve (token, repo) for the FIX from the caller's connected GitHub.

    Mirrors proxy.py: read the Bearer token off Authorization (the same user JWT
    the run was started with), find their Client row, return the stored OAuth
    token + repo. `body_repo` overrides the saved repo. Never raises; returns
    (None, None) when GitHub isn't connected or the caller is unauthenticated.
    """
    user_id = None
    if authorization:
        tok = authorization.strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        try:
            payload = verify_token(tok)
            user_id = payload.get("sub")
        except Exception:
            user_id = None
    if not user_id:
        return None, None
    try:
        uid = uuid.UUID(str(user_id))
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Client).where(Client.user_id == uid))
            client = result.scalars().first()
        if client is None or not client.github_token:
            return None, None
        repo = body_repo or client.github_repo
        return client.github_token, repo
    except Exception:
        logger.debug("[run.fix] github resolve failed", exc_info=True)
        return None, None


def _audit_from_history(run_id: str):
    """Recover (dims, cards, target_url) from the run's SSE history. Never raises."""
    dims: list = []
    cards: list = []
    target_url = ""
    try:
        history, _cursor, _done = test_service.get_history(run_id, since=0)
    except Exception:
        history = []
    for ev in history:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "score":
            d = ev.get("dimensions")
            if isinstance(d, list):
                dims = d
        elif ev.get("type") == "cards" and isinstance(ev.get("cards"), list):
            cards = ev["cards"]
        for key in ("url", "base_url", "target"):
            v = ev.get(key)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                target_url = v.rstrip("/")
    return dims, cards, target_url


@router.post("/{run_id}/fix")
async def fix_run(
    run_id: str,
    body: FixRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Kick off the REAL FIX harness for a completed run.

    Spins a Daytona sandbox, `git clone`s the caller's connected repo (token-
    auth'd), does agentic agent-readiness work, pushes a branch, and opens a PR.
    Falls back to the REST file-drop if the harness can't push. Progress streams
    on the run's SSE bus as `line` events; it terminates with a `fix_pr` event
    the frontend already renders.

    Returns immediately ({"started": True, "repo": ...}); the work runs in the
    background. 400 if GitHub isn't connected for the caller.
    """
    from fastapi import HTTPException

    # Gate: fixing is the paid value (the audit stays free). Pro/judge only.
    _uid = None
    if authorization and authorization.lower().startswith("bearer "):
        try:
            _uid = verify_token(authorization.split(" ", 1)[1]).get("sub")
        except Exception:
            _uid = None
    if not _uid:
        raise HTTPException(status_code=401, detail="sign in")
    from ....core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as _db:
        if not await entitlements.is_pro(_db, _uid):
            raise HTTPException(
                status_code=402,
                detail={"detail": "Upgrade to Pro to open the fix PR", "upgrade": True, "reason": "pro_required"},
            )

    token, repo = await _resolve_github(authorization, body.repo)
    if not token or not repo:
        raise HTTPException(
            status_code=400,
            detail="GitHub not connected (connect a repo to open a fix PR)",
        )

    dims, cards, target_url = _audit_from_history(run_id)

    async def _fix():
        try:
            await github_harness_fix.run_harness_fix(
                run_id=run_id,
                github_token=token,
                repo_full_name=repo,
                target_url=target_url,
                audit_dims=dims,
                cards=cards,
                proxy_mcp_url="",
            )
        except Exception:
            logger.exception("[run.fix] harness task failed for %s", run_id)
            try:
                await test_service.emit(
                    run_id, events.fix_pr("", [], repo=repo, error="fix harness failed")
                )
            except Exception:
                pass

    import asyncio

    task = asyncio.create_task(_fix())
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)

    return {"started": True, "repo": repo}
