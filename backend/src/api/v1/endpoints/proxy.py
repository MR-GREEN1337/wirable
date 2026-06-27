"""
Proxy endpoints (Wirable) — generate + HOST the live MCP proxy that fixes the
semantic breakage the test run found, then verify the before/after.

  POST /api/v1/run/{run_id}/proxy   body {auth:{...}}  -> {mcp_url}
        (configure auth -> generate -> deploy -> verify; gated behind a run;
         streams the generate/deploy/verify phases on the run's SSE bus)
  GET/POST /api/v1/proxy/{id}/mcp                        (ProxyRuntime, MCP-over-HTTP)
  GET  /api/v1/proxy/{id}/.well-known/mcp.json           (discovery manifest)
  POST /api/v1/proxy/{id}/keys                           (owner-issued scoped key)

MCP-over-HTTP shape (minimal but correct JSON-RPC):
  request:  {"jsonrpc":"2.0","id":<n>,"method":"tools/list"|"tools/call",
             "params":{"name":"<tool>","arguments":{...}}}
  response: {"jsonrpc":"2.0","id":<n>,"result":{...}}  | {"...","error":{...}}

Auth: tools/call requires an owner-issued scoped agent key, supplied as
`Authorization: Bearer <key>` (or `X-Agent-Key: <key>`). The owner's UPSTREAM
credential is injected server-side by the runtime and never exposed to callers.
"""
import inspect
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from loguru import logger

from ....core.auth import get_current_user
from ....core.contracts import events
from ....services import test_service, proxy_generator, proxy_runtime

router = APIRouter(tags=["proxy"])


class ProxyRequest(BaseModel):
    auth: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Generator adapter — the generator is owned by another agent and may expose
# either the spec'd `generate_proxy_config(run_id, auth)` or the staged stub
# `generate_proxy_config(run_id, auth, *, target_id=..., base_url=..., kind=...)`.
# Call it correctly regardless of which signature is live.
# ---------------------------------------------------------------------------

async def _gen_config(run_id: str, auth: Optional[dict], target_id: str):
    fn = proxy_generator.generate_proxy_config
    try:
        params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = set()
    kwargs: dict[str, Any] = {}
    if "target_id" in params:
        kwargs["target_id"] = target_id
    return await fn(run_id, auth, **kwargs)


async def _verify(run_id: str, mcp_url: str):
    """Call the generator's verify_against_proxy if present; else (None, None)."""
    fn = getattr(proxy_generator, "verify_against_proxy", None)
    if fn is None:
        return None, None
    try:
        result = await fn(run_id, mcp_url)
    except Exception as exc:
        logger.debug("verify_against_proxy failed: %s", exc)
        return None, None
    if isinstance(result, (tuple, list)) and len(result) == 2:
        return result[0], result[1]
    return None, None


# ---------------------------------------------------------------------------
# POST /run/{run_id}/proxy  — the gated generate -> deploy -> verify pipeline
# ---------------------------------------------------------------------------


@router.post("/run/{run_id}/proxy")
async def create_proxy(run_id: str, body: ProxyRequest, bg: BackgroundTasks):
    """Configure auth, generate + DEPLOY (host) the proxy, then verify
    before/after. Returns the hosted mcp_url immediately; phases stream on the
    run's SSE bus.
    """
    proxy_id = run_id  # 1:1 with the run.
    mcp_url = proxy_runtime.mcp_url_for(proxy_id)

    async def _generate_deploy_verify():
        try:
            # --- generate ----------------------------------------------------
            await test_service.emit(run_id, events.phase("generate", "start"))
            config = await _gen_config(run_id, body.auth, proxy_id)
            await test_service.emit(run_id, events.phase("generate", "done"))

            # --- deploy (persist + host) -------------------------------------
            await test_service.emit(run_id, events.phase("deploy", "start"))
            # Persist the ProxyConfig + the owner credential (server-side only)
            # so the ProxyRuntime can load + serve it by id, and inject auth.
            await proxy_runtime.persist_proxy_config(
                proxy_id,
                config,
                auth_secret=body.auth,
                mcp_url=mcp_url,
            )
            tools = [
                {"name": t.name, "description": t.description} for t in config.tools
            ]
            await test_service.emit(
                run_id, events.proxy_ready(mcp_url, tools, config.advertise)
            )
            await test_service.emit(run_id, events.phase("deploy", "done"))

            # --- verify ------------------------------------------------------
            await test_service.emit(run_id, events.phase("verify", "start"))
            before, after = await _verify(run_id, mcp_url)
            # Defensive defaults when the verifier isn't wired yet.
            before = int(before) if before is not None else 0
            after = int(after) if after is not None else 0
            await test_service.emit(run_id, events.verify(before=before, after=after))
            await test_service.emit(run_id, events.phase("verify", "done"))

            await test_service.emit(run_id, events.done())
        except Exception as exc:
            logger.exception("[proxy] generate/deploy/verify failed for %s", run_id)
            await test_service.emit(run_id, events.error(str(exc)))

    bg.add_task(_generate_deploy_verify)
    return {"mcp_url": mcp_url, "proxy_id": proxy_id}


# ---------------------------------------------------------------------------
# ProxyRuntime surface — MCP-over-HTTP (JSON-RPC)
# ---------------------------------------------------------------------------


def _rpc_result(req_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}},
        status_code=status,
    )


def _extract_agent_key(authorization: Optional[str], x_agent_key: Optional[str]) -> Optional[str]:
    if x_agent_key:
        return x_agent_key.strip()
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token
    return None


@router.get("/proxy/{proxy_id}/mcp")
async def proxy_mcp_get(proxy_id: str):
    """GET probe — returns the MCP server descriptor + tool list (no auth needed
    to discover; tools/call still requires a scoped key)."""
    runtime = await proxy_runtime.load_runtime(proxy_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")
    tools = await runtime.list_tools()
    return {
        "name": f"wirable-proxy-{proxy_id}",
        "protocol": "mcp",
        "transport": "http",
        "methods": ["tools/list", "tools/call"],
        "tools": tools,
    }


@router.post("/proxy/{proxy_id}/mcp")
async def proxy_mcp_post(
    proxy_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_agent_key: Optional[str] = Header(default=None, alias="X-Agent-Key"),
):
    """MCP-over-HTTP JSON-RPC entrypoint: tools/list and tools/call."""
    runtime = await proxy_runtime.load_runtime(proxy_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error: invalid JSON", status=400)

    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if method == "tools/list":
        tools = await runtime.list_tools()
        return _rpc_result(req_id, {"tools": tools})

    if method == "tools/call":
        # tools/call requires a valid owner-issued scoped agent key.
        agent_key = _extract_agent_key(authorization, x_agent_key)
        if not runtime.broker.validate_agent_key(agent_key):
            return _rpc_error(
                req_id, -32001, "Unauthorized: missing or invalid agent key", status=401
            )
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not name:
            return _rpc_error(req_id, -32602, "Invalid params: missing tool name")
        normalized = await runtime.call_tool(name, arguments)
        # MCP content envelope wrapping our normalized {success,error_code,...}.
        return _rpc_result(
            req_id,
            {
                "content": [{"type": "text", "text": _json_text(normalized)}],
                "isError": not normalized.get("success", False),
                "structuredContent": normalized,
            },
        )

    return _rpc_error(req_id, -32601, f"Method not found: {method}")


def _json_text(obj: Any) -> str:
    import json

    try:
        return json.dumps(obj)
    except Exception:
        return str(obj)


# ---------------------------------------------------------------------------
# Discovery manifest
# ---------------------------------------------------------------------------


@router.get("/proxy/{proxy_id}/.well-known/mcp.json")
async def proxy_well_known(proxy_id: str):
    """Discovery manifest — serves the persisted advertise.well_known body."""
    advertise = await proxy_runtime.get_advertise(proxy_id)
    if not advertise or not advertise.get("well_known"):
        raise HTTPException(status_code=404, detail="No proxy manifest for this id")
    return JSONResponse(advertise["well_known"])


# ---------------------------------------------------------------------------
# Owner-issued scoped agent keys (owner auth required)
# ---------------------------------------------------------------------------


class IssueKeyRequest(BaseModel):
    scopes: Optional[list[str]] = None


@router.post("/proxy/{proxy_id}/keys")
async def issue_key(
    proxy_id: str,
    body: IssueKeyRequest,
    user: dict = Depends(get_current_user),
):
    """Owner mints a scoped agent key for an agent to call this proxy. Returned
    ONCE (not retrievable later). Requires the owner to be authenticated.
    """
    try:
        return await proxy_runtime.issue_agent_key(proxy_id, body.scopes)
    except LookupError:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
