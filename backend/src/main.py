import asyncio
import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .api.v1.router import router
from .core.config import settings

app = FastAPI(
    title="AgentReady API",
    version="0.1.0",
    description=(
        "Agent-readiness audit + MCP fix pipeline. "
        "Runs N parallel Daytona sandboxes, aggregates with CATTS, "
        "and opens a GitHub PR with the generated MCP server."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://agentready.verdit.io",
        "https://agentready.dev",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


# ---------------------------------------------------------------------------
# Autonomous scout — background loop (opt-in via SCOUT_ENABLED).
#
# When enabled, runs one scout cycle (discover → audit → enrich → contact) every
# SCOUT_INTERVAL_MINUTES with a batch of SCOUT_BATCH_SIZE. Fully isolated: any
# scout failure is caught + logged so it can never crash the app. Disabled by
# default so it's strictly opt-in.
# ---------------------------------------------------------------------------

async def _scout_loop() -> None:
    from .services.scout import run_scout

    interval_s = max(1, settings.SCOUT_INTERVAL_MINUTES) * 60
    category = "developer tools"
    batch = max(1, settings.SCOUT_BATCH_SIZE)
    logger.info(
        f"[scout] background loop enabled — every {settings.SCOUT_INTERVAL_MINUTES}m, "
        f"batch={batch}, category={category!r}"
    )
    while True:
        try:
            await run_scout(category, batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a scout failure must never crash the app
            logger.warning(f"[scout] cycle errored (continuing): {exc}")
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            raise


@app.on_event("startup")
async def _start_scout() -> None:
    if not settings.SCOUT_ENABLED:
        logger.info("[scout] disabled (set SCOUT_ENABLED=true to enable the loop)")
        return
    app.state.scout_task = asyncio.create_task(_scout_loop())


@app.on_event("shutdown")
async def _stop_scout() -> None:
    task = getattr(app.state, "scout_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@app.get("/health", tags=["ops"])
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/", tags=["ops"])
async def root():
    return {"service": "AgentReady API", "docs": "/docs"}
