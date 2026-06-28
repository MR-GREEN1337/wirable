"""
github_harness_fix (Wirable) — the REAL FIX harness orchestrator.

Where github_fix.open_fix_pr drops files via the GitHub REST Contents API (no
clone), THIS path does real agentic git work inside a Daytona sandbox:

  provision sandbox -> token-auth'd `git clone` the user's repo -> inspect +
  ask Claude for grounded agent-readiness changes -> commit on a branch ->
  `git push` -> open a PR from the pushed branch.

It streams progress on the run's SSE bus (the same `emit` + event shapes the
rest of the app uses) and ALWAYS terminates with an `events.fix_pr(...)` event
so the frontend renders the PR (or the error). If the harness can't push (no
git, clone/push failure), it falls back to github_fix.open_fix_pr so we still
deliver a PR via the REST file-drop.

Defensive contract: run_harness_fix never raises — any exception becomes a
`fix_pr` event carrying the error.
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Optional
from urllib.parse import quote

import httpx
from loguru import logger

from ..core.config import settings
from ..core.contracts import events
from ..core.llm import key_pool
from ..core.sandbox import DaytonaClient
from . import github_fix, test_service
from .github_fix import _GH_API, _GH_VERSION, _open_or_get_pr  # reuse PR-open helper

# The in-sandbox driver source — uploaded each run so it's iterable without
# rebuilding the snapshot (mirrors test_service's audit_driver upload pattern).
from pathlib import Path

_FIX_DRIVER_PATH = Path(__file__).parent.parent / "harness" / "fix_driver.py"
_EXEC_TIMEOUT = 600
_BRANCH = "wirable/agent-ready"


def _build_env(proxy_mcp_url: str) -> Optional[dict[str, str]]:
    """Sandbox env: a pooled Claude key (rotation) + model + the MCP URL.

    Mirrors test_service.run_single_audit: pull our OWN key from the pool so the
    in-sandbox driver can call Claude. WIRABLE_MCP_URL is injected so the driver
    can wire the hosted endpoint into llms.txt / .well-known/mcp.json.
    """
    key = key_pool.next_key()
    env: dict[str, str] = {}
    if key:
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_MODEL"] = settings.ANTHROPIC_MODEL
    if proxy_mcp_url:
        env["WIRABLE_MCP_URL"] = proxy_mcp_url
    # Return None (not {}) when empty so sandbox() applies its own default pool
    # injection rather than treating "no key wanted" as explicit.
    return env or None


def _authed_clone_url(repo_full_name: str, github_token: str) -> str:
    """Token-embedded HTTPS clone URL: https://x-access-token:<tok>@github.com/owner/repo.git"""
    tok = quote(github_token, safe="")
    return f"https://x-access-token:{tok}@github.com/{repo_full_name}.git"


async def _open_pr_for_pushed_branch(
    repo_full_name: str,
    github_token: str,
    base_branch: str,
    target_url: str,
) -> str:
    """Open (or reuse) a PR for the already-pushed branch. Returns html_url or ""."""
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _GH_VERSION,
        "User-Agent": "wirable-harness-fix/1.0",
    }
    try:
        async with httpx.AsyncClient(base_url=_GH_API, headers=headers, timeout=30.0) as client:
            base = base_branch or "main"
            if not base_branch:
                # Resolve the repo's default branch as the PR base.
                repo_resp = await client.get(f"/repos/{repo_full_name}")
                if repo_resp.status_code < 400:
                    base = repo_resp.json().get("default_branch") or "main"
            return await _open_or_get_pr(
                client, repo_full_name, _BRANCH, base, target_url
            )
    except Exception:
        logger.exception("[harness_fix] open PR for pushed branch failed")
        return ""


async def run_harness_fix(
    run_id: str,
    github_token: str,
    repo_full_name: str,
    target_url: str,
    audit_dims: Any,
    cards: Optional[list] = None,
    proxy_mcp_url: str = "",
) -> dict:
    """Run the FIX harness: clone -> agent-ready edits -> push -> open PR.

    Streams `line` progress on the run bus and emits a terminal `fix_pr` event.
    Returns the same dict shape as github_fix.open_fix_pr:
      {"pr_url", "branch", "files": [...]} on success, {"error": "..."} otherwise.
    Never raises.
    """
    if not repo_full_name or "/" not in repo_full_name:
        result = {"error": "invalid repo_full_name (expected 'owner/repo')"}
        await test_service.emit(run_id, events.fix_pr("", [], error=result["error"]))
        return result
    if not github_token:
        result = {"error": "no github token"}
        await test_service.emit(run_id, events.fix_pr("", [], repo=repo_full_name, error=result["error"]))
        return result

    cards = cards or []

    async def _line(msg: str, ok: bool = True) -> None:
        await test_service.emit(run_id, {"type": "line", "ok": ok, "msg": msg})

    output: dict = {}
    try:
        await _line("provisioning sandbox…")
        env = _build_env(proxy_mcp_url)
        driver_src = _FIX_DRIVER_PATH.read_text() if _FIX_DRIVER_PATH.exists() else ""
        audit_blob = json.dumps(
            {"dimensions": _dims_payload(audit_dims), "cards": cards}
        ).encode()
        clone_url = _authed_clone_url(repo_full_name, github_token)

        async with DaytonaClient.sandbox(env=env) as sb:
            await sb.upload("/tmp/fix_driver.py", driver_src.encode())
            await sb.upload("/tmp/audit.json", audit_blob)

            await _line(f"cloning {repo_full_name}…")
            await _line("generating agent-ready changes…")
            cmd = (
                "cd /tmp && python3 /tmp/fix_driver.py "
                f"{shlex.quote(clone_url)} {shlex.quote(repo_full_name)} "
                f"{shlex.quote(target_url or '')} /tmp/audit.json 2>&1 || true"
            )
            await sb.exec(cmd, timeout=_EXEC_TIMEOUT)

            raw = await sb.read("/tmp/fix_output.json")

        if raw:
            try:
                output = json.loads(raw.decode())
            except Exception:
                text = raw.decode(errors="replace")
                brace = text.rfind("{")
                if brace >= 0:
                    try:
                        output = json.loads(text[brace:])
                    except Exception:
                        output = {}

        pushed = bool(output.get("pushed"))
        files = output.get("files") or []
        default_branch = output.get("default_branch") or ""
        diff = output.get("diff") or ""
        driver_err = output.get("error")

        if pushed:
            await _line("pushing branch…")
            await _line("opening pull request…")
            pr_url = await _open_pr_for_pushed_branch(
                repo_full_name, github_token, default_branch, target_url or ""
            )
            if pr_url:
                result = {"pr_url": pr_url, "branch": _BRANCH, "files": files}
                await test_service.emit(
                    run_id,
                    events.fix_pr(
                        pr_url,
                        files,
                        branch=_BRANCH,
                        repo=repo_full_name,
                        diff=diff or None,
                    ),
                )
                return result
            # Pushed but PR couldn't open — surface it, then try REST fallback.
            await _line("branch pushed but PR open failed — falling back to REST", ok=False)

        # ---- Fallback: REST file-drop so we STILL deliver a PR ----
        if not pushed:
            reason = driver_err or "harness did not push a branch"
            await _line(f"harness fix unavailable ({reason}) — using REST fallback", ok=False)
        await _line("opening agent-ready PR via REST…")
        rest = await github_fix.open_fix_pr(
            repo_full_name=repo_full_name,
            github_token=github_token,
            target_url=target_url or "",
            audit_dims=audit_dims,
            cards=cards,
            proxy_mcp_url=proxy_mcp_url,
        )
        if rest.get("error"):
            await test_service.emit(
                run_id, events.fix_pr("", [], repo=repo_full_name, error=rest["error"])
            )
            return rest
        await test_service.emit(
            run_id,
            events.fix_pr(
                rest.get("pr_url", ""),
                rest.get("files", []),
                branch=rest.get("branch"),
                repo=repo_full_name,
            ),
        )
        return rest

    except Exception as exc:
        logger.exception("[harness_fix] run_harness_fix failed for %s", run_id)
        # Last-ditch: try the REST path before giving up entirely.
        try:
            rest = await github_fix.open_fix_pr(
                repo_full_name=repo_full_name,
                github_token=github_token,
                target_url=target_url or "",
                audit_dims=audit_dims,
                cards=cards,
                proxy_mcp_url=proxy_mcp_url,
            )
            if not rest.get("error"):
                await test_service.emit(
                    run_id,
                    events.fix_pr(
                        rest.get("pr_url", ""),
                        rest.get("files", []),
                        branch=rest.get("branch"),
                        repo=repo_full_name,
                    ),
                )
                return rest
        except Exception:
            logger.exception("[harness_fix] REST fallback also failed for %s", run_id)
        await test_service.emit(
            run_id, events.fix_pr("", [], repo=repo_full_name, error=str(exc))
        )
        return {"error": str(exc)}


def _dims_payload(audit_dims: Any) -> dict:
    """Normalize audit_dims into the {dim: {passed, evidence}} map the driver reads.

    The driver's load_audit handles both shapes, but normalizing here keeps the
    uploaded blob compact + canonical.
    """
    out: dict = {}
    try:
        if isinstance(audit_dims, dict):
            for k, v in audit_dims.items():
                v = v if isinstance(v, dict) else {}
                out[k] = {
                    "passed": bool(v.get("passed", False)),
                    "evidence": str(v.get("evidence", "") or ""),
                }
        elif isinstance(audit_dims, list):
            for d in audit_dims:
                if not isinstance(d, dict):
                    continue
                key = d.get("dim") or d.get("key")
                if not key:
                    continue
                out[key] = {
                    "passed": bool(d.get("passed", False)),
                    "evidence": str(d.get("evidence", "") or ""),
                }
    except Exception:
        logger.debug("[harness_fix] _dims_payload normalize failed")
    return out
