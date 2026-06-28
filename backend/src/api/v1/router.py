from fastapi import APIRouter

from .endpoints import (
    run,
    proxy,
    dashboard,
    track,
    auth,
    github,
    access,
)

router = APIRouter(prefix="/api/v1")

router.include_router(run.router)
router.include_router(proxy.router)
router.include_router(dashboard.router)
router.include_router(track.router)
router.include_router(auth.router)
router.include_router(github.router)
router.include_router(access.router)
router.include_router(access.billing_router)
