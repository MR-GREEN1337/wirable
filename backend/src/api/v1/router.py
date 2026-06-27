from fastapi import APIRouter

from .endpoints import (
    run,
    proxy,
    dashboard,
    track,
    auth,
)

router = APIRouter(prefix="/api/v1")

router.include_router(run.router)
router.include_router(proxy.router)
router.include_router(dashboard.router)
router.include_router(track.router)
router.include_router(auth.router)
