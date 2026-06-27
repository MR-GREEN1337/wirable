"""
GitHub OAuth service — exchanges auth codes and lists repos.
"""
import httpx

from ..core.config import settings


async def exchange_code(code: str) -> str:
    """Exchange a GitHub OAuth authorization code for an access token."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token", "")
        if not token:
            raise ValueError(f"GitHub OAuth error: {data.get('error_description', data)}")
        return token


async def list_repos(token: str) -> list[dict]:
    """Return a list of repos the user has access to, sorted by last push."""
    repos: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            r = await client.get(
                "https://api.github.com/user/repos",
                params={"per_page": 100, "sort": "updated", "page": page},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            repos.extend(
                {
                    "full_name": repo["full_name"],
                    "private": repo["private"],
                    "description": repo.get("description") or "",
                    "default_branch": repo.get("default_branch", "main"),
                    "updated_at": repo.get("updated_at"),
                }
                for repo in batch
            )
            if len(batch) < 100:
                break
            page += 1
    return repos
