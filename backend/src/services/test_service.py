"""
Test service (Wirable) — the workflow TEST engine. Orchestrates N parallel
Daytona sandboxes that drive the canonical agent workflows against a target,
then aggregates results with CATTS into the 6 deterministic score dimensions.

This module also owns the in-process SSE bus (emit / subscribe) that the
orchestrator and the run endpoints reuse — keep that mechanism intact.

Historically named "audit_service"; the public concept is now a "run".
`run_test` is the canonical entry point; `run_audit` is kept as an alias.
"""
import asyncio
import base64
import json
import shlex
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
# Global concurrency cap
#
# Every run fans out N Daytona sandboxes, each driving a headless browser + a
# Claude key. A Product Hunt spike of concurrent runs would otherwise exhaust
# Daytona quota and the Claude key pool all at once. A module-level semaphore
# (lazily sized from settings.WIRABLE_MAX_CONCURRENT_RUNS) bounds how many runs
# do their heavy sandbox fan-out simultaneously; excess runs WAIT (queue) rather
# than error, so the UI shows a "queued" line instead of a hard failure.
# ---------------------------------------------------------------------------
_run_sem: Optional[asyncio.Semaphore] = None


def _get_run_sem() -> asyncio.Semaphore:
    """Lazily build the module-level run semaphore from settings.

    Lazy (not built at import time) so the size honours whatever the running
    process configured and so it binds to the live event loop. Floors at 1 so a
    misconfigured 0/negative value can never deadlock every run.
    """
    global _run_sem
    if _run_sem is None:
        try:
            n = int(getattr(settings, "WIRABLE_MAX_CONCURRENT_RUNS", 4) or 4)
        except Exception:
            n = 4
        _run_sem = asyncio.Semaphore(max(1, n))
    return _run_sem


def run_slots_busy() -> bool:
    """Best-effort heuristic: True when the run semaphore has no free slot.

    Used by the run-start path to decide whether to emit a "queued" line before
    the heavy work blocks. Never raises; a not-yet-built semaphore reads as free.
    """
    try:
        return _get_run_sem().locked()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Live screenshot streaming knobs (every agent streams — see _stream_audit_run).
# ---------------------------------------------------------------------------
_SHOT_POLL_S = 1.2          # sweep /tmp/screenshots ~2x faster so frames feel live
_SHOT_HARD_TIMEOUT_S = 900  # ceiling on the streaming loop (matches exec timeout)
_SHOT_MAX_FRAMES = 160      # before+after shots per step ~2x the frame count; don't truncate
_SHOT_MAX_B64 = 2_500_000   # skip any single frame whose base64 exceeds this.
# A full rendered page screenshot is commonly 400-700KB of base64; the old
# 250KB cap silently dropped EVERY frame on rich pages -> the live viewport
# loaded forever. 2.5MB covers full-page captures while still bounding payload.

# Path to the harness audit prompt
AUDIT_PROMPT_PATH = (
    Path(__file__).parent.parent.parent.parent / "harness" / "prompts" / "audit.md"
)
# The SOTA audit driver — runs inside the sandbox, drives agent-browser + Claude.
AUDIT_DRIVER_PATH = Path(__file__).parent.parent / "harness" / "audit_driver.py"
SKILLS_PATH = Path(__file__).parent.parent / "harness" / "skills.py"

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

# ---------------------------------------------------------------------------
# Human-in-the-loop input bus
#
# When the in-sandbox agent is blocked (OTP / 2FA / a login it lacks) it writes
# /tmp/need_input.json and polls /tmp/human_input.json. The run's camera loop
# (_stream_audit_run) bridges the two: it surfaces the request as a `needs_input`
# SSE event and, once the human answers via POST /run/{id}/input, relays the
# value back into the sandbox. This module-level registry is that mailbox.
# ---------------------------------------------------------------------------
_human_input: dict[str, str] = {}


def set_human_input(run_id: str, value: str) -> None:
    """Deposit a human-supplied value for a run (called by POST /run/{id}/input).

    The camera loop pops it on its next poll and writes it into the sandbox.
    """
    _human_input[run_id] = value


def _pop_human_input(run_id: str) -> Optional[str]:
    """Atomically take any pending human value for a run (None if absent)."""
    return _human_input.pop(run_id, None)

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


def get_history(job_id: str, since: int = 0) -> tuple[list[dict], int, bool]:
    """Cursor-based read of a job's event history (for the polling state endpoint).

    Returns ``(events, cursor, done)`` where ``events`` is the slice of history
    strictly after index ``since``, ``cursor`` is the new index the caller should
    send back next time (== total number of events seen so far), and ``done`` is
    True once a terminal (done/error) event has been emitted.

    Cheap, idempotent, lock-free read of the in-memory history list — safe to hit
    on a sub-second poll. Slicing a Python list is atomic w.r.t. the GIL, and
    appends elsewhere never mutate earlier indices, so no Condition is needed.
    """
    hist = _history.get(job_id, [])
    if since < 0:
        since = 0
    events = hist[since:]
    cursor = len(hist)
    return events, cursor, bool(_done.get(job_id))


# ---------------------------------------------------------------------------
# Live screenshot streaming (agent 0 = the "camera")
# ---------------------------------------------------------------------------

def _frame_seq(path: str) -> int:
    """Extract the zero-padded numeric stem from /tmp/screenshots/NNNN.jpg."""
    try:
        return int(Path(path).stem)
    except Exception:
        return 0


async def _emit_frame(sb, job_id: str, jpg_path: str, seq: int, agent_id: int) -> bool:
    """Read one screenshot + sidecar and emit a 'screenshot' SSE event.

    The event is tagged with `agent` (0..N-1) so the frontend can split the
    stream into one live tile per agent. `seq` is the per-agent frame number
    (the driver writes /tmp/screenshots/NNNN.jpg in each sandbox independently),
    so seqs collide ACROSS agents — the frontend keys by `${agent}:${seq}` to
    de-dupe without one agent's frame masking another's.

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
                "agent": agent_id,
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


async def _sweep_frames(
    sb, job_id: str, agent_id: int, seen: set[int], emitted: list[int]
) -> None:
    """List /tmp/screenshots/*.jpg and emit any new frames, honoring the frame cap.

    `seen` tracks every seq we've inspected (so we never re-read); `emitted`
    tracks seqs we actually streamed (to enforce _SHOT_MAX_FRAMES). When the cap
    is hit we still emit the *latest* frame (skip the middle) so the live view
    always shows where the agent is now.

    The per-agent frame cap is unchanged — each agent keeps its OWN seen/emitted
    sets, so the cap applies per agent, not across the whole run. Each emitted
    frame is tagged with `agent_id`.
    """
    try:
        files = await sb.list_files("/tmp/screenshots/*.jpg")
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
        if await _emit_frame(sb, job_id, jpg_path, seq, agent_id):
            emitted.append(seq)


async def _bridge_human_input(sb, job_id: str, emitted_requests: set[str]) -> None:
    """Bridge the in-sandbox HITL contract to the SSE bus, once per camera poll.

    Two halves, both fully defensive (any failure is swallowed so screenshot
    streaming is never interrupted):

      1. OUTBOUND ask: if the driver wrote /tmp/need_input.json and we haven't
         already surfaced that request_id, emit a `needs_input` SSE event so the
         frontend can prompt the human.
      2. INBOUND answer: if a human value is waiting in the input bus, write it
         into the sandbox at /tmp/human_input.json (where the driver polls) and
         emit the "human input received, resuming…" line.
    """
    from ..core.contracts import events as _events

    # 1) Outbound: surface a pending request from the sandbox.
    try:
        raw = await sb.exec("cat /tmp/need_input.json 2>/dev/null", timeout=15)
        if raw and raw.strip():
            req = json.loads(raw)
            rid = str(req.get("request_id", "") or "")
            if rid and rid not in emitted_requests:
                emitted_requests.add(rid)
                await emit(
                    job_id,
                    _events.needs_input(
                        prompt=str(req.get("prompt", "") or "Input needed"),
                        kind=str(req.get("kind", "text") or "text"),
                        request_id=rid,
                    ),
                )
    except Exception as e:
        logger.debug("need_input bridge (outbound) failed (non-fatal): {}", e)

    # 2) Inbound: relay a human answer into the sandbox.
    try:
        value = _pop_human_input(job_id)
        if value is not None:
            payload = json.dumps({"value": value})
            b64 = base64.b64encode(payload.encode()).decode()
            # base64 → file avoids any quoting/heredoc hazards with arbitrary input.
            await sb.exec(
                f"echo {shlex.quote(b64)} | base64 -d > /tmp/human_input.json",
                timeout=15,
            )
            await emit(
                job_id,
                {"type": "line", "ok": True, "msg": "human input received, resuming…"},
            )
    except Exception as e:
        logger.debug("need_input bridge (inbound) failed (non-fatal): {}", e)


async def _stream_audit_run(
    sb, job_id: str, agent_id: int, command: str, *, bridge_input: bool = False
) -> None:
    """Run the audit command in the background and stream screenshots live.

    Every agent runs through this path so all N tiles show their own frames; each
    emitted screenshot is tagged with `agent_id` and keeps its own per-agent
    seen/emitted sets (so the frame cap is per agent and seqs don't clobber each
    other across agents — the frontend keys by `${agent}:${seq}`).

    `bridge_input` gates the human-in-the-loop bridge to a SINGLE agent (the
    camera, agent 0): the HITL contract is one mailbox per run, so only one agent
    should surface the request and relay the answer into its sandbox. The other
    agents stream screenshots only.

    Defensive throughout: if the sandbox/harness produces no screenshots (or any
    streaming step errors) the audit still completes — the command runs to
    completion and the caller reads /tmp/output.json as usual.
    """
    cmd_id = await sb.exec_bg("mkdir -p /tmp/screenshots; " + command)

    seen: set[int] = set()
    emitted: list[int] = []
    emitted_requests: set[str] = set()  # human-input request_ids already surfaced
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SHOT_HARD_TIMEOUT_S

    while True:
        try:
            done = await sb.is_command_done(cmd_id)
        except Exception as e:
            logger.debug("is_command_done errored, assuming still running: {}", e)
            done = False

        try:
            await _sweep_frames(sb, job_id, agent_id, seen, emitted)
        except Exception as e:
            logger.debug("screenshot sweep failed (non-fatal): {}", e)

        # Human-in-the-loop bridge — surface any agent input request + relay any
        # waiting human answer into the sandbox. Only the designated agent runs
        # this (one mailbox per run). Fully defensive (never breaks streaming).
        if bridge_input:
            try:
                await _bridge_human_input(sb, job_id, emitted_requests)
            except Exception as e:
                logger.debug("human-input bridge failed (non-fatal): {}", e)

        if done:
            break
        if loop.time() > deadline:
            logger.warning("[{}] audit stream hit hard timeout {}s", agent_id, _SHOT_HARD_TIMEOUT_S)
            break
        await asyncio.sleep(_SHOT_POLL_S)

    # Final sweep — catch any last frames written just before exit.
    try:
        await _sweep_frames(sb, job_id, agent_id, seen, emitted)
    except Exception as e:
        logger.debug("final screenshot sweep failed (non-fatal): {}", e)


# ---------------------------------------------------------------------------
# Single-agent runner
# ---------------------------------------------------------------------------

def _access_env(access: Optional[dict]) -> dict[str, str]:
    """Map an access grant ({mode,email,password,api_key,token,notes}) to the
    WIRABLE_* env vars the in-sandbox driver reads. Only set what's provided so
    the driver can tell which auth path it has. Empty dict when no access."""
    out: dict[str, str] = {}
    if not access:
        return out
    if access.get("email"):
        out["WIRABLE_LOGIN_EMAIL"] = str(access["email"])
    if access.get("password"):
        out["WIRABLE_LOGIN_PASSWORD"] = str(access["password"])
    if access.get("api_key"):
        out["WIRABLE_API_KEY"] = str(access["api_key"])
    if access.get("token"):
        out["WIRABLE_BEARER"] = str(access["token"])
    if access.get("notes"):
        out["WIRABLE_ACCESS_NOTES"] = str(access["notes"])
    # White-box: when a repo + token are bound to the run, the driver clones it and
    # runs the code skills (scan_routes/read_auth/find_openapi) for deep analysis.
    if access.get("repo"):
        out["WIRABLE_REPO"] = str(access["repo"])
    if access.get("github_token"):
        out["WIRABLE_GH_TOKEN"] = str(access["github_token"])
    return out


async def run_single_audit(
    domain: str, agent_id: int, job_id: str, access: Optional[dict] = None
) -> dict:
    """Spawn one Daytona sandbox, run the audit agent, return structured result."""
    try:
        await emit(job_id, {"type": "line", "ok": True, "msg": f"[{agent_id}] spawning sandbox"})

        url = domain if domain.startswith("http") else f"https://{domain}"
        driver_src = AUDIT_DRIVER_PATH.read_text() if AUDIT_DRIVER_PATH.exists() else ""
        skills_src = SKILLS_PATH.read_text() if SKILLS_PATH.exists() else ""

        # Each parallel agent pulls its OWN key from the pool so N agents spread
        # across N keys (defends against per-key rate limits during fan-out).
        key = key_pool.next_key()
        env: dict[str, str] = {}
        if key:
            env["ANTHROPIC_API_KEY"] = key
            env["ANTHROPIC_MODEL"] = settings.ANTHROPIC_MODEL
        # Human-in-the-loop pre-run access: give the creds to ALL agents so any
        # can sign in and exercise the authed product.
        env.update(_access_env(access))
        env = env or None
        async with DaytonaClient.sandbox(env=env) as sb:
            # The SOTA driver drives agent-browser (a11y-tree observation) + Claude
            # verdict, writing /tmp/output.json + /tmp/screenshots/. Uploaded each
            # run so it's iterable without rebuilding the snapshot.
            await sb.upload("/tmp/audit_driver.py", driver_src.encode())
            # The skill library (the "do" layer) — uploaded alongside the driver so
            # `import skills` resolves; without it the driver degrades to live-only.
            if skills_src:
                await sb.upload("/tmp/skills.py", skills_src.encode())
            await emit(job_id, {"type": "line", "ok": True, "msg": f"[{agent_id}] driving agent-browser on {domain}"})

            # Agent 0 is the "camera": deep agentic browser-use exploration
            # (signup → dashboard → core action → error/retry, 50+ frames). The
            # other agents run a fast probe pass for consensus without 3x the
            # deep cost — but EVERY agent now streams its screenshots so all N
            # tiles in the live grid show their own frames (each frame is tagged
            # with this agent_id; seqs are namespaced per agent on the frontend).
            mission = "deep" if agent_id == 0 else "fast"
            audit_cmd = f"cd /tmp && python3 /tmp/audit_driver.py {shlex.quote(url)} {mission} 2>&1 || true"
            # Only agent 0 bridges the human-in-the-loop mailbox (one per run).
            try:
                await _stream_audit_run(
                    sb, job_id, agent_id, audit_cmd, bridge_input=(agent_id == 0)
                )
            except Exception as e:
                logger.debug("[{}] screenshot streaming failed (non-fatal): {}", agent_id, e)
                # If streaming aborted before the command ran to completion,
                # fall back to a plain blocking exec so /tmp/output.json exists.
                await sb.exec(audit_cmd, timeout=900)

            raw = await sb.read("/tmp/output.json")

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
        import traceback
        logger.error("run_single_audit[{}] failed: {}\n{}", agent_id, exc, traceback.format_exc())
        from ..agents.catts import clean_evidence
        msg = clean_evidence(str(exc))
        await emit(job_id, {"type": "line", "ok": False, "msg": f"error: {msg}"})
        return {
            "domain": domain,
            "dimensions": {
                dim: {"passed": False, "confidence": 0.5, "evidence": msg}
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
    access: Optional[dict] = None,
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

    # Global concurrency cap: the N-sandbox fan-out (and any escalation agent) is
    # the heavy, dollar-burning work. Hold a semaphore slot around it so a traffic
    # spike queues runs instead of exhausting Daytona + the Claude key pool. When
    # at capacity this awaits a free slot (the run-start path already emitted a
    # "queued" line so the UI shows the wait); released in finally.
    sem = _get_run_sem()
    await sem.acquire()
    try:
        tasks = [run_single_audit(domain, i, job_id, access=access) for i in range(n)]
        results = list(await asyncio.gather(*tasks, return_exceptions=False))

        await emit(job_id, {"type": "line", "ok": True, "msg": "Aggregating CATTS consensus..."})
        # N agents → Claude arbiter resolves split dimensions (or majority fallback
        # when no keys). Always returns a dict.
        agg = await catts_aggregate_with_arbiter(results)

        # If overall confidence is still very low, escalate one more agent and
        # re-aggregate (the arbiter then tie-breaks the wider evidence pool).
        if agg.get("confidence", 0.0) < 0.5:
            await emit(job_id, {"type": "line", "ok": True, "msg": "Low confidence — spawning 4th agent..."})
            extra = await run_single_audit(domain, n, job_id, access=access)
            results.append(extra)
            agg = await catts_aggregate_with_arbiter(results)
    finally:
        sem.release()

    # Collect the "Wrapped"-style interaction cards (the driver/Claude emits them
    # per agent). Pick the richest set; emit as its own event for the report grid.
    cards: list = []
    for r in results:
        c = r.get("cards") if isinstance(r, dict) else None
        if isinstance(c, list) and len(c) > len(cards):
            cards = c
    if cards:
        await emit(job_id, {"type": "cards", "cards": cards})

    # Only emit the terminal score/done when we're the top-level driver. When the
    # orchestrator drives us (emit_done=False) it owns the contract-shaped score
    # event (events.score → key "total"); emitting our own here caused a second,
    # mis-keyed score event ("score" not "total") that flashed a null score in the
    # UI before the real one landed.
    if emit_done:
        # Frontend reads `total` (see core.contracts.events.score). Keep the extra
        # fields for the standalone/scout path, but the canonical key is `total`.
        await emit(
            job_id,
            {
                "type": "score",
                "total": agg["score"],
                "confidence": agg["confidence"],
                "dimensions": agg["dimensions"],
                "cards": cards,
                "report_id": report_id,
            },
        )
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


# ---------------------------------------------------------------------------
# Canonical Wirable names (aliases). The public concept is a "run"/"test"; the
# underlying engine kept its historical `run_audit` / `persist_audit_result`
# names to avoid churning every call site at once.
# ---------------------------------------------------------------------------

run_test = run_audit
persist_test_result = persist_audit_result
