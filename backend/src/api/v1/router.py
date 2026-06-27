from fastapi import APIRouter

from .endpoints import (
    audit,
    fix,
    dashboard,
    github,
    report,
    track,
    auth,
    outbound,
    onboarding,
    discovery,
)

router = APIRouter(prefix="/api/v1")

router.include_router(audit.router)
router.include_router(fix.router)
router.include_router(dashboard.router)
router.include_router(github.router)
router.include_router(report.router)
router.include_router(track.router)
router.include_router(auth.router)
router.include_router(outbound.router)
router.include_router(onboarding.router)
router.include_router(discovery.router)
