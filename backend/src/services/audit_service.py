"""
Audit service — orchestrates N parallel Daytona sandboxes running the audit agent,
then aggregates results with CATTS.

SSE progress is streamed via per-job asyncio.Queue instances.
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.sandbox import DaytonaClient
from ..core.llm import key_pool
from ..core.config import settings
from ..agents.catts import catts_aggregate_with_arbiter, DIMENSIONS

# ---------------------------------------------------------------------------
# Live screenshot streaming knobs (agent 0 only — see _stream_audit_run).
# ---------------------------------------------------------------------------
_SHOT_POLL_S = 2.5          # how often agent 0 sweeps /screenshots for new jpgs
_SHOT_HARD_TIMEOUT_S = 900  # ceiling on the streaming loop (matches exec timeout)
_SHOT_MAX_FRAMES = 20       # cap streamed frames to bound the SSE payload
_SHOT_MAX_B64 = 250_000     # skip any single frame whose base64 exceeds this

# Path to the harness audit prompt
AUDIT_PROMPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "harness" / "prompts" / "audit.md"
)

# ---------------------------------------------------------------------------
# In-process SSE event bus keyed by job_id
#
# We keep a FULL history of events per job plus a "done" flag, and notify
# subscribers via an asyncio.Condition. A subscriber replays the entire history
# from index 0 (so a client that connects AFTER the background task already
# finished still receives every event, including the terminal one), then waits
# on the condition for any new appends. This is the single source of truth — we
# do NOT also push to a Queue, so there is no possibility of double-yielding an
# event that lives in both history and a queue.
# ---------------------------------------------------------------------------
_history: dict[str, list[dict]] = {}
_done: dict[str, bool] = {}
_conds: dict[str, asyncio.Condition] = {}

# Subscribe wait timeout — generous ceiling for a long audit run.
_SUBSCRIBE_TIMEOUT_S = 1200


def _cond(job_id: str) -> asyncio.Condition:
    if job_id not in _conds:
        _conds[job_id] = asyncio.Condition()
    return _conds[job_id]


async def emit(job_id: str, msg: dict) -> None:
    """Append an event to the job's history and wake any subscribers."""
    cond = _cond(job_id)
    async with cond:
        _history.setdefault(job_id, []).append(msg)
        if msg.get("type") in ("done", "error"):
            _done[job_id] = True
        cond.notify_all()


async def subscribe(job_id: str) -> AsyncGenerator[dict, None]:
    """Yield SSE event dicts for the given job — replay then live-stream.

    Correctness contract: no duplicate events, no hang on a fast/finished job,
    and the terminal (done/error) event is always delivered. A monotonically
    increasing cursor into _history guarantees each event is yielded exactly
    once. Events are collected under the condition lock then yielded outside it,
    so the SSE socket write never blocks the producer.
    """
    cond = _cond(job_id)
    cursor = 0
    terminal = False
    while True:
        pending: list[dict] = []
        async with cond:
            hist = _history.get(job_id, [])
            # Drain everything we haven't yielded yet.
            while cursor < len(hist):
                event = hist[cursor]
                cursor += 1
                pending.append(event)
                if event.get("type") in ("done", "error"):
                    terminal = True
                    break
            if not pending:
                # Caught up with nothing new. If finished, stop; else wait.
                if _done.get(job_id):
                    return
                try:
                    await asyncio.wait_for(cond.wait(), timeout=_SUBSCRIBE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    return
                # Loop back to drain whatever arrived.
                continue

        # Yield outside the lock.
        for event in pending:
            yield event
        if terminal:
            return


def get_queue(job_id: str):  # pragma: no cover - backwards-compat shim
    """Deprecated: retained so older imports don't crash. Returns the history list."""
    return _history.setdefault(job_id, [])


# ---------------------------------------------------------------------------
# Live screenshot streaming (agent 0 = the "camera")
# ---------------------------------------------------------------------------

def _frame_seq(path: str) -> int:
    """Extract the zero-padded numeric stem from /screenshots/NNNN.jpg."""
    try:
        return int(Path(path).stem)
    except Exception:
        return 0


async def _emit_frame(sb, job_id: str, jpg_path: str, seq: int) -> bool:
    """Read one screenshot + sidecar and emit a 'screenshot' SSE event.

    Returns True if a frame was actually emitted. Fully defensive: any failure
    is swallowed (logged at debug) so screenshot streaming can never fail the
    audit.
    """
    try:
        # Sidecar metadata — caption/dimension/url. Plain JSON, safe to cat.
        caption, dimension, url = "", "", ""
        json_path = jpg_path[:-4] + ".json" if jpg_path.endswith(".jpg") else jpg_path + ".json"
        try:
            meta_raw = await sb.exec(f"cat {json_path} 2>/dev/null", timeout=15)
            if meta_raw and meta_raw.strip():
                meta = json.loads(meta_raw)
                caption = str(meta.get("caption", "") or "")
                dimension = str(meta.get("dimension", "") or "")
                url = str(meta.get("url", "") or "")
        except Exception as e:
            logger.debug("screenshot sidecar parse failed for {}: {}", json_path, e)

        b64 = await sb.read_b64(jpg_path)
        if not b64:
            return False
        if len(b64) > _SHOT_MAX_B64:
            logger.debug("screenshot {} too large ({}B b64) — skipping", jpg_path, len(b64))
            return False

        await emit(
            job_id,
            {
                "type": "screenshot",
                "seq": seq,
                "caption": caption,
                "dimension": dimension,
                "url": url,
                "image": "data:image/jpeg;base64," + b64,
            },
        )
        return True
    except Exception as e:
        logger.debug("screenshot emit failed for {}: {}", jpg_path, e)
        return False


async def _sweep_frames(sb, job_id: str, seen: set[int], emitted: list[int]) -> None:
    """List /screenshots/*.jpg and emit any new frames, honoring the frame cap.

    `seen` tracks every seq we've inspected (so we never re-read); `emitted`
    tracks seqs we actually streamed (to enforce _SHOT_MAX_FRAMES). When the cap
    is hit we still emit the *latest* frame (skip the middle) so the live view
    always shows where the agent is now.
    """
    try:
        files = await sb.list_files("/screenshots/*.jpg")
    except Exception as e:
        logger.debug("screenshot sweep list failed: {}", e)
        return

    new = [(f, _frame_seq(f)) for f in files if _frame_seq(f) not in seen]
    new.sort(key=lambda t: t[1])
    for jpg_path, seq in new:
        seen.add(seq)
        if len(emitted) >= _SHOT_MAX_FRAMES:
            # Cap reached: only emit if this is the newest frame on disk, and
            # only if it's strictly newer than the last one we streamed.
            is_latest = seq == _frame_seq(files[-1])
            if not (is_latest and (not emitted or seq > emitted[-1])):
                continue
        if await _emit_frame(sb, job_id, jpg_path, seq):
            emitted.append(seq)


async def _stream_audit_run(sb, job_id: str, agent_id: int, command: str) -> None:
    """Run the audit command in the background and stream screenshots live.

    Defensive throughout: if the sandbox/harness produces no screenshots (or any
    streaming step errors) the audit still completes — the command runs to
    completion and the caller reads /output.json as usual.
    """
    cmd_id = await sb.exec_bg("mkdir -p /screenshots; " + command)

    seen: set[int] = set()
    emitted: list[int] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SHOT_HARD_TIMEOUT_S

    while True:
        try:
            done = await sb.is_command_done(cmd_id)
        except Exception as e:
            logger.debug("is_command_done errored, assuming still running: {}", e)
            done = False

        try:
            await _sweep_frames(sb, job_id, seen, emitted)
        except Exception as e:
            logger.debug("screenshot sweep failed (non-fatal): {}", e)

        if done:
            break
        if loop.time() > deadline:
            logger.warning("[{}] audit stream hit hard timeout {}s", agent_id, _SHOT_HARD_TIMEOUT_S)
            break
        await asyncio.sleep(_SHOT_POLL_S)

    # Final sweep — catch any last frames written just before exit.
    try:
        await _sweep_frames(sb, job_id, seen, emitted)
    except Exception as e:
        logger.debug("final screenshot sweep failed (non-fatal): {}", e)


# ---------------------------------------------------------------------------
# Single-agent runner
# ---------------------------------------------------------------------------

async def run_single_audit(domain: str, agent_id: int, job_id: str) -> dict:
    """Spawn one Daytona sandbox, run the audit agent, return structured result."""
    try:
        await emit(job_id, {"type": "line", "ok": True, "msg": f"[{agent_id}] spawning sandbox"})

        if AUDIT_PROMPT_PATH.exists():
            prompt = AUDIT_PROMPT_PATH.read_text().replace("{domain}", domain)
        else:
            prompt = (
                f"Audit {domain} for agent-readiness across: {', '.join(DIMENSIONS)}.\n"
                "Return ONLY JSON to /output.json:\n"
                '{"domain":"...","dimensions":{"discoverability":{"passed":false,"confidence":0.9,"evidence":"..."}}}'
            )

        # Each parallel agent pulls its OWN key from the pool so N agents spread
        # across N keys (defends against per-key rate limits during fan-out).
        key = key_pool.next_key()
        env = (
            {"ANTHROPIC_API_KEY": key, "ANTHROPIC_MODEL": settings.ANTHROPIC_MODEL}
            if key
            else None
        )
        async with DaytonaClient.sandbox(env=env) as sb:
            await sb.upload("/task.md", prompt.encode())
            await emit(job_id, {"type": "line", "ok": True, "msg": f"[{agent_id}] running audit"})

            audit_cmd = "opencode run --task /task.md 2>&1 || true"
            if agent_id == 0:
                # Agent 0 is the "camera": run non-blocking and stream the
                # screenshots the harness writes to /screenshots while it works.
                # Streaming is best-effort and never fails the audit.
                try:
                    await _stream_audit_run(sb, job_id, agent_id, audit_cmd)
                except Exception as e:
                    logger.debug("[{}] screenshot streaming failed (non-fatal): {}", agent_id, e)
                    # If streaming aborted before the command ran to completion,
                    # fall back to a plain blocking exec so /output.json exists.
                    await sb.exec(audit_cmd, timeout=900)
            else:
                # Other agents run the audit without streaming (avoid Nx
                # duplicate frames) — keep the original blocking path.
                await sb.exec(audit_cmd, timeout=900)

            raw = await sb.read("/output.json")

        data: dict = {}
        if raw:
            try:
                data = json.loads(raw.decode())
            except Exception:
                # Try to find JSON blob in the raw bytes
                text = raw.decode(errors="replace")
                brace = text.rfind("{")
                if brace >= 0:
                    try:
                        data = json.loads(text[brace:])
                    except Exception:
                        pass

        # Fill missing dimensions with failure
        data.setdefault("dimensions", {})
        for dim in DIMENSIONS:
            data["dimensions"].setdefault(dim, {
                "passed": False, "confidence": 0.5, "evidence": "not evaluated"
            })

        await emit(job_id, {"type": "line", "ok": True, "msg": f"[{agent_id}] complete"})
        return data

    except Exception as exc:
        await emit(job_id, {"type": "line", "ok": False, "msg": f"error: {exc}"})
        return {
            "domain": domain,
            "dimensions": {
                dim: {"passed": False, "confidence": 0.5, "evidence": str(exc)}
                for dim in DIMENSIONS
            },
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_audit(
    domain: str,
    job_id: str,
    n: int = 3,
    report_id: str | None = None,
    emit_done: bool = True,
) -> dict:
    """
    Run N parallel audit agents and aggregate with CATTS.

    If confidence < 0.6 after the first N agents, spawns a 4th arbiter agent.
    Emits SSE events throughout; terminates the stream with type='done'.

    Args:
        report_id: Company id used to key the report page. When provided it is
            included in BOTH the terminal "score" and "done" events so the
            landing page knows where to navigate after the audit completes.
        emit_done: When False, the terminal "done" event is suppressed so a
            caller (e.g. the verification service) can append its own summary
            event and emit the terminal "done" itself on the same SSE bus.
    """
    await emit(job_id, {"type": "line", "ok": True, "msg": f"Spawning {n} agents for {domain}..."})

    tasks = [run_single_audit(domain, i, job_id) for i in range(n)]
    results = list(await asyncio.gather(*tasks, return_exceptions=False))

    await emit(job_id, {"type": "line", "ok": True, "msg": "Aggregating CATTS consensus..."})
    # N agents → Claude arbiter resolves split dimensions (or majority fallback
    # when no keys). Always returns a dict.
    agg = await catts_aggregate_with_arbiter(results)

    # If overall confidence is still very low, escalate one more agent and
    # re-aggregate (the arbiter then tie-breaks the wider evidence pool).
    if agg.get("confidence", 0.0) < 0.5:
        await emit(job_id, {"type": "line", "ok": True, "msg": "Low confidence — spawning 4th agent..."})
        extra = await run_single_audit(domain, n, job_id)
        results.append(extra)
        agg = await catts_aggregate_with_arbiter(results)

    await emit(
        job_id,
        {
            "type": "score",
            "score": agg["score"],
            "confidence": agg["confidence"],
            "dimensions": agg["dimensions"],
            "report_id": report_id,
        },
    )
    if emit_done:
        await emit(
            job_id,
            {
                "type": "done",
                "score": agg["score"],
                "confidence": agg["confidence"],
                "report_id": report_id,
            },
        )
    return agg


# ---------------------------------------------------------------------------
# Shared persistence — used by BOTH the /audit endpoint and the autonomous
# scout so they never diverge. Writes the aggregated CATTS result onto the
# Audit row, creates one AuditStep per dimension, and mirrors the score onto
# the Company. Defensive: returns the Company (refreshed) or None.
# ---------------------------------------------------------------------------

async def persist_audit_result(
    db: AsyncSession,
    audit_id: uuid.UUID,
    company_id: uuid.UUID,
    agg: dict,
) -> Optional["object"]:
    """Persist an aggregated audit result onto an existing Audit + Company.

    The caller owns the session lifecycle (commit happens here, but the session
    stays open so the caller can keep using the returned Company). Mirrors the
    exact field mapping the /audit endpoint used before it was factored out:

      - audit.score / audit.confidence  ← agg["score"] / agg["confidence"]
      - one AuditStep per dimension      (evidence stored as {"message": ...})
      - company.score / company.confidence + last_audited_at

    Returns the refreshed Company row (or None if either row is missing).
    """
    # Local imports to avoid a circular import at module load time.
    from ..models.audit import Audit, AuditStep
    from ..models.company import Company

    audit_row = await db.get(Audit, audit_id)
    if not audit_row:
        return None

    audit_row.score = agg.get("score")
    audit_row.confidence = agg.get("confidence")
    for dim, v in (agg.get("dimensions") or {}).items():
        db.add(
            AuditStep(
                audit_id=audit_id,
                dimension=dim,
                passed=bool(v.get("passed", False)),
                confidence=float(v.get("confidence", 0.5)),
                weight=v.get("weight"),
                evidence={"message": v.get("evidence", "") or ""},
                agent_votes=None,
            )
        )

    co = await db.get(Company, company_id)
    if co:
        co.score = agg.get("score")
        co.confidence = agg.get("confidence")
        co.last_audited_at = datetime.utcnow()

    await db.commit()
    return co
