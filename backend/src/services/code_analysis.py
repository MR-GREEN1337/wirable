"""
code_analysis (Wirable) — CODE-GROUNDED endpoint extraction + commit tracking.

When a GitHub repo is bound to a tested product we clone it inside a Daytona
sandbox and extract the REAL API endpoints from the SOURCE CODE (ground truth,
not black-box probes), then persist the analyzed commit + endpoint set so we can
diff the surface on future commits.

Public API (the proxy/generator agent imports these — keep the signatures exact):

    analyze_repo(repo_full_name, github_token, ref=None) -> dict
    store_snapshot(repo_full_name, commit_sha, framework, endpoints,
                   base_url_hint=None) -> None
    get_snapshot(repo_full_name) -> dict | None
    diff_endpoints(prev, curr) -> dict

analyze_repo NEVER raises — any failure degrades to {"ok": False, "error": ...}
with the rest of the contract dict populated with defaults.

Persistence is migration-free: a self-ensuring table (`repo_code_snapshot`, one
row per repo) is created on first use via CREATE TABLE IF NOT EXISTS, mirroring
services/mcp_monitor.py. We never touch Alembic or create_all.
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from loguru import logger
from sqlalchemy import text

from ..core.database import AsyncSessionLocal

# The in-sandbox driver source — uploaded each run so it's iterable without
# rebuilding the snapshot (mirrors test_service / github_harness_fix upload).
_CODE_DRIVER_PATH = Path(__file__).parent.parent / "harness" / "code_driver.py"
_EXEC_TIMEOUT = 600


# ===========================================================================
# analyze_repo — sandbox orchestration
# ===========================================================================

def _build_env() -> Optional[dict[str, str]]:
    """Sandbox env: a pooled Claude key (rotation) + model for the Tier-3 LLM pass.

    Mirrors github_harness_fix._build_env: pull our OWN key from the pool so the
    in-sandbox driver can call Claude for thin-parse augmentation. Heavy deps are
    imported lazily so `import src.main` stays clean even if they're absent.
    """
    try:
        from ..core.config import settings
        from ..core.llm import key_pool

        key = key_pool.next_key()
        env: dict[str, str] = {}
        if key:
            env["ANTHROPIC_API_KEY"] = key
            env["ANTHROPIC_MODEL"] = settings.ANTHROPIC_MODEL
        # Return None (not {}) when empty so sandbox() applies its own default
        # pool injection rather than treating "no key wanted" as explicit.
        return env or None
    except Exception:
        logger.exception("[code_analysis] _build_env failed")
        return None


def _authed_clone_url(repo_full_name: str, github_token: str) -> str:
    """Token-embedded HTTPS clone URL."""
    tok = quote(github_token, safe="")
    return f"https://x-access-token:{tok}@github.com/{repo_full_name}.git"


def _empty_result(error: str | None = None) -> dict:
    return {
        "ok": False,
        "commit_sha": "",
        "branch": "",
        "framework": "unknown",
        "endpoints": [],
        "base_url_hint": None,
        "scanned_files": 0,
        "error": error,
    }


async def analyze_repo(
    repo_full_name: str,
    github_token: str | None,
    ref: str | None = None,
) -> dict:
    """Clone `repo_full_name` in a sandbox and extract its API endpoints from code.

    repo_full_name : "owner/repo"
    github_token   : GitHub token for the clone (required for private repos; public
                     repos can clone without one but we still embed it when present).
    ref            : optional branch / tag / commit sha to analyze (defaults HEAD).

    Returns (always — never raises):
      {ok, commit_sha, branch, framework, endpoints:[...], base_url_hint,
       scanned_files, error}
    """
    if not repo_full_name or "/" not in repo_full_name:
        return _empty_result("invalid repo_full_name (expected 'owner/repo')")

    try:
        from ..core.sandbox import DaytonaClient
    except Exception as exc:
        logger.exception("[code_analysis] sandbox import failed")
        return _empty_result(f"sandbox unavailable: {exc}")

    try:
        env = _build_env()
        driver_src = _CODE_DRIVER_PATH.read_text() if _CODE_DRIVER_PATH.exists() else ""
        if not driver_src:
            return _empty_result("code_driver.py source not found")

        # Public repos can clone tokenless; embed the token when we have one.
        clone_url = (
            _authed_clone_url(repo_full_name, github_token)
            if github_token
            else f"https://github.com/{repo_full_name}.git"
        )

        raw: bytes | None = None
        async with DaytonaClient.sandbox(env=env) as sb:
            await sb.upload("/tmp/code_driver.py", driver_src.encode())
            cmd = (
                "cd /tmp && python3 /tmp/code_driver.py "
                f"{shlex.quote(clone_url)} {shlex.quote(repo_full_name)} "
                f"{shlex.quote(ref or '')} 2>&1 || true"
            )
            await sb.exec(cmd, timeout=_EXEC_TIMEOUT)
            raw = await sb.read("/tmp/code_output.json")

        output: dict = {}
        if raw:
            try:
                output = json.loads(raw.decode())
            except Exception:
                txt = raw.decode(errors="replace")
                brace = txt.rfind("{")
                if brace >= 0:
                    try:
                        output = json.loads(txt[brace:])
                    except Exception:
                        output = {}

        if not output:
            return _empty_result("driver produced no output")

        endpoints = output.get("endpoints")
        endpoints = endpoints if isinstance(endpoints, list) else []
        driver_err = output.get("error")
        commit_sha = str(output.get("commit_sha") or "")

        result = {
            # ok == we got a real commit out (clone succeeded). Zero endpoints is
            # still a valid analysis (a repo may legitimately expose no HTTP API).
            "ok": bool(commit_sha) and not driver_err,
            "commit_sha": commit_sha,
            "branch": str(output.get("branch") or ""),
            "framework": str(output.get("framework") or "unknown"),
            "endpoints": endpoints,
            "base_url_hint": output.get("base_url_hint") or None,
            "scanned_files": int(output.get("scanned_files") or 0),
            "error": driver_err,
        }
        return result

    except Exception as exc:
        logger.exception("[code_analysis] analyze_repo failed for %s", repo_full_name)
        return _empty_result(str(exc))


# ===========================================================================
# Self-ensuring persistence (migration-free) — mirrors mcp_monitor.py
# ===========================================================================

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS repo_code_snapshot (
    repo          VARCHAR(512) NOT NULL,
    commit_sha    VARCHAR(64) NOT NULL DEFAULT '',
    framework     VARCHAR(128) NOT NULL DEFAULT 'unknown',
    endpoints     JSONB NOT NULL DEFAULT '[]'::jsonb,
    base_url_hint TEXT,
    analyzed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (repo)
);
"""

_ensured = False


async def _ensure_table(session) -> None:
    """Create the snapshot table if it does not exist yet (idempotent)."""
    global _ensured
    if _ensured:
        return
    try:
        await session.execute(text(_TABLE_DDL))
        await session.commit()
        _ensured = True
    except Exception:
        await session.rollback()
        logger.exception("[code_analysis] could not ensure snapshot table")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def store_snapshot(
    repo_full_name: str,
    commit_sha: str,
    framework: str,
    endpoints: list,
    base_url_hint: str | None = None,
) -> None:
    """Upsert the latest analyzed snapshot for `repo_full_name`. Never raises."""
    if not repo_full_name:
        return
    try:
        async with AsyncSessionLocal() as session:
            await _ensure_table(session)
            try:
                await session.execute(
                    text(
                        "INSERT INTO repo_code_snapshot "
                        "(repo, commit_sha, framework, endpoints, base_url_hint, analyzed_at) "
                        "VALUES (:repo, :commit_sha, :framework, "
                        "CAST(:endpoints AS jsonb), :base_url_hint, :analyzed_at) "
                        "ON CONFLICT (repo) DO UPDATE SET "
                        "commit_sha = EXCLUDED.commit_sha, "
                        "framework = EXCLUDED.framework, "
                        "endpoints = EXCLUDED.endpoints, "
                        "base_url_hint = EXCLUDED.base_url_hint, "
                        "analyzed_at = EXCLUDED.analyzed_at"
                    ),
                    {
                        "repo": repo_full_name,
                        "commit_sha": commit_sha or "",
                        "framework": framework or "unknown",
                        "endpoints": json.dumps(endpoints if isinstance(endpoints, list) else []),
                        "base_url_hint": base_url_hint,
                        "analyzed_at": _now_iso(),
                    },
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("[code_analysis] store_snapshot upsert failed for %s", repo_full_name)
    except Exception:
        logger.exception("[code_analysis] store_snapshot failed for %s", repo_full_name)


async def get_snapshot(repo_full_name: str) -> dict | None:
    """Return the latest stored snapshot for `repo` (or None if never analyzed).

    -> {"commit_sha","framework","endpoints":[...],"base_url_hint","analyzed_at"}
    """
    if not repo_full_name:
        return None
    try:
        async with AsyncSessionLocal() as session:
            await _ensure_table(session)
            res = await session.execute(
                text(
                    "SELECT commit_sha, framework, endpoints, base_url_hint, analyzed_at "
                    "FROM repo_code_snapshot WHERE repo = :repo"
                ),
                {"repo": repo_full_name},
            )
            row = res.mappings().first()
            if not row:
                return None
            endpoints = row.get("endpoints")
            if isinstance(endpoints, str):
                try:
                    endpoints = json.loads(endpoints)
                except Exception:
                    endpoints = []
            if not isinstance(endpoints, list):
                endpoints = []
            analyzed_at = row.get("analyzed_at")
            return {
                "commit_sha": row.get("commit_sha") or "",
                "framework": row.get("framework") or "unknown",
                "endpoints": endpoints,
                "base_url_hint": row.get("base_url_hint"),
                "analyzed_at": (
                    analyzed_at.isoformat()
                    if hasattr(analyzed_at, "isoformat")
                    else (str(analyzed_at) if analyzed_at is not None else None)
                ),
            }
    except Exception:
        logger.exception("[code_analysis] get_snapshot failed for %s", repo_full_name)
        return None


# ===========================================================================
# diff_endpoints — pure function, no DB
# ===========================================================================

def _ep_key(ep: dict) -> str:
    method = str(ep.get("method", "")).strip().upper()
    path = str(ep.get("path", "")).strip()
    return f"{method} {path}"


def _ep_signature(ep: dict) -> tuple:
    """The comparable shape of an endpoint (ignores `source` line drift)."""
    params = ep.get("params")
    if isinstance(params, list):
        params_norm = tuple(sorted(str(p) for p in params))
    else:
        params_norm = ()
    return (
        str(ep.get("summary", "") or "").strip(),
        params_norm,
        (str(ep.get("auth")) if ep.get("auth") else None),
    )


def diff_endpoints(prev: list, curr: list) -> dict:
    """Diff two endpoint lists keyed by "METHOD PATH".

    -> {"added": [...], "removed": [...], "changed": [...]}
       added   : in curr, not in prev
       removed : in prev, not in curr
       changed : same key, but summary/params/auth differ. Each changed item is
                 {"key", "before": <prev ep>, "after": <curr ep>}.
    """
    prev = prev if isinstance(prev, list) else []
    curr = curr if isinstance(curr, list) else []

    prev_map: dict[str, dict] = {}
    for ep in prev:
        if isinstance(ep, dict):
            prev_map[_ep_key(ep)] = ep
    curr_map: dict[str, dict] = {}
    for ep in curr:
        if isinstance(ep, dict):
            curr_map[_ep_key(ep)] = ep

    prev_keys = set(prev_map)
    curr_keys = set(curr_map)

    added = [curr_map[k] for k in sorted(curr_keys - prev_keys)]
    removed = [prev_map[k] for k in sorted(prev_keys - curr_keys)]
    changed = []
    for k in sorted(prev_keys & curr_keys):
        before, after = prev_map[k], curr_map[k]
        if _ep_signature(before) != _ep_signature(after):
            changed.append({"key": k, "before": before, "after": after})

    return {"added": added, "removed": removed, "changed": changed}
