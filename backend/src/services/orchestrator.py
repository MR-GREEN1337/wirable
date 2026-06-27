"""
Orchestrator (Wirable) — the deterministic if/else engine that drives a run.

A run is: recon -> classify(api|site) -> test(branch) -> score. It STOPS there.
The proxy steps (generate -> deploy -> verify) are gated behind
POST /run/{id}/proxy (see endpoints/proxy.py) so a human configures auth first.

All progress is emitted as the canonical SSE run-events from core.contracts via
the existing in-process SSE bus owned by test_service (emit / subscribe /
history + Condition replay) — that mechanism is reused, not reinvented.

Workflow diagram:
    recon -> classify(api|site) -> test(branch) -> score -> [gate]
             -> (POST /proxy) generate -> deploy -> verify
"""
from __future__ import annotations

import httpx
from loguru import logger

from ..core import contracts
from ..core.contracts import events
from . import test_service

# Probe knobs.
_PROBE_TIMEOUT_S = 8.0
_API_MARKERS = ("/openapi.json", "/.well-known/mcp.json", "/llms.txt", "/api")


def _normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw.rstrip("/")


def _domain_of(url: str) -> str:
    return (
        url.replace("https://", "")
        .replace("http://", "")
        .split("/")[0]
        .split("?")[0]
    )


async def _recon(run_id: str, url: str) -> dict:
    """Probe the target for agent-facing surfaces.

    Returns a recon bundle: {"found": {marker: bool}, "kind": "api"|"site",
    "evidence": str}. Defensive — any probe failure is treated as "absent".
    """
    found: dict[str, bool] = {}
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT_S, follow_redirects=True
    ) as client:
        for marker in _API_MARKERS:
            probe_url = url + marker
            ok = False
            try:
                resp = await client.get(probe_url)
                ok = resp.status_code < 400
            except Exception as exc:  # network/DNS/timeout — treat as absent
                logger.debug("recon probe failed for {}: {}", probe_url, exc)
            found[marker] = ok
            await test_service.emit(
                run_id,
                events.line(ok, f"probe {marker} -> {'200' if ok else 'absent'}"),
            )

    # Classification: any machine surface => api; else site (needs Playwright).
    has_api = any(
        found.get(m) for m in ("/openapi.json", "/.well-known/mcp.json", "/api")
    )
    kind = "api" if has_api else "site"
    if found.get("/openapi.json"):
        evidence = "OpenAPI spec served at /openapi.json"
    elif found.get("/.well-known/mcp.json"):
        evidence = "MCP manifest at /.well-known/mcp.json"
    elif found.get("/api"):
        evidence = "Reachable /api surface"
    else:
        evidence = "No machine surface found — treating as a human-facing site"
    return {"found": found, "kind": kind, "evidence": evidence}


async def run_workflow(run_id: str, url: str) -> dict:
    """Drive recon -> classify -> test -> score, emitting canonical SSE events.

    Stops after `score` (proxy generation is gated). Always terminates the SSE
    stream with `done` (or `error` on a hard failure). Returns the aggregated
    score dict from the test engine.
    """
    url = _normalize_url(url)
    if not url:
        await test_service.emit(run_id, events.error("empty or invalid url"))
        return {}

    domain = _domain_of(url)

    try:
        # --- recon -----------------------------------------------------------
        await test_service.emit(run_id, events.phase("recon", "start"))
        recon = await _recon(run_id, url)
        await test_service.emit(run_id, events.phase("recon", "done"))

        # --- classify --------------------------------------------------------
        await test_service.emit(
            run_id, events.classify(recon["kind"], recon["evidence"])
        )

        # --- test (branch: api vs site) -------------------------------------
        # The deterministic engine fans out N sandbox agents that drive the
        # canonical workflows. Daytona + Playwright + key-pool injection stay
        # intact inside test_service.run_test. It streams line/screenshot
        # events and emits its own "score" event; we suppress its terminal
        # "done" so we can append our contract-shaped score + done.
        await test_service.emit(run_id, events.phase("test", "start"))
        if recon["kind"] == "api":
            await test_service.emit(
                run_id, events.line(True, "branch: API workflow battery")
            )
        else:
            await test_service.emit(
                run_id, events.line(True, "branch: site (Playwright) workflow battery")
            )

        agg = await test_service.run_test(
            domain, run_id, report_id=None, emit_done=False
        )
        await test_service.emit(run_id, events.phase("test", "done"))

        # --- score -----------------------------------------------------------
        await test_service.emit(run_id, events.phase("score", "start"))
        total = int(agg.get("score", 0) or 0)
        dims = [
            {
                "dim": dim,
                "passed": bool(v.get("passed", False)),
                "evidence": str(v.get("evidence", "") or ""),
            }
            for dim, v in (agg.get("dimensions") or {}).items()
        ]
        await test_service.emit(run_id, events.score(total, dims))
        await test_service.emit(run_id, events.phase("score", "done"))

        # STOP — proxy steps are gated behind POST /run/{id}/proxy.
        await test_service.emit(run_id, events.done())
        return agg

    except Exception as exc:  # never leave the SSE stream hanging
        logger.exception("[orchestrator] run {} failed", run_id)
        await test_service.emit(run_id, events.error(str(exc)))
        return {}
