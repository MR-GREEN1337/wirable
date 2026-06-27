"""
GitHub OAuth endpoints — connect an account and list repos.
"""
import uuid as _uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.config import settings
from ....core.database import get_session
from ....models.client import Client
from ....models.user import User
from ....services.github_service import exchange_code, list_repos

router = APIRouter(prefix="/github", tags=["github"])


class ConnectRequest(BaseModel):
    code: str  # GitHub OAuth authorization code


def _redirect_uri() -> str:
    return settings.GITHUB_REDIRECT_URI or f"{settings.REPORT_BASE_URL.rstrip('/')}/github"


@router.get("/authorize-url")
async def authorize_url():
    """Return the GitHub OAuth consent URL for the frontend connect button.

    The frontend opens this URL; GitHub redirects back to {REPORT_BASE_URL}/github
    with a `code` that the page exchanges via POST /github/connect.
    """
    params = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "scope": "repo",
        "redirect_uri": _redirect_uri(),
    }
    return {"url": f"https://github.com/login/oauth/authorize?{urlencode(params)}"}


@router.post("/connect")
async def connect_github(
    body: ConnectRequest,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """
    Exchange a GitHub OAuth code for an access token and store it on the
    authenticated user's Client record (creating one if needed).
    """
    try:
        token = await exchange_code(body.code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"GitHub OAuth failed: {exc}") from exc

    # Find or create a Client row for this user
    result = await db.execute(select(Client).where(Client.user_id == _uuid.UUID(user["sub"])))
    client = result.scalar_one_or_none()

    if client:
        client.github_token = token
    else:
        client = Client(
            id=_uuid.uuid4(),
            user_id=_uuid.UUID(user["sub"]),
            github_token=token,
        )
        db.add(client)

    await db.commit()
    return {"connected": True}


@router.post("/disconnect")
async def disconnect_github(
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Remove the stored GitHub token."""
    result = await db.execute(select(Client).where(Client.user_id == _uuid.UUID(user["sub"])))
    client = result.scalar_one_or_none()
    if client:
        client.github_token = None
        await db.commit()
    return {"disconnected": True}


@router.get("/repos")
async def get_repos(
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """List GitHub repos accessible with the stored token."""
    result = await db.execute(select(Client).where(Client.user_id == _uuid.UUID(user["sub"])))
    client = result.scalar_one_or_none()

    if not client or not client.github_token:
        return {"repos": [], "connected": False}

    try:
        repos = await list_repos(client.github_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc

    return {"repos": repos, "connected": True}
