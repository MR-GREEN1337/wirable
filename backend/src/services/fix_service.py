"""
Fix service — spawns a Daytona sandbox to clone the repo, run the fix agent,
collect generated artifacts, project the post-fix score, and open a GitHub PR.

SSE progress is streamed via per-job asyncio.Queue instances.
"""
import asyncio
import uuid
from pathlib import Path
from typing import AsyncGenerator

from ..core.sandbox import DaytonaClient
from ..core.llm import key_pool
from ..core.config import settings
from ..core.database import AsyncSessionLocal
from ..models.mcp import MCP
from ..models.client import Client
from ..agents.pr_agent import PRAgent
from ..services.score_service import project_after

FIX_PROMPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "harness" / "prompts" / "fix.md"
)

# ---------------------------------------------------------------------------
# In-process SSE event bus keyed by job_id (history + replay).
#
# Same late-subscriber-safe design as audit_service: keep the full event
# history + a done flag, notify via a Condition, and have subscribers replay
# from a cursor. A client that connects AFTER the fix task already finished
# still receives every event including the terminal one — no hang, no dupes.
# ---------------------------------------------------------------------------
_fix_history: dict[str, list[dict]] = {}
_fix_done: dict[str, bool] = {}
_fix_conds: dict[str, asyncio.Condition] = {}

_SUBSCRIBE_TIMEOUT_S = 1800


def _fix_cond(job_id: str) -> asyncio.Condition:
    if job_id not in _fix_conds:
        _fix_conds[job_id] = asyncio.Condition()
    return _fix_conds[job_id]


async def _emit(job_id: str, msg: dict) -> None:
    cond = _fix_cond(job_id)
    async with cond:
        _fix_history.setdefault(job_id, []).append(msg)
        if msg.get("type") in ("done", "error"):
            _fix_done[job_id] = True
        cond.notify_all()


def get_fix_queue(job_id: str):  # pragma: no cover - backwards-compat shim
    """Deprecated: retained so older imports don't crash. Returns the history list."""
    return _fix_history.setdefault(job_id, [])


async def subscribe_fix(job_id: str) -> AsyncGenerator[dict, None]:
    """Yield SSE event dicts for the given fix job — replay then live-stream."""
    cond = _fix_cond(job_id)
    cursor = 0
    terminal = False
    while True:
        pending: list[dict] = []
        async with cond:
            hist = _fix_history.get(job_id, [])
            while cursor < len(hist):
                event = hist[cursor]
                cursor += 1
                pending.append(event)
                if event.get("type") in ("done", "error"):
                    terminal = True
                    break
            if not pending:
                if _fix_done.get(job_id):
                    return
                try:
                    await asyncio.wait_for(cond.wait(), timeout=_SUBSCRIBE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    return
                continue
        for event in pending:
            yield event
        if terminal:
            return


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_fix(
    client_id: str,
    repo: str,
    domain: str,
    github_token: str,
    before_dims: dict,
    before_score: int,
    job_id: str,
    audit_id: str | None = None,
) -> dict:
    """
    Clone the repo in a Daytona sandbox, run the fix agent, collect artifacts,
    project the post-fix score, open a GitHub PR, and PERSIST the result.

    Args:
        client_id:     Client UUID (owns the MCP row; fix_status is flipped).
        repo:          GitHub repo in "owner/repo" format.
        domain:        Product domain being audited.
        github_token:  OAuth token with repo write access.
        before_dims:   CATTS-aggregated dimension dict from the pre-fix audit.
        before_score:  Numeric score before the fix (0-100).
        job_id:        SSE stream identifier.
        audit_id:      Pre-fix Audit id this MCP links back to (optional).

    Returns:
        {"pr_url": str, "projected_score": int} on success, {} on error.
    """

    async def em(msg: str, **extra):
        await _emit(job_id, {"type": "line", "ok": True, "msg": msg, **extra})

    try:
        if FIX_PROMPT_PATH.exists():
            prompt = FIX_PROMPT_PATH.read_text().replace("{domain}", domain).replace("{repo}", repo)
        else:
            prompt = (
                f"Fix agent-readiness for {repo} ({domain}).\n"
                "Generate: mcp-server/index.ts, llms.txt, docs/agent-guide.md, evals/basic.ts\n"
                "Write files relative to /repo. Stop when done."
            )

        await em("Spawning Daytona sandbox + cloning repo...")
        # Pull one pooled key so the OpenCode fix agent runs on Claude.
        key = key_pool.next_key()
        env = (
            {"ANTHROPIC_API_KEY": key, "ANTHROPIC_MODEL": settings.ANTHROPIC_MODEL}
            if key
            else None
        )
        async with DaytonaClient.sandbox(env=env) as sb:
            clone_url = f"https://x-access-token:{github_token}@github.com/{repo}.git"
            await sb.exec(f"git clone {clone_url} /repo 2>&1", timeout=120)
            await em(f"Repo cloned. Running OpenCode fix agent...")

            await sb.upload("/task.md", prompt.encode())
            await sb.exec("cd /repo && opencode run --task /task.md 2>&1 || true", timeout=1200)

            # Collect generated files
            files: dict[str, str] = {}
            for rel in [
                "mcp-server/index.ts", "mcp-server/package.json",
                "llms.txt", "docs/agent-guide.md",
                "evals/basic.ts", "openapi.json",
            ]:
                raw = await sb.read(f"/repo/{rel}")
                if raw:
                    files[rel] = raw.decode("utf-8", errors="replace")

            # Tool files
            tool_ls = await sb.exec("ls /repo/mcp-server/tools/ 2>/dev/null || true", timeout=10)
            for tool_file in tool_ls.strip().splitlines():
                tool_file = tool_file.strip()
                if tool_file:
                    raw = await sb.read(f"/repo/mcp-server/tools/{tool_file}")
                    if raw:
                        files[f"mcp-server/tools/{tool_file}"] = raw.decode("utf-8", errors="replace")

        await em(f"Generated {len(files)} files: {', '.join(files.keys()) or '(none)'}")

        if not files:
            await em("Warning: no artifacts generated. Adding minimal llms.txt.")
            files["llms.txt"] = f"# {domain}\n\nAuto-generated by AgentReady.\n"

        projection = project_after(files, before_dims)
        projected_score = projection["score"]

        await em("Opening GitHub PR...")
        pr_agent = PRAgent(github_token)
        pr = await pr_agent.open_pr(repo, files, before_score, projected_score)
        await em(f"PR #{pr.number} opened: {pr.url}", pr_url=pr.url, pr_number=pr.number)
        await _emit(job_id, {
            "type": "pr_open",
            "pr_url": pr.url,
            "pr_number": pr.number,
            "before_score": before_score,
            "after_score": projected_score,
            "before_dims": before_dims,
            "after_dims": {
                d: {"passed": d in projection["verified_dims"] or before_dims.get(d, {}).get("passed", False),
                    "needs_live": d in projection["unverified_dims"]}
                for d in ["discoverability", "auth", "mcp", "errors", "idempotency", "ratelimit", "docs"]
            },
            "pr_files": list(files.keys()),
        })
        await em(
            f"Score: {before_score}/100 → {projected_score}/100 (projected)",
            projected_score=projected_score,
            verified_dims=projection["verified_dims"],
            unverified_dims=projection["unverified_dims"],
        )

        # --- Persist the fix result: an MCP row + flip the client's fix_status.
        mcp_id: str | None = None
        try:
            async with AsyncSessionLocal() as db:
                client = await db.get(Client, uuid.UUID(client_id))
                mcp = MCP(
                    id=uuid.uuid4(),
                    client_id=uuid.UUID(client_id),
                    audit_id=uuid.UUID(audit_id) if audit_id else None,
                    daytona_job_id=job_id,
                    pr_url=pr.url,
                    pr_number=pr.number,
                    pr_status="open",
                    server_code=files.get("mcp-server/index.ts", ""),
                    llms_txt=files.get("llms.txt", ""),
                    projected_score=projected_score,
                    verified_dims=projection["verified_dims"],
                    unverified_dims=projection["unverified_dims"],
                )
                db.add(mcp)
                if client:
                    client.fix_status = "pr_open"
                await db.commit()
                mcp_id = str(mcp.id)
        except Exception as persist_err:  # don't fail the run on a DB hiccup
            from loguru import logger
            logger.warning("fix_service: failed to persist MCP row: %s", persist_err)

        await _emit(job_id, {
            "type": "done", "pr_url": pr.url, "pr_number": pr.number,
            "projected_score": projected_score,
            "verified_dims": projection["verified_dims"],
            "unverified_dims": projection["unverified_dims"],
            "files": list(files.keys()),
            "mcp_id": mcp_id,
        })
        return {"pr_url": pr.url, "projected_score": projected_score, "mcp_id": mcp_id}

    except Exception as exc:
        await _emit(job_id, {"type": "error", "msg": str(exc)})
        return {}
