"""
mcp_monitor (Wirable) — MCP drift monitoring.

When a connected repo pushes (or a deploy happens), GitHub calls our webhook. We
then fetch that repo's `llms.txt`, find the MCP endpoint it advertises, re-probe
that MCP (`tools/list`), and compare the live tool set to the LAST set we stored
for that repo + mcp_url. Any added/removed tools or a reachability flip is
"drift" — we persist the new status and surface it in the run UI.

The check is the one public entry point:

    check_repo_mcp(repo_full_name, default_branch="main", token=None) -> dict

It NEVER raises. A missing llms.txt, an llms.txt with no MCP url, an unreachable
MCP, or a private repo without a token all degrade to a clear status dict.

Persistence is migration-free: a tiny self-ensuring table (`mcp_monitor_status`,
one row per repo+mcp_url) is created on first use via CREATE TABLE IF NOT EXISTS.
We never touch the Client model or Alembic.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import text

from ..core.database import AsyncSessionLocal
from . import code_analysis  # read-only: real-endpoint extraction + commit diff
from . import proxy_generator  # read-only: we reuse its _probe_proxy_mcp

_GH_API = "https://api.github.com"
_GH_VERSION = "2022-11-28"
_RAW = "https://raw.githubusercontent.com"
_TIMEOUT = 20.0
_UA = "wirable-monitor/1.0"


# ===========================================================================
# Self-ensuring persistence (migration-free)
# ===========================================================================

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS mcp_monitor_status (
    repo        VARCHAR(512) NOT NULL,
    mcp_url     VARCHAR(2048) NOT NULL DEFAULT '',
    reachable   BOOLEAN NOT NULL DEFAULT FALSE,
    tool_names  TEXT NOT NULL DEFAULT '[]',
    last_status TEXT NOT NULL DEFAULT '{}',
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (repo)
);
"""

_ensured = False


async def _ensure_table(session) -> None:
    """Create the status table if it does not exist yet (idempotent)."""
    global _ensured
    if _ensured:
        return
    try:
        await session.execute(text(_TABLE_DDL))
        await session.commit()
        _ensured = True
    except Exception:
        await session.rollback()
        logger.exception("[mcp_monitor] could not ensure status table")


async def _load_last(session, repo: str) -> Optional[dict]:
    """Return the last stored status row for `repo` (or None)."""
    try:
        res = await session.execute(
            text(
                "SELECT repo, mcp_url, reachable, tool_names, last_status, checked_at "
                "FROM mcp_monitor_status WHERE repo = :repo"
            ),
            {"repo": repo},
        )
        row = res.mappings().first()
        if not row:
            return None
        return dict(row)
    except Exception:
        logger.exception("[mcp_monitor] load_last failed for %s", repo)
        return None


async def _save(session, repo: str, mcp_url: str, reachable: bool,
                tool_names: list[str], status: dict) -> None:
    """Upsert the latest status + tool set for `repo`."""
    try:
        await session.execute(
            text(
                "INSERT INTO mcp_monitor_status "
                "(repo, mcp_url, reachable, tool_names, last_status, checked_at) "
                "VALUES (:repo, :mcp_url, :reachable, :tool_names, :last_status, :checked_at) "
                "ON CONFLICT (repo) DO UPDATE SET "
                "mcp_url = EXCLUDED.mcp_url, "
                "reachable = EXCLUDED.reachable, "
                "tool_names = EXCLUDED.tool_names, "
                "last_status = EXCLUDED.last_status, "
                "checked_at = EXCLUDED.checked_at"
            ),
            {
                "repo": repo,
                "mcp_url": mcp_url or "",
                "reachable": bool(reachable),
                "tool_names": json.dumps(sorted(tool_names)),
                "last_status": json.dumps(status),
                "checked_at": status.get("checked_at") or _now_iso(),
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("[mcp_monitor] save failed for %s", repo)


# ===========================================================================
# llms.txt fetch + MCP url extraction
# ===========================================================================


async def _fetch_llms_txt(
    repo: str, branch: str, token: Optional[str]
) -> Optional[str]:
    """Fetch the repo's llms.txt.

    Tries the public raw endpoint first (works for public repos with no auth),
    then falls back to the authenticated Contents API (private repos). Returns
    the file text, or None if it can't be found. Never raises.
    """
    branch = branch or "main"
    # 1) public raw content
    raw_url = f"{_RAW}/{repo}/{branch}/llms.txt"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as c:
            resp = await c.get(raw_url, headers={"User-Agent": _UA})
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
    except Exception:
        logger.debug("[mcp_monitor] raw llms.txt fetch failed for %s", repo)

    # 2) authenticated Contents API (private repos / fallback)
    if token:
        try:
            import base64

            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                resp = await c.get(
                    f"{_GH_API}/repos/{repo}/contents/llms.txt",
                    params={"ref": branch},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": _GH_VERSION,
                        "User-Agent": _UA,
                    },
                )
                if resp.status_code == 200:
                    body = resp.json()
                    if isinstance(body, dict) and body.get("content"):
                        try:
                            return base64.b64decode(body["content"]).decode(
                                "utf-8", "replace"
                            )
                        except Exception:
                            return None
        except Exception:
            logger.debug("[mcp_monitor] contents llms.txt fetch failed for %s", repo)

    return None


_URL_RE = re.compile(r"https?://[^\s\)\]\}<>\"'`]+")


def extract_mcp_url(llms_txt: str) -> Optional[str]:
    """Extract the MCP endpoint a repo's llms.txt advertises. Lenient + defensive.

    Strategy (first hit wins):
      1. An explicit `mcp_url`/`MCP:`/`MCP endpoint` field -> the url on/after it.
      2. The first https url that itself looks MCP-ish (contains '/mcp' or
         '.well-known/mcp.json').
      3. The first https url that appears under (or on the same line as) a line
         that mentions "MCP".
    Returns the url string, or None. Trims trailing punctuation.
    """
    if not llms_txt:
        return None
    lines = llms_txt.splitlines()

    # 1) explicit field-style hint on a single line
    field_re = re.compile(
        r"(?:mcp[_\s-]*url|mcp\s*endpoint|mcp)\s*[:=]\s*(\S+)", re.IGNORECASE
    )
    for ln in lines:
        m = field_re.search(ln)
        if m:
            cand = _clean_url(m.group(1))
            if cand and cand.startswith("http"):
                return cand

    # 2) any url that itself looks like an MCP endpoint
    for m in _URL_RE.finditer(llms_txt):
        u = _clean_url(m.group(0))
        low = u.lower()
        if "/.well-known/mcp.json" in low or "/mcp" in low or "mcp" in low.split("//")[-1].split("/")[0]:
            return u

    # 3) first url near an "MCP" mention (same line or the next few lines)
    for i, ln in enumerate(lines):
        if "mcp" in ln.lower():
            window = "\n".join(lines[i : i + 4])
            m = _URL_RE.search(window)
            if m:
                return _clean_url(m.group(0))

    return None


def _clean_url(u: str) -> str:
    """Strip wrapping markdown/punctuation from a captured url."""
    u = u.strip().strip("<>`\"'")
    u = u.rstrip(".,;:)]}")
    # strip a leading markdown '(' if present
    u = u.lstrip("([")
    return u


# ===========================================================================
# Drift computation
# ===========================================================================


def _compute_drift(
    prev: Optional[dict], reachable: bool, tool_names: list[str]
) -> dict:
    """Compare the live probe to the previously stored row -> drift summary.

    Returns {added:[], removed:[], reachable_flip: bool, drift: bool, first_seen: bool}.
    """
    cur = set(tool_names)
    if not prev:
        # First time we've ever seen this repo — establish a baseline, no drift.
        return {
            "added": [],
            "removed": [],
            "reachable_flip": False,
            "drift": False,
            "first_seen": True,
        }

    try:
        prev_tools = set(json.loads(prev.get("tool_names") or "[]"))
    except Exception:
        prev_tools = set()
    prev_reachable = bool(prev.get("reachable"))

    added = sorted(cur - prev_tools)
    removed = sorted(prev_tools - cur)
    reachable_flip = prev_reachable != reachable
    drift = bool(added or removed or reachable_flip)
    return {
        "added": added,
        "removed": removed,
        "reachable_flip": reachable_flip,
        "drift": drift,
        "first_seen": False,
    }


# ===========================================================================
# Code-endpoint diff on push — "product changed -> read what changed -> store"
# ===========================================================================


async def _check_endpoint_changes(
    repo_full_name: str, branch: str, token: Optional[str]
) -> Optional[dict]:
    """If we have a prior code snapshot for this repo, re-analyze the source at the
    pushed branch, diff the REAL endpoints against the last commit, store the new
    snapshot, and return the change summary. Returns None when there is no prior
    snapshot (we only diff repos that were code-grounded at proxy-build time) or
    when analysis fails. Fully defensive — never raises.

    -> {
         "commit_sha", "prev_commit_sha", "branch",
         "added_count", "removed_count", "changed_count",
         "added": [...], "removed": [...], "changed": [...],
         "changed_since_last_commit": bool,
       }
    """
    try:
        prev = await code_analysis.get_snapshot(repo_full_name)
    except Exception:
        logger.exception("[mcp_monitor] get_snapshot failed for %s", repo_full_name)
        prev = None
    if not prev:
        # Repo was never code-grounded (no proxy built from its source) — nothing
        # to diff against. The llms.txt MCP re-check still ran.
        return None

    try:
        result = await code_analysis.analyze_repo(repo_full_name, token, ref=branch)
    except Exception:
        logger.exception("[mcp_monitor] analyze_repo raised for %s", repo_full_name)
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None

    new_endpoints = result.get("endpoints") or []
    if not isinstance(new_endpoints, list):
        new_endpoints = []
    new_commit = str(result.get("commit_sha") or "")
    prev_commit = str(prev.get("commit_sha") or "")

    # Nothing new analyzed (same commit) -> no change, but still report the state.
    try:
        diff = code_analysis.diff_endpoints(prev.get("endpoints") or [], new_endpoints)
    except Exception:
        logger.exception("[mcp_monitor] diff_endpoints failed for %s", repo_full_name)
        diff = {"added": [], "removed": [], "changed": []}

    added = diff.get("added") or []
    removed = diff.get("removed") or []
    changed = diff.get("changed") or []

    # Store the new commit's surface so the NEXT push diffs against it.
    try:
        await code_analysis.store_snapshot(
            repo_full_name,
            new_commit,
            result.get("framework") or "unknown",
            new_endpoints,
            base_url_hint=result.get("base_url_hint"),
        )
    except Exception:
        logger.exception("[mcp_monitor] store_snapshot failed for %s", repo_full_name)

    change = {
        "commit_sha": new_commit,
        "prev_commit_sha": prev_commit,
        "branch": branch,
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
        "added": added,
        "removed": removed,
        "changed": changed,
        "changed_since_last_commit": bool(added or removed or changed),
    }
    if change["changed_since_last_commit"]:
        logger.info(
            "[mcp_monitor] ENDPOINT CHANGE %s %s..%s +%d -%d ~%d",
            repo_full_name, prev_commit[:7], new_commit[:7],
            len(added), len(removed), len(changed),
        )
    return change


# ===========================================================================
# Public entry point
# ===========================================================================


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def check_repo_mcp(
    repo_full_name: str,
    default_branch: str = "main",
    token: Optional[str] = None,
) -> dict:
    """Re-verify the MCP a repo's llms.txt advertises, and record any drift.

    repo_full_name : "owner/repo"
    default_branch : the branch to read llms.txt from (push payloads carry the
                     pushed branch).
    token          : optional GitHub OAuth token, used only for the private-repo
                     llms.txt fallback.

    Returns a status dict (always — never raises):
      {
        repo, mcp_url, reachable, tool_count, tools,
        added: [], removed: [], drift: bool, reachable_flip: bool,
        first_seen: bool, checked_at, error?: str
      }
    """
    checked_at = _now_iso()
    base = {
        "repo": repo_full_name,
        "mcp_url": None,
        "reachable": False,
        "tool_count": 0,
        "tools": [],
        "added": [],
        "removed": [],
        "drift": False,
        "reachable_flip": False,
        "first_seen": False,
        "checked_at": checked_at,
        # Code-grounded endpoint diff (populated below when a snapshot exists).
        "endpoint_changes": None,
    }

    if not repo_full_name or "/" not in repo_full_name:
        base["error"] = "invalid repo (expected 'owner/repo')"
        return base

    # 0) code-grounded endpoint diff — re-analyze the SOURCE at this commit and
    #    diff the real endpoints vs the last snapshot. Runs whenever we have a
    #    prior snapshot (i.e. the proxy was code-grounded), independent of
    #    llms.txt presence, so a pure backend change still surfaces. Defensive.
    try:
        endpoint_changes = await _check_endpoint_changes(
            repo_full_name, default_branch, token
        )
        base["endpoint_changes"] = endpoint_changes
    except Exception:
        logger.exception("[mcp_monitor] endpoint-change check failed for %s", repo_full_name)
        base["endpoint_changes"] = None

    # 1) fetch llms.txt
    llms = await _fetch_llms_txt(repo_full_name, default_branch, token)
    if not llms:
        base["error"] = "no llms.txt found (is it on the default branch?)"
        # still persist so the UI shows "checked, missing"
        await _persist(repo_full_name, "", False, [], base)
        return base

    # 2) extract the advertised MCP url
    mcp_url = extract_mcp_url(llms)
    if not mcp_url:
        base["error"] = "llms.txt has no MCP endpoint"
        await _persist(repo_full_name, "", False, [], base)
        return base
    base["mcp_url"] = mcp_url

    # 3) re-probe the MCP (reuse proxy_generator's probe; never raises)
    try:
        reachable, tool_names = await proxy_generator._probe_proxy_mcp(mcp_url)
    except Exception:
        logger.exception("[mcp_monitor] probe raised for %s", mcp_url)
        reachable, tool_names = False, []
    tool_names = [str(t) for t in (tool_names or [])]

    # 4) compute drift vs the last stored set, then persist the new state
    async with AsyncSessionLocal() as session:
        await _ensure_table(session)
        prev = await _load_last(session, repo_full_name)
        drift = _compute_drift(prev, reachable, tool_names)

        base.update(
            {
                "reachable": reachable,
                "tool_count": len(tool_names),
                "tools": sorted(tool_names),
                "added": drift["added"],
                "removed": drift["removed"],
                "reachable_flip": drift["reachable_flip"],
                "drift": drift["drift"],
                "first_seen": drift["first_seen"],
            }
        )
        if not reachable and not base.get("error"):
            base["error"] = "MCP endpoint unreachable"

        await _save(session, repo_full_name, mcp_url, reachable, tool_names, base)

    if drift.get("drift"):
        logger.info(
            "[mcp_monitor] DRIFT %s +%s -%s flip=%s",
            repo_full_name, drift["added"], drift["removed"], drift["reachable_flip"],
        )
    return base


async def _persist(
    repo: str, mcp_url: str, reachable: bool, tool_names: list[str], status: dict
) -> None:
    """Persist a status row in its own session (used on early-degrade paths)."""
    try:
        async with AsyncSessionLocal() as session:
            await _ensure_table(session)
            await _save(session, repo, mcp_url, reachable, tool_names, status)
    except Exception:
        logger.exception("[mcp_monitor] _persist failed for %s", repo)


async def get_status(repo_full_name: str) -> Optional[dict]:
    """Return the latest stored monitor status for `repo` (or None if never checked)."""
    if not repo_full_name:
        return None
    try:
        async with AsyncSessionLocal() as session:
            await _ensure_table(session)
            row = await _load_last(session, repo_full_name)
            if not row:
                return None
            try:
                status = json.loads(row.get("last_status") or "{}")
            except Exception:
                status = {}
            # checked_at from the row is authoritative for display
            ca = row.get("checked_at")
            if ca is not None:
                status["checked_at"] = (
                    ca.isoformat() if hasattr(ca, "isoformat") else str(ca)
                )
            status.setdefault("repo", row.get("repo"))
            status.setdefault("mcp_url", row.get("mcp_url"))
            status.setdefault("reachable", bool(row.get("reachable")))
            status.setdefault("endpoint_changes", None)
            return status
    except Exception:
        logger.exception("[mcp_monitor] get_status failed for %s", repo_full_name)
        return None
