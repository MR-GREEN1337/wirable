"""
GitHub endpoints (Wirable) — connect a user's GitHub via OAuth, list their
repos, and select the repo the FIX flow will open a PR against.

  GET  /api/v1/github/authorize-url            -> {url}      (OAuth start)
  POST /api/v1/github/connect   body {code}    -> {connected, login}  (callback)
  GET  /api/v1/github/repos                     -> {repos: [...]}
  POST /api/v1/github/select    body {repo}    -> {repo}      (persist selection)
  GET  /api/v1/github/status                    -> {connected, repo, login}

The OAuth token + selected repo are persisted on the user's Client row
(Client.github_token / Client.github_repo) so the proxy FIX flow can read them
server-side and open the PR. The token is never returned to the client.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.config import settings
from ....core.database import get_session
from ....models.client import Client
from ....services import mcp_monitor

router = APIRouter(prefix="/github", tags=["github"])

_GH_API = "https://api.github.com"
_GH_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_SCOPE = "repo"  # needs repo scope to push a branch + open a PR


# ---------------------------------------------------------------------------
# Client row resolution — the FIX flow reads the token/repo off the Client.
# ---------------------------------------------------------------------------


async def _get_or_create_client(db: AsyncSession, user_id) -> Client:
    result = await db.execute(select(Client).where(Client.user_id == user_id))
    client = result.scalars().first()
    if client is None:
        client = Client(user_id=user_id)
        db.add(client)
        await db.commit()
        await db.refresh(client)
    return client


def _user_uuid(user: dict):
    import uuid as _uuid

    sub = user.get("sub")
    try:
        return _uuid.UUID(str(sub))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid user id in token")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConnectRequest(BaseModel):
    code: str
    redirect_uri: Optional[str] = None


class SelectRepoRequest(BaseModel):
    repo: str  # "owner/repo"


class MonitorCheckRequest(BaseModel):
    repo: str  # "owner/repo"


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


@router.get("/authorize-url")
async def authorize_url(redirect_uri: Optional[str] = None):
    """Return the GitHub OAuth authorize URL the frontend should redirect to."""
    if not settings.GITHUB_CLIENT_ID:
        raise HTTPException(status_code=503, detail="github oauth not configured")
    params = [
        f"client_id={settings.GITHUB_CLIENT_ID}",
        f"scope={_SCOPE}",
    ]
    if redirect_uri:
        from urllib.parse import quote

        params.append(f"redirect_uri={quote(redirect_uri, safe='')}")
    return {"url": f"{_GH_AUTHORIZE_URL}?" + "&".join(params)}


@router.post("/connect")
async def connect(
    body: ConnectRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Exchange the OAuth `code` for a token and persist it on the Client row."""
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="github oauth not configured")

    token_body = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": body.code,
    }
    if body.redirect_uri:
        token_body["redirect_uri"] = body.redirect_uri

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            tok_resp = await client.post(
                _GH_TOKEN_URL,
                json=token_body,
                headers={"Accept": "application/json"},
            )
            data = tok_resp.json() if tok_resp.status_code < 400 else {}
            token = data.get("access_token")
            if not token:
                logger.warning("[github] token exchange failed: %s", data)
                raise HTTPException(status_code=400, detail="oauth exchange failed")

            # Resolve the GitHub login for display.
            me = await client.get(
                f"{_GH_API}/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "wirable-fix/1.0",
                },
            )
            login = me.json().get("login", "") if me.status_code < 400 else ""
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[github] connect failed")
        raise HTTPException(status_code=502, detail=f"github error: {exc}")

    client_row = await _get_or_create_client(db, _user_uuid(user))
    client_row.github_token = token
    await db.commit()

    return {"connected": True, "login": login}


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------


@router.get("/repos")
async def list_repos(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """List the connected user's repos (most recently pushed first)."""
    client_row = await _get_or_create_client(db, _user_uuid(user))
    token = client_row.github_token
    if not token:
        raise HTTPException(status_code=400, detail="github not connected")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{_GH_API}/user/repos",
                params={"sort": "pushed", "per_page": 100, "affiliation": "owner,collaborator"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "wirable-fix/1.0",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail="github repo list failed")
            repos = [
                {
                    "full_name": r.get("full_name"),
                    "name": r.get("name"),
                    "private": r.get("private", False),
                    "default_branch": r.get("default_branch"),
                    "permissions": r.get("permissions", {}),
                }
                for r in resp.json()
                if isinstance(r, dict)
            ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[github] list repos failed")
        raise HTTPException(status_code=502, detail=f"github error: {exc}")

    return {"repos": repos, "selected": client_row.github_repo}


@router.post("/select")
async def select_repo(
    body: SelectRepoRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Persist the repo the FIX flow should open a PR against."""
    if "/" not in body.repo:
        raise HTTPException(status_code=400, detail="repo must be 'owner/repo'")
    client_row = await _get_or_create_client(db, _user_uuid(user))
    if not client_row.github_token:
        raise HTTPException(status_code=400, detail="github not connected")
    client_row.github_repo = body.repo
    await db.commit()
    return {"repo": body.repo}


@router.get("/status")
async def status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Connection status for the UI: connected?, selected repo."""
    client_row = await _get_or_create_client(db, _user_uuid(user))
    return {
        "connected": bool(client_row.github_token),
        "repo": client_row.github_repo,
    }


# ---------------------------------------------------------------------------
# MCP drift monitoring — webhook (unauthenticated) + status routes (authed)
# ---------------------------------------------------------------------------


def _verify_webhook_signature(secret: str, body: bytes, header: Optional[str]) -> bool:
    """Best-effort HMAC verify of the X-Hub-Signature-256 header.

    When `secret` is empty we accept everything (signature not enforced). When a
    secret is set we require a matching sha256 signature.
    """
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, header)


def _branch_from_ref(ref: Optional[str], fallback: str) -> str:
    """`refs/heads/main` -> `main`; otherwise fall back to the repo default."""
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return fallback or "main"


async def _resolve_repo_token(repo_full_name: str) -> Optional[str]:
    """Find a stored GitHub token for a connected repo (for private-repo reads).

    Best-effort: matches the Client row that selected this repo. Returns None
    when no connected client owns it (public repos still work without a token).
    """
    if not repo_full_name:
        return None
    try:
        async with get_session_ctx() as db:
            res = await db.execute(
                select(Client).where(Client.github_repo == repo_full_name)
            )
            row = res.scalars().first()
            return row.github_token if row else None
    except Exception:
        logger.exception("[github] resolve repo token failed for %s", repo_full_name)
        return None


def get_session_ctx():
    """AsyncSession context manager (the dependency yields, so we need our own)."""
    from ....core.database import AsyncSessionLocal

    return AsyncSessionLocal()


async def _run_check(repo_full_name: str, branch: str) -> None:
    """Background task: resolve a token if connected, then re-check the MCP."""
    try:
        token = await _resolve_repo_token(repo_full_name)
        await mcp_monitor.check_repo_mcp(repo_full_name, branch, token)
    except Exception:
        logger.exception("[github] background MCP check failed for %s", repo_full_name)


@router.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    """Receive GitHub webhook deliveries (push / ping).

    Unauthenticated by design — GitHub calls this. On a `push` we extract the
    repo + pushed branch and re-verify, in the background, the MCP that the
    repo's llms.txt advertises. Returns 200 fast; never blocks on the check.
    """
    body = await request.body()
    event = request.headers.get("X-GitHub-Event", "")
    sig = request.headers.get("X-Hub-Signature-256")

    if not _verify_webhook_signature(settings.GITHUB_WEBHOOK_SECRET, body, sig):
        raise HTTPException(status_code=401, detail="invalid signature")

    if event == "ping":
        return {"ok": True, "pong": True}

    try:
        import json as _json

        payload = _json.loads(body or b"{}")
    except Exception:
        payload = {}

    if event != "push":
        # We only act on pushes/deploys; acknowledge everything else.
        return {"ok": True, "ignored": event or "unknown"}

    repo = ((payload.get("repository") or {}).get("full_name")) or ""
    default_branch = (payload.get("repository") or {}).get("default_branch") or "main"
    branch = _branch_from_ref(payload.get("ref"), default_branch)

    if not repo:
        return {"ok": True, "ignored": "no repo in payload"}

    # Only re-check pushes to the default branch (where llms.txt lives).
    if branch != default_branch:
        return {"ok": True, "skipped_branch": branch}

    background.add_task(_run_check, repo, branch)
    return {"ok": True, "scheduled": repo, "branch": branch}


@router.get("/monitor")
async def monitor_status(
    repo: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Latest stored MCP-monitor status for `repo` (authed).

    Returns {connected:false} when the caller hasn't connected GitHub, or
    {checked:false} when we've never checked this repo yet.
    """
    client_row = await _get_or_create_client(db, _user_uuid(user))
    status = await mcp_monitor.get_status(repo)
    if status is None:
        return {
            "connected": bool(client_row.github_token),
            "repo": repo,
            "checked": False,
        }
    return {"connected": bool(client_row.github_token), "checked": True, "status": status}


@router.post("/monitor/check")
async def monitor_check_now(
    body: MonitorCheckRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """On-demand re-check (the UI's "Check now" button / demo without a push)."""
    if "/" not in body.repo:
        raise HTTPException(status_code=400, detail="repo must be 'owner/repo'")
    client_row = await _get_or_create_client(db, _user_uuid(user))
    # Prefer the connected client's token for private-repo reads.
    token = client_row.github_token or await _resolve_repo_token(body.repo)

    # Resolve the default branch via the API when we have a token (best-effort);
    # otherwise default to "main" (the raw fetch tolerates a wrong branch).
    branch = "main"
    if token:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(
                    f"{_GH_API}/repos/{body.repo}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "wirable-monitor/1.0",
                    },
                )
                if r.status_code < 400:
                    branch = r.json().get("default_branch") or "main"
        except Exception:
            logger.debug("[github] default-branch lookup failed for %s", body.repo)

    status = await mcp_monitor.check_repo_mcp(body.repo, branch, token)
    return {"checked": True, "status": status}
