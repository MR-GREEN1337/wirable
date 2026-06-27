"""
Email tracking endpoint — serves a 1x1 transparent GIF tracking pixel
and records open events on OutboundEmail rows.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_session
from ....models.outbound import OutboundEmail

router = APIRouter(prefix="/track", tags=["track"])

# Full valid 1×1 transparent GIF (35 bytes)
_PIXEL = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)


@router.get("/open/{token}")
async def track_open(
    token: str,
    db: AsyncSession = Depends(get_session),
):
    """
    Record an email open event (first open only) and return a tracking pixel.

    The pixel URL is embedded in outbound cold emails as:
      <img src="https://api.agentready.dev/api/v1/track/open/{token}" width="1" height="1" />
    """
    await db.execute(
        update(OutboundEmail)
        .where(
            OutboundEmail.token == token,
            OutboundEmail.opened_at.is_(None),
        )
        .values(opened_at=datetime.utcnow())
    )
    await db.commit()

    return Response(content=_PIXEL, media_type="image/gif")


@router.get("/click/{token}")
async def track_click(
    token: str,
    db: AsyncSession = Depends(get_session),
):
    """
    Record a link-click event and redirect to the report URL.
    """
    result = await db.execute(
        select(OutboundEmail).where(OutboundEmail.token == token)
    )
    email = result.scalar_one_or_none()

    if email and not email.clicked_at:
        await db.execute(
            update(OutboundEmail)
            .where(OutboundEmail.token == token)
            .values(clicked_at=datetime.utcnow())
        )
        await db.commit()

    from fastapi.responses import RedirectResponse
    redirect_url = email.report_url if email and email.report_url else "/"
    return RedirectResponse(url=redirect_url, status_code=302)
