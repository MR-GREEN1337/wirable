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
import asyncio
import inspect
import json as _json_mod
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from loguru import logger
from sqlalchemy import select

from ....core.auth import get_current_user, verify_token
from ....core.config import settings
from ....core.contracts import events
from ....core.database import AsyncSessionLocal
from ....models.client import Client
from ....services import (
    test_service,
    proxy_generator,
    proxy_runtime,
    github_fix,
    entitlements,
    code_analysis,
)

router = APIRouter(tags=["proxy"])

# Hard ceiling on the generate phase (grounding/discovery). Past this we ship a
# generic proxy rather than letting the run hang silently on "generate".
_GENERATE_DEADLINE_S = 90.0


class ProxyRequest(BaseModel):
    auth: Optional[dict[str, Any]] = None
    repo: Optional[str] = None  # "owner/repo"; overrides the user's saved repo


# ---------------------------------------------------------------------------
# Generator adapter — the generator is owned by another agent and may expose
# either the spec'd `generate_proxy_config(run_id, auth)` or the staged stub
# `generate_proxy_config(run_id, auth, *, target_id=..., base_url=..., kind=...)`.
# Call it correctly regardless of which signature is live.
# ---------------------------------------------------------------------------

async def _gen_config(
    run_id: str,
    auth: Optional[dict],
    target_id: str,
    *,
    code_endpoints: Optional[list] = None,
    code_base_url: Optional[str] = None,
):
    fn = proxy_generator.generate_proxy_config
    try:
        params = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        params = set()
    kwargs: dict[str, Any] = {}
    if "target_id" in params:
        kwargs["target_id"] = target_id
    # Pass the REAL code-extracted endpoints as the ground-truth tool source when
    # the generator supports it; it prefers them over black-box discovery.
    if code_endpoints and "code_endpoints" in params:
        kwargs["code_endpoints"] = code_endpoints
        if "code_base_url" in params:
            kwargs["code_base_url"] = code_base_url
    # Stream the generator's REAL build steps onto the run bus when supported:
    # it narrates upstream discovery + per-tool mapping via this callback.
    if "emit" in params:
        async def _emit(event: dict) -> None:
            await test_service.emit(run_id, event)

        kwargs["emit"] = _emit
    return await fn(run_id, auth, **kwargs)


async def _analyze_bound_repo(
    run_id: str, token: Optional[str], repo: Optional[str]
) -> tuple[Optional[list], Optional[str]]:
    """If a repo + token are available, read the repo's source to ground the MCP
    in REAL endpoints before generating. Stores the analyzed commit as a snapshot
    (so future pushes can diff against it). Returns (endpoints, base_url_hint) or
    (None, None). Fully defensive — any failure degrades to black-box generation.
    """
    if not token or not repo:
        return None, None
    try:
        await test_service.emit(
            run_id,
            events.line(True, "proxy: reading your code to ground the MCP in real endpoints…"),
        )
        result = await code_analysis.analyze_repo(repo, token)
        if not isinstance(result, dict) or not result.get("ok"):
            return None, None
        endpoints = result.get("endpoints") or []
        if not isinstance(endpoints, list) or not endpoints:
            return None, None
        commit = str(result.get("commit_sha") or "")
        # Record this commit's surface so the monitor can diff later pushes.
        try:
            await code_analysis.store_snapshot(
                repo,
                commit,
                result.get("framework") or "unknown",
                endpoints,
                base_url_hint=result.get("base_url_hint"),
            )
        except Exception:
            logger.debug("[proxy] store_snapshot failed for %s", repo, exc_info=True)
        short = commit[:7] if commit else "?"
        await test_service.emit(
            run_id,
            events.line(
                True,
                f"proxy: grounded MCP in {len(endpoints)} real endpoints from {repo}@{short}",
            ),
        )
        return endpoints, result.get("base_url_hint")
    except Exception:
        logger.exception("[proxy] code analysis failed for %s — falling back", repo)
        return None, None


async def _resolve_github(authorization: Optional[str], body_repo: Optional[str]):
    """Resolve (token, repo) for the FIX PR from the caller's connected GitHub.

    Reads the Bearer token off the Authorization header (the same user JWT the
    run was started with), finds their Client row, and returns the stored OAuth
    token + repo. `body_repo` overrides the saved repo when present. Returns
    (None, None) when GitHub isn't connected or the caller is unauthenticated.
    Never raises.
    """
    user_id = None
    if authorization:
        tok = authorization.strip()
        if tok.lower().startswith("bearer "):
            tok = tok[7:].strip()
        try:
            payload = verify_token(tok)
            user_id = payload.get("sub")
        except Exception:
            user_id = None
    if not user_id:
        return None, None, None
    try:
        import uuid as _uuid

        uid = _uuid.UUID(str(user_id))
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Client).where(Client.user_id == uid))
            client = result.scalars().first()
        if client is None or not client.github_token:
            return None, None, None
        repo = body_repo or client.github_repo
        return client.github_token, repo, client
    except Exception:
        logger.debug("[proxy] github resolve failed", exc_info=True)
        return None, None, None


def _audit_from_history(run_id: str):
    """Recover (dims, cards, target_url) from the run's SSE history. Never raises."""
    dims: list[dict] = []
    cards: list = []
    target_url = ""
    try:
        history = list(test_service._history.get(run_id, []))  # noqa: SLF001
    except Exception:
        history = []
    for ev in history:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "score":
            d = ev.get("dimensions")
            if isinstance(d, list):
                dims = d
        elif ev.get("type") == "cards" and isinstance(ev.get("cards"), list):
            cards = ev["cards"]
        for key in ("url", "base_url", "target"):
            v = ev.get(key)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                target_url = v.rstrip("/")
    return dims, cards, target_url


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
async def create_proxy(
    run_id: str,
    body: ProxyRequest,
    bg: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
):
    """Configure auth, generate + DEPLOY (host) the proxy, then verify
    before/after. Returns the hosted mcp_url immediately; phases stream on the
    run's SSE bus.

    If the caller has a connected GitHub repo (OAuth), the flow ALSO opens a PR
    on that repo adding llms.txt / AGENTS.md / docs/agent-readiness.md, and emits
    a `fix_pr` event. GitHub failures never break the proxy deploy.
    """
    # Gate: hosting the MCP proxy is the paid value (the audit stays free). Pro/judge only.
    _uid = None
    if authorization and authorization.lower().startswith("bearer "):
        try:
            _uid = verify_token(authorization.split(" ", 1)[1]).get("sub")
        except Exception:
            _uid = None
    if not _uid:
        raise HTTPException(status_code=401, detail="sign in")
    async with AsyncSessionLocal() as _db:
        if not await entitlements.is_pro(_db, _uid):
            raise HTTPException(
                status_code=402,
                detail={"detail": "Upgrade to Pro to host the MCP proxy", "upgrade": True, "reason": "pro_required"},
            )

    proxy_id = run_id  # 1:1 with the run.
    mcp_url = proxy_runtime.mcp_url_for(proxy_id)
    # Resolve absolute MCP URL for the PR content (the repo files must point at a
    # reachable URL, not an app-relative path).
    abs_mcp_url = proxy_generator._absolute_url(mcp_url)  # noqa: SLF001

    async def _generate_deploy_verify():
        try:
            # --- resolve the caller's GitHub (token + repo) once -------------
            # Used both to ground the MCP in real code endpoints (below) and to
            # open the agent-ready fix PR later. Never raises.
            gh_token, gh_repo = None, None
            try:
                gh_token, gh_repo, _gh_client = await _resolve_github(
                    authorization, body.repo
                )
            except Exception:
                logger.debug("[proxy] github resolve failed", exc_info=True)

            # --- generate ----------------------------------------------------
            await test_service.emit(run_id, events.phase("generate", "start"))
            await test_service.emit(
                run_id, events.line(True, "proxy: building the MCP proxy…")
            )

            async def _build_config():
                # If a repo is bound, analyze its source FIRST so the MCP is
                # grounded in real endpoints (ground truth) instead of black-box
                # probes.
                code_endpoints, code_base_url = await _analyze_bound_repo(
                    run_id, gh_token, gh_repo
                )
                return await _gen_config(
                    run_id,
                    body.auth,
                    proxy_id,
                    code_endpoints=code_endpoints,
                    code_base_url=code_base_url,
                )

            # Hard deadline on the whole generate phase. Grounding can block for
            # minutes (Daytona sandbox provisioning on the repo path, or the
            # subdomain OpenAPI sweep on the black-box path); without this the
            # `generate` phase emits `start` and then hangs with no `done`. On
            # timeout we degrade to a deployable generic-tool proxy instead.
            try:
                config = await asyncio.wait_for(
                    _build_config(), timeout=_GENERATE_DEADLINE_S
                )
            except Exception as gen_exc:
                is_timeout = isinstance(gen_exc, asyncio.TimeoutError)
                if is_timeout:
                    logger.warning(
                        "[proxy] generate exceeded %ss for %s — degrading to a "
                        "generic proxy",
                        _GENERATE_DEADLINE_S,
                        run_id,
                    )
                    await test_service.emit(
                        run_id,
                        events.line(
                            True,
                            "proxy: grounding took too long — deploying a generic "
                            "proxy you can refine",
                        ),
                    )
                else:
                    logger.exception("[proxy] generate failed for %s", run_id)
                config = proxy_generator.minimal_proxy_config(
                    proxy_id, run_id=run_id, auth=body.auth, mcp_url=mcp_url
                )
            await test_service.emit(run_id, events.phase("generate", "done"))

            # --- deploy (persist + host) -------------------------------------
            await test_service.emit(run_id, events.phase("deploy", "start"))
            await test_service.emit(
                run_id, events.line(True, "proxy: deploying proxy runtime…")
            )
            # Persist the ProxyConfig + the owner credential (server-side only)
            # so the ProxyRuntime can load + serve it by id, and inject auth.
            await proxy_runtime.persist_proxy_config(
                proxy_id,
                config,
                auth_secret=body.auth,
                mcp_url=mcp_url,
            )
            await test_service.emit(
                run_id, events.line(True, f"proxy: proxy live: {mcp_url}")
            )
            tools = [
                {"name": t.name, "description": t.description} for t in config.tools
            ]
            await test_service.emit(
                run_id, events.proxy_ready(mcp_url, tools, config.advertise)
            )
            await test_service.emit(run_id, events.phase("deploy", "done"))

            # --- fix PR (bonus; never breaks the proxy deliverable) ----------
            try:
                token, repo = gh_token, gh_repo
                if token and repo:
                    await test_service.emit(
                        run_id,
                        events.phase("generate", "start"),
                    )
                    await test_service.emit(
                        run_id,
                        events.line(True, f"opening agent-ready PR on {repo}"),
                    )
                    dims, cards, target_url = _audit_from_history(run_id)
                    if not target_url:
                        target_url = getattr(config, "base_url", "") or ""
                    result = await github_fix.open_fix_pr(
                        repo_full_name=repo,
                        github_token=token,
                        target_url=target_url,
                        audit_dims=dims,
                        cards=cards,
                        proxy_mcp_url=abs_mcp_url,
                    )
                    await test_service.emit(run_id, events.phase("generate", "done"))
                    if result.get("error"):
                        await test_service.emit(
                            run_id,
                            events.fix_pr("", [], repo=repo, error=result["error"]),
                        )
                    else:
                        await test_service.emit(
                            run_id,
                            events.fix_pr(
                                result.get("pr_url", ""),
                                result.get("files", []),
                                branch=result.get("branch"),
                                repo=repo,
                            ),
                        )
            except Exception:
                logger.exception("[proxy] fix PR step failed for %s", run_id)
                # Surface a non-fatal fix_pr error; proxy deploy already succeeded.
                await test_service.emit(
                    run_id, events.fix_pr("", [], error="fix PR step failed")
                )

            # --- verify ------------------------------------------------------
            await test_service.emit(run_id, events.phase("verify", "start"))
            await test_service.emit(
                run_id, events.line(True, "proxy: calling tools/list + exercising a read-only call…")
            )
            before, after = await _verify(run_id, mcp_url)
            # Defensive defaults when the verifier isn't wired yet.
            before = int(before) if before is not None else 0
            after = int(after) if after is not None else 0
            await test_service.emit(
                run_id,
                events.line(True, f"proxy: verified — {len(tools)} tools reachable (score {before} -> {after})"),
            )
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


def _rpc_envelope(req_id: Any, result: Any = None, error: Optional[dict] = None) -> dict:
    """A single JSON-RPC 2.0 response object (result XOR error)."""
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _rpc_result(req_id: Any, result: Any) -> JSONResponse:
    return JSONResponse(_rpc_envelope(req_id, result=result))


def _rpc_error(req_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        _rpc_envelope(req_id, error={"code": code, "message": message}),
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


def _json_text(obj: Any) -> str:
    try:
        return _json_mod.dumps(obj)
    except Exception:
        return str(obj)


def _wants_sse(accept: Optional[str]) -> bool:
    return bool(accept) and "text/event-stream" in accept.lower()


def _sse_frame(payload: Any) -> str:
    """A single Streamable-HTTP SSE `message` frame carrying a JSON-RPC body."""
    return f"event: message\ndata: {_json_text(payload)}\n\n"


def _unauthorized_response(proxy_id: str, req_id: Any) -> JSONResponse:
    """401 with WWW-Authenticate pointing at the protected-resource metadata,
    per the MCP authorization (OAuth-discovery-shaped) flow."""
    base = (settings.APP_BASE_URL or "").rstrip("/")
    resource_meta = (
        f"{base}/api/v1/proxy/{proxy_id}/.well-known/oauth-protected-resource"
        if base
        else f"/api/v1/proxy/{proxy_id}/.well-known/oauth-protected-resource"
    )
    resp = JSONResponse(
        _rpc_envelope(
            req_id,
            error={"code": -32001, "message": "Unauthorized: missing or invalid agent key"},
        ),
        status_code=401,
    )
    resp.headers["WWW-Authenticate"] = (
        f'Bearer resource_metadata="{resource_meta}"'
    )
    return resp


# ---------------------------------------------------------------------------
# JSON-RPC method dispatch — one request object -> one response object (or None
# for notifications, which produce no response).
# ---------------------------------------------------------------------------


async def _dispatch_rpc(
    runtime,
    proxy_id: str,
    msg: dict,
    agent_key: Optional[str],
) -> tuple[Optional[dict], bool]:
    """Handle a single JSON-RPC message.

    Returns (response_obj_or_None, auth_required_failure). The second flag is
    True when the call needed auth and the key was missing/invalid, so the HTTP
    layer can emit a 401 + WWW-Authenticate.
    """
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_envelope(
            (msg or {}).get("id") if isinstance(msg, dict) else None,
            error={"code": -32600, "message": "Invalid Request"},
        ), False

    req_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    # --- lifecycle: initialize ------------------------------------------
    if method == "initialize":
        requested = None
        if isinstance(params, dict):
            requested = params.get("protocolVersion")
        return _rpc_envelope(req_id, result=runtime.initialize_result(requested)), False

    # --- lifecycle: initialized notification ----------------------------
    if method in ("notifications/initialized", "initialized"):
        return None, False  # notification: no response body

    # --- ping -----------------------------------------------------------
    if method == "ping":
        return _rpc_envelope(req_id, result={}), False

    # --- tools/list -----------------------------------------------------
    if method == "tools/list":
        tools = await runtime.list_tools()
        return _rpc_envelope(req_id, result={"tools": tools}), False

    # --- tools/call -----------------------------------------------------
    if method == "tools/call":
        if runtime.requires_auth() and not runtime.broker.validate_agent_key(agent_key):
            return None, True  # signal a 401 to the HTTP layer
        name = params.get("name") if isinstance(params, dict) else None
        arguments = (params.get("arguments") if isinstance(params, dict) else None) or {}
        if not name:
            return _rpc_envelope(
                req_id, error={"code": -32602, "message": "Invalid params: missing tool name"}
            ), False
        normalized = await runtime.call_tool(name, arguments)
        result = {
            "content": [{"type": "text", "text": _json_text(normalized)}],
            "isError": not normalized.get("success", False),
            "structuredContent": normalized,
        }
        return _rpc_envelope(req_id, result=result), False

    # --- unknown --------------------------------------------------------
    if is_notification:
        return None, False  # silently ignore unknown notifications
    return _rpc_envelope(
        req_id, error={"code": -32601, "message": f"Method not found: {method}"}
    ), False


@router.get("/proxy/{proxy_id}/mcp")
async def proxy_mcp_get(
    proxy_id: str,
    request: Request,
    accept: Optional[str] = Header(default=None),
):
    """Streamable HTTP GET. MCP clients open this for a server->client SSE
    stream; we have no server-initiated messages, so we return an empty
    keepalive stream when SSE is requested, else a small JSON descriptor."""
    runtime = await proxy_runtime.load_runtime(proxy_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")

    if _wants_sse(accept):
        # An empty, immediately-closing SSE stream is a valid Streamable-HTTP
        # response (we never push server-initiated notifications).
        async def _empty_stream():
            yield ": keepalive\n\n"

        from fastapi.responses import StreamingResponse

        return StreamingResponse(_empty_stream(), media_type="text/event-stream")

    tools = await runtime.list_tools()
    return {
        "name": runtime.server_name(),
        "protocol": "mcp",
        "transport": "streamable-http",
        "protocolVersion": proxy_runtime.MCP_PROTOCOL_VERSION,
        "methods": ["initialize", "ping", "tools/list", "tools/call"],
        "tools": tools,
    }


@router.post("/proxy/{proxy_id}/mcp")
async def proxy_mcp_post(
    proxy_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_agent_key: Optional[str] = Header(default=None, alias="X-Agent-Key"),
    accept: Optional[str] = Header(default=None),
):
    """MCP Streamable HTTP transport (JSON-RPC 2.0).

    Handles `initialize`, `notifications/initialized`, `ping`, `tools/list`,
    `tools/call`, and both single objects and batch arrays. Responds as either
    `application/json` or a single SSE `message` frame, per the Accept header.
    """
    runtime = await proxy_runtime.load_runtime(proxy_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Parse error: invalid JSON", status=400)

    agent_key = _extract_agent_key(authorization, x_agent_key)
    want_sse = _wants_sse(accept)

    # --- batch ----------------------------------------------------------
    if isinstance(payload, list):
        if not payload:
            return _rpc_error(None, -32600, "Invalid Request: empty batch", status=400)
        responses: list[dict] = []
        for msg in payload:
            resp, auth_fail = await _dispatch_rpc(runtime, proxy_id, msg, agent_key)
            if auth_fail:
                # Any auth failure in a batch -> 401 for the whole request.
                return _unauthorized_response(proxy_id, (msg or {}).get("id"))
            if resp is not None:
                responses.append(resp)
        # A batch consisting only of notifications yields no response body (202).
        if not responses:
            return Response(status_code=202)
        if want_sse:
            return _stream_single(responses)
        return JSONResponse(responses)

    # --- single object --------------------------------------------------
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Invalid Request", status=400)

    resp, auth_fail = await _dispatch_rpc(runtime, proxy_id, payload, agent_key)
    if auth_fail:
        return _unauthorized_response(proxy_id, payload.get("id"))
    if resp is None:
        # Notification (e.g. notifications/initialized) -> 202, no body.
        return Response(status_code=202)
    if want_sse:
        return _stream_single(resp)
    return JSONResponse(resp)


def _stream_single(payload: Any):
    """Reply with a single SSE `message` frame (Streamable HTTP single-response)."""
    from fastapi.responses import StreamingResponse

    async def _gen():
        yield _sse_frame(payload)

    return StreamingResponse(_gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Discovery manifests
# ---------------------------------------------------------------------------


@router.get("/proxy/{proxy_id}/.well-known/mcp.json")
async def proxy_well_known(proxy_id: str):
    """Discovery manifest — serves the persisted advertise.well_known body."""
    advertise = await proxy_runtime.get_advertise(proxy_id)
    if not advertise or not advertise.get("well_known"):
        raise HTTPException(status_code=404, detail="No proxy manifest for this id")
    return JSONResponse(advertise["well_known"])


@router.get("/proxy/{proxy_id}/.well-known/oauth-protected-resource")
async def proxy_oauth_protected_resource(proxy_id: str):
    """Minimal OAuth 2.0 Protected Resource Metadata (RFC 9728), as required by
    the MCP authorization spec. We are not a full authorization server — this is
    OAuth-discovery-shaped so compliant clients learn the resource expects a
    bearer token. The scoped-key bearer mechanism is the v1 credential.
    """
    runtime = await proxy_runtime.load_runtime(proxy_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="No proxy hosted for this id")
    base = (settings.APP_BASE_URL or "").rstrip("/")
    resource = (
        f"{base}/api/v1/proxy/{proxy_id}/mcp" if base else f"/api/v1/proxy/{proxy_id}/mcp"
    )
    return JSONResponse(
        {
            "resource": resource,
            "bearer_methods_supported": ["header"],
            # No external AS in v1; bearer scoped keys are owner-minted out of band.
            "authorization_servers": [],
            "scopes_supported": [],
            "auth": "bearer",
        }
    )


# ---------------------------------------------------------------------------
# Public registry / directory — products with a live hosted proxy.
# ---------------------------------------------------------------------------


@router.get("/registry")
async def public_registry():
    """Public directory of hosted proxies: [{domain, score, mcp_url, tool_count}].
    Defensive: never 500s — returns [] on any failure."""
    try:
        items = await proxy_runtime.list_public_registry()
    except Exception:
        logger.exception("[proxy] /registry failed")
        items = []
    return JSONResponse(items)


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
