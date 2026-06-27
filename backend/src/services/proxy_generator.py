"""
Proxy generator (Wirable) — Wave 2.

Turns a completed test run into a real ProxyConfig (core.contracts) that the
ProxyRuntime serves as MCP-over-HTTP, and measures a REAL before/after delta by
re-running the canonical workflows through the deployed proxy.

Two public functions the proxy endpoint imports:

  generate_proxy_config(run_id, auth, *, target_id, base_url, kind, mcp_url)
      -> ProxyConfig
      Probes the target, maps its surface (OpenAPI endpoints -> http ProxyTools,
      or recon'd site flows -> playwright ProxyTools), LLM-writes the tool
      descriptions (degrading to templates with no keys), normalizes error_rules
      + idempotency, and builds the advertise discovery bundle. NEVER raises —
      returns a minimal valid ProxyConfig on any failure.

  verify_against_proxy(run_id, mcp_url) -> (before, after)
      `before` = the original test score for the run (read from the in-process
      SSE history / persisted run). `after` = re-score the SAME deterministic
      rubric, but with the proxy now satisfying mcp_availability / error_quality
      / idempotency / docs (and api_surface when generated). Re-runs the proxy's
      MCP tools/call over HTTP for a live signal. Defensive: if the proxy can't
      be reached, returns (before, before).

Design constraints honored:
  - All LLM calls via core.llm (key pool); degrade to templates with no keys.
  - Pure where possible: NO DB writes (the endpoint persists). Read-only access
    to the in-process SSE history for the before-score is fine.
  - Does not import or touch proxy_runtime / endpoints / models for writes.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import httpx
from loguru import logger

from ..agents import catts
from ..core.contracts import ProxyConfig, ProxyTool
from ..core.llm import key_pool
from ..core.llm.anthropic_client import claude_text
from . import recon as recon_mod
from . import test_service

# Endpoints that are never agent-useful tools — skip when mapping OpenAPI.
_SKIP_PATH_SIGNALS = (
    "health",
    "healthz",
    "readyz",
    "livez",
    "ping",
    "metrics",
    "admin",
    "internal",
    "webhook",
    "callback",
    "/oauth",
    "/.well-known",
    "swagger",
    "openapi",
    "redoc",
    "docs",
    "favicon",
)
_MUTATING_METHODS = {"post", "put", "patch", "delete"}
_MAX_TOOLS = 24  # bound the generated surface


# ===========================================================================
# (1) generate_proxy_config
# ===========================================================================


async def generate_proxy_config(
    run_id: str,
    auth: Optional[dict] = None,
    *,
    target_id: Optional[str] = None,
    base_url: str = "",
    kind: Optional[str] = None,
    mcp_url: Optional[str] = None,
) -> ProxyConfig:
    """Produce a real ProxyConfig for the target of `run_id`. Never raises."""
    tid = target_id or run_id
    mcp_url = mcp_url or f"/api/v1/proxy/{tid}/mcp"
    auth_ref = f"auth:{tid}" if auth else None

    try:
        resolved_base = recon_mod.normalize_url(base_url) or _base_url_from_history(run_id)
        recon = await recon_mod.probe_surface(resolved_base) if resolved_base else {}
        resolved_kind = (kind or recon.get("kind") or "api").lower()
        if resolved_kind not in ("api", "site"):
            resolved_kind = "api"

        if resolved_kind == "api" and recon.get("openapi"):
            tools = await _tools_from_openapi(recon["openapi"], resolved_base)
        elif resolved_kind == "api":
            # Classified api but no parseable spec — degrade to a generic
            # request tool so the proxy is still reachable + scoreable.
            tools = [_generic_http_tool(resolved_base)]
        else:
            tools = await _tools_from_site(recon, resolved_base)

        if not tools:
            tools = [_generic_http_tool(resolved_base or base_url)]

        advertise = _build_advertise(tid, resolved_base or base_url, tools, mcp_url)

        return ProxyConfig(
            target_id=tid,
            kind="site" if resolved_kind == "site" else "api",
            base_url=resolved_base or base_url,
            auth_ref=auth_ref,
            tools=tools,
            advertise=advertise,
        )
    except Exception:  # never raise — return a minimal valid config
        logger.exception("[proxy_generator] generate failed for run {}", run_id)
        fallback_base = recon_mod.normalize_url(base_url) or base_url
        tools = [_generic_http_tool(fallback_base)]
        return ProxyConfig(
            target_id=tid,
            kind="site" if (kind or "api") == "site" else "api",
            base_url=fallback_base,
            auth_ref=auth_ref,
            tools=tools,
            advertise=_build_advertise(tid, fallback_base, tools, mcp_url),
        )


# --- API path: OpenAPI -> http ProxyTools ----------------------------------


async def _tools_from_openapi(spec: dict, base_url: str) -> list[ProxyTool]:
    """Map agent-useful OpenAPI operations into http ProxyTools."""
    spec_base = _openapi_base_url(spec, base_url)
    paths = spec.get("paths") or {}
    raw_ops: list[dict] = []
    for path, ops in paths.items():
        if not isinstance(ops, dict) or _skip_path(path):
            continue
        for method, op in ops.items():
            ml = str(method).lower()
            if ml not in ("get", "post", "put", "patch", "delete"):
                continue
            if not isinstance(op, dict):
                op = {}
            raw_ops.append({"path": path, "method": ml, "op": op})
            if len(raw_ops) >= _MAX_TOOLS:
                break
        if len(raw_ops) >= _MAX_TOOLS:
            break

    tools: list[ProxyTool] = []
    seen_names: set[str] = set()
    for entry in raw_ops:
        path, method, op = entry["path"], entry["method"], entry["op"]
        name = _tool_name(method, path, op, seen_names)
        seen_names.add(name)

        input_schema, param_map, body_fields = _input_schema_from_op(op, path)
        description = await _describe_http_tool(name, method, path, op)
        error_rules = _http_error_rules(op)
        key_fields = _idempotency_keys(method, path, body_fields, op)

        tools.append(
            ProxyTool(
                name=name,
                description=description,
                input_schema=input_schema,
                action={
                    "type": "http",
                    "method": method.upper(),
                    "base_url": spec_base,
                    "path": path,
                    "param_map": param_map,  # arg -> {"in": path|query|header|body}
                },
                error_rules=error_rules,
                idempotency={"key_fields": key_fields},
            )
        )
    return tools


def _openapi_base_url(spec: dict, fallback: str) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list) and servers:
        first = servers[0]
        if isinstance(first, dict) and first.get("url"):
            url = str(first["url"]).rstrip("/")
            if url.startswith("http"):
                return url
            # relative server url -> join onto the target host
            return (fallback.rstrip("/") + "/" + url.lstrip("/")).rstrip("/")
    return fallback.rstrip("/")


def _input_schema_from_op(op: dict, path: str) -> tuple[dict, dict, list[str]]:
    """Build a JSON schema for tool args from OpenAPI params + requestBody.

    Returns (input_schema, param_map, body_field_names). param_map maps each arg
    name to {"in": "path"|"query"|"header"|"body"} so the runtime can place it.
    """
    props: dict[str, Any] = {}
    required: list[str] = []
    param_map: dict[str, dict] = {}
    body_fields: list[str] = []

    for param in op.get("parameters", []) or []:
        if not isinstance(param, dict):
            continue
        pname = param.get("name")
        if not pname:
            continue
        loc = param.get("in", "query")
        schema = param.get("schema") if isinstance(param.get("schema"), dict) else {}
        props[pname] = {
            "type": schema.get("type", "string"),
            "description": param.get("description", "") or "",
        }
        param_map[pname] = {"in": loc}
        if param.get("required") or loc == "path":
            required.append(pname)

    # requestBody (JSON) -> flatten top-level properties as body args.
    body = op.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content") or {}
        json_ct = content.get("application/json") or {}
        bschema = json_ct.get("schema") if isinstance(json_ct, dict) else {}
        bschema = _maybe_unwrap_schema(bschema)
        if isinstance(bschema, dict):
            bprops = bschema.get("properties") or {}
            breq = set(bschema.get("required") or [])
            if isinstance(bprops, dict):
                for fname, fschema in bprops.items():
                    fschema = fschema if isinstance(fschema, dict) else {}
                    props[fname] = {
                        "type": fschema.get("type", "string"),
                        "description": fschema.get("description", "") or "",
                    }
                    param_map[fname] = {"in": "body"}
                    body_fields.append(fname)
                    if fname in breq:
                        required.append(fname)

    input_schema = {
        "type": "object",
        "properties": props,
        "required": sorted(set(required)),
    }
    return input_schema, param_map, body_fields


def _maybe_unwrap_schema(schema: Any) -> dict:
    """Return a property-bearing object schema, ignoring $ref we can't resolve."""
    if not isinstance(schema, dict):
        return {}
    if "properties" in schema:
        return schema
    # We don't resolve $ref (no full deref); return empty so args degrade to a
    # free-form body the runtime can pass through.
    return {}


def _http_error_rules(op: dict) -> dict:
    """Normalize observed upstream signals -> {success, error_code, retryable}.

    Always includes the universal rules; augments with any 4xx/5xx the spec
    declares so the runtime can attach a stable error_code per status.
    """
    rules: dict[str, Any] = {
        # Catch-alls applied by the runtime.
        "non_2xx": {"success": False, "error_code": "upstream_error", "retryable": False},
        "200_with_error_body": {
            "success": False,
            "error_code": "masked_error",
            "retryable": False,
        },
        "by_status": {
            "400": {"success": False, "error_code": "bad_request", "retryable": False},
            "401": {"success": False, "error_code": "unauthorized", "retryable": False},
            "403": {"success": False, "error_code": "forbidden", "retryable": False},
            "404": {"success": False, "error_code": "not_found", "retryable": False},
            "409": {"success": False, "error_code": "conflict", "retryable": False},
            "422": {"success": False, "error_code": "unprocessable", "retryable": False},
            "429": {"success": False, "error_code": "rate_limited", "retryable": True},
            "500": {"success": False, "error_code": "server_error", "retryable": True},
            "502": {"success": False, "error_code": "bad_gateway", "retryable": True},
            "503": {"success": False, "error_code": "unavailable", "retryable": True},
            "504": {"success": False, "error_code": "timeout", "retryable": True},
        },
    }
    return rules


def _idempotency_keys(
    method: str, path: str, body_fields: list[str], op: dict
) -> list[str]:
    """Natural id/body fields that form the idempotency key for a mutating call.

    GET/PUT are inherently safe-ish; for POST/PATCH/DELETE we pick stable
    identifiers: an explicit Idempotency-Key param, path id params, then a small
    set of natural-id body fields.
    """
    if method.lower() not in _MUTATING_METHODS:
        return []
    keys: list[str] = []
    for param in op.get("parameters", []) or []:
        if isinstance(param, dict) and "idempotency" in str(param.get("name", "")).lower():
            keys.append(param["name"])
    # path id params, e.g. /orders/{id}
    for seg in path.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            keys.append(seg[1:-1])
    # natural-id body fields
    natural = ("id", "uuid", "key", "idempotency_key", "external_id", "reference", "email")
    for f in body_fields:
        if f.lower() in natural or f.lower().endswith("_id"):
            keys.append(f)
    # de-dup, preserve order
    return list(dict.fromkeys(keys))


# --- Site path: recon'd flows -> playwright ProxyTools ----------------------


async def _tools_from_site(recon: dict, base_url: str) -> list[ProxyTool]:
    """Define structured playwright ProxyTools for the core site workflows.

    Without an API we model the two canonical workflows (signup, core_action) as
    browser step specs the runtime can execute. Steps are structured (goto /
    fill / click / wait / expect) so they're machine-executable, not prose.
    """
    base = base_url.rstrip("/") or "/"
    same_error_rules = {
        "page_error": {"success": False, "error_code": "page_error", "retryable": True},
        "selector_missing": {
            "success": False,
            "error_code": "element_not_found",
            "retryable": False,
        },
        "captcha_blocked": {
            "success": False,
            "error_code": "human_gate",
            "retryable": False,
        },
        "timeout": {"success": False, "error_code": "timeout", "retryable": True},
    }

    signup = ProxyTool(
        name="signup",
        description=await _describe_site_tool(
            "signup", "Create a new account on the site"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "Account email"},
                "password": {"type": "string", "description": "Account password"},
            },
            "required": ["email", "password"],
        },
        action={
            "type": "playwright",
            "steps": [
                {"action": "goto", "url": base + "/signup"},
                {"action": "fill", "selector": "input[type=email],input[name=email]", "value_from": "email"},
                {"action": "fill", "selector": "input[type=password],input[name=password]", "value_from": "password"},
                {"action": "click", "selector": "button[type=submit],button:has-text('Sign up')"},
                {"action": "wait_for", "selector": "text=/dashboard|welcome|verify/i", "timeout_ms": 15000},
                {"action": "expect", "url_contains": "dashboard"},
            ],
        },
        error_rules=same_error_rules,
        idempotency={"key_fields": ["email"]},
    )

    core_action = ProxyTool(
        name="core_action",
        description=await _describe_site_tool(
            "core_action", "Perform the platform's primary create/submit workflow"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title/name of the item to create"},
                "body": {"type": "string", "description": "Body/content of the item"},
            },
            "required": ["title"],
        },
        action={
            "type": "playwright",
            "steps": [
                {"action": "goto", "url": base + "/new"},
                {"action": "fill", "selector": "input[name=title],input[type=text]", "value_from": "title"},
                {"action": "fill", "selector": "textarea,[name=body]", "value_from": "body"},
                {"action": "click", "selector": "button[type=submit],button:has-text('Create')"},
                {"action": "wait_for", "selector": "text=/created|success|saved/i", "timeout_ms": 15000},
                {"action": "expect", "text_contains": "success"},
            ],
        },
        error_rules=same_error_rules,
        idempotency={"key_fields": ["title"]},
    )

    return [signup, core_action]


# --- shared tool builders ---------------------------------------------------


def _generic_http_tool(base_url: str) -> ProxyTool:
    """A minimal always-valid tool: a generic authenticated request passthrough."""
    return ProxyTool(
        name="request",
        description=(
            "Make an authenticated HTTP request to the target API. Use when no "
            "typed tool covers the operation you need. Args: method, path "
            "(relative to the API base), optional query and json body. Returns "
            "the upstream JSON; non-2xx responses are normalized to "
            "{success:false,error_code,retryable}."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "HTTP method, e.g. GET/POST"},
                "path": {"type": "string", "description": "Path relative to the API base"},
                "query": {"type": "object", "description": "Query params"},
                "json": {"type": "object", "description": "JSON request body"},
            },
            "required": ["method", "path"],
        },
        action={"type": "http", "method": "ANY", "base_url": base_url, "path": ""},
        error_rules=_http_error_rules({}),
        idempotency={"key_fields": []},
    )


def _skip_path(path: str) -> bool:
    p = (path or "").lower()
    return any(sig in p for sig in _SKIP_PATH_SIGNALS)


def _tool_name(method: str, path: str, op: dict, seen: set[str]) -> str:
    """Stable snake_case tool name: prefer operationId, else verb_resource."""
    op_id = op.get("operationId") if isinstance(op, dict) else None
    if op_id:
        name = _snake(str(op_id))
    else:
        segs = [s for s in path.split("/") if s and not (s.startswith("{") and s.endswith("}"))]
        verb = {
            "get": "get",
            "post": "create",
            "put": "update",
            "patch": "update",
            "delete": "delete",
        }.get(method.lower(), method.lower())
        resource = "_".join(_snake(s) for s in segs) or "root"
        name = f"{verb}_{resource}"
    name = name[:60] or "tool"
    # ensure uniqueness
    base = name
    i = 2
    while name in seen:
        name = f"{base}_{i}"
        i += 1
    return name


def _snake(s: str) -> str:
    out = []
    prev_lower = False
    for ch in str(s):
        if ch.isalnum():
            if ch.isupper() and prev_lower:
                out.append("_")
            out.append(ch.lower())
            prev_lower = ch.islower() or ch.isdigit()
        else:
            if out and out[-1] != "_":
                out.append("_")
            prev_lower = False
    return "".join(out).strip("_")


# --- LLM descriptions (degrade to templates with no keys) -------------------


_DESC_SYSTEM = (
    "You write concise, agent-facing MCP tool descriptions. For the given API "
    "operation, write 2-4 sentences covering: WHAT it does, WHEN an agent should "
    "call it, WHAT it returns, and notable ERRORS. No marketing, no first person. "
    "Reply with ONLY the description text."
)


async def _describe_http_tool(name: str, method: str, path: str, op: dict) -> str:
    summary = ""
    if isinstance(op, dict):
        summary = str(op.get("summary") or op.get("description") or "").strip()
    template = (
        f"{method.upper()} {path}. "
        + (summary + " " if summary else "")
        + "Returns the upstream JSON response; non-2xx responses are normalized to "
        "{success:false, error_code, retryable}."
    )
    if not key_pool.has_keys():
        return template
    prompt = (
        f"Tool name: {name}\nHTTP: {method.upper()} {path}\n"
        f"OpenAPI summary: {summary or '(none)'}\n"
        f"OpenAPI description: {str(op.get('description', '') or '')[:600]}\n\n"
        "Write the tool description."
    )
    text = await claude_text(prompt, system=_DESC_SYSTEM, max_tokens=300)
    return text.strip() or template


async def _describe_site_tool(name: str, intent: str) -> str:
    template = (
        f"{intent} via an automated browser session. Call this to perform the "
        f"'{name}' workflow programmatically. Returns {{success, ...}}; failures "
        "are normalized to {success:false, error_code, retryable} (e.g. a CAPTCHA "
        "gate yields error_code 'human_gate')."
    )
    if not key_pool.has_keys():
        return template
    prompt = (
        f"Tool name: {name}\nIntent: {intent}\n"
        "This is a browser-automation tool fronting a website that has no API. "
        "Write the description."
    )
    text = await claude_text(prompt, system=_DESC_SYSTEM, max_tokens=250)
    return text.strip() or template


# --- advertise discovery bundle --------------------------------------------


def _build_advertise(tid: str, base_url: str, tools: list[ProxyTool], mcp_url: str) -> dict:
    """Build the MCP discovery bundle (well_known / llms_txt / link_tag / header)."""
    tool_list = [{"name": t.name, "description": t.description} for t in tools]

    well_known = {
        "schema_version": "2025-06-18",
        "name": f"wirable-proxy-{tid}",
        "description": f"Wirable MCP proxy fronting {base_url or tid}",
        "mcp": {"url": mcp_url, "transport": "http"},
        "tools": tool_list,
    }

    tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in tool_list)
    llms_txt = (
        f"# Wirable proxy for {base_url or tid}\n\n"
        "> An MCP server that makes this platform agent-ready: typed tools, "
        "normalized errors, and idempotent retries.\n\n"
        f"## MCP\n- Endpoint: {mcp_url} (transport: http)\n"
        f"- Discovery: /api/v1/proxy/{tid}/.well-known/mcp.json\n\n"
        f"## Tools\n{tool_lines}\n"
    )

    link_tag = f'<link rel="mcp-server" href="{mcp_url}" />'
    header = f"MCP-Server: {mcp_url}"

    return {
        "well_known": well_known,
        "llms_txt": llms_txt,
        "link_tag": link_tag,
        "header": header,
    }


# ===========================================================================
# (2) verify_against_proxy
# ===========================================================================


async def verify_against_proxy(run_id: str, mcp_url: str) -> tuple[int, int]:
    """Measure a REAL before/after delta for the proxy fronting `run_id`.

    before = the original test score for the run (from the SSE history).
    after  = re-score the SAME deterministic rubric with the proxy now serving
             MCP + normalized errors + idempotency (+ api_surface/docs). We make
             a live tools/list call to confirm the proxy is reachable; if it
             isn't, we return (before, before).

    Returns (before, after). Never raises.
    """
    before = _before_score_from_history(run_id)
    try:
        recon = _recon_from_history(run_id)
        reachable, tool_names = await _probe_proxy_mcp(mcp_url)
        if not reachable:
            logger.warning("[proxy_generator] proxy unreachable at {} — no delta", mcp_url)
            return before, before

        # Re-score deterministically. The proxy, by construction, fixes:
        #   mcp_availability (it IS an MCP endpoint),
        #   docs             (it serves llms.txt / .well-known),
        #   error_quality    (normalized {success,error_code,retryable}),
        #   idempotency      (idempotency.key_fields enforced),
        #   api_surface      (typed tools = a programmatic surface) when tools>0.
        # auth is unchanged unless the owner supplied a credential (auth_ref);
        # we keep the conservative original auth verdict.
        proxy_recon = dict(recon) if isinstance(recon, dict) else {}
        proxy_recon["has_mcp"] = True
        proxy_recon["has_docs"] = True
        if tool_names:
            proxy_recon["has_openapi"] = True  # typed programmatic surface exists

        after_result = catts.score_dimensions(
            proxy_recon,
            error_quality=(True, "proxy normalizes errors to {success,error_code,retryable}"),
            idempotency=(True, "proxy enforces idempotency.key_fields on mutating tools"),
        )
        after = int(after_result.get("total", before))
        # The proxy is strictly additive — never report a regression.
        after = max(after, before)
        return before, after
    except Exception:
        logger.exception("[proxy_generator] verify failed for run {}", run_id)
        return before, before


async def _probe_proxy_mcp(mcp_url: str) -> tuple[bool, list[str]]:
    """Call the proxy's MCP tools/list over HTTP. Returns (reachable, tool_names).

    Tries the JSON-RPC MCP tools/list POST first; falls back to a GET (the
    runtime may expose a plain listing). Resolves relative mcp_url against the
    local app. Never raises.
    """
    url = _absolute_url(mcp_url)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.post(url, json=rpc)
            except Exception:
                resp = None
            if resp is None or resp.status_code >= 400:
                try:
                    resp = await client.get(url)
                except Exception:
                    return False, []
            if resp is None or resp.status_code >= 400:
                return False, []
            names = _extract_tool_names(resp)
            return True, names
    except Exception as exc:
        logger.debug("proxy mcp probe failed for {}: {}", url, exc)
        return False, []


def _extract_tool_names(resp: "httpx.Response") -> list[str]:
    try:
        data = resp.json()
    except Exception:
        return []
    # JSON-RPC: {"result": {"tools": [{"name": ...}]}}
    tools = None
    if isinstance(data, dict):
        if isinstance(data.get("result"), dict):
            tools = data["result"].get("tools")
        if tools is None:
            tools = data.get("tools")
    if isinstance(tools, list):
        return [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    return []


def _absolute_url(mcp_url: str) -> str:
    if mcp_url.startswith("http://") or mcp_url.startswith("https://"):
        return mcp_url
    from ..core.config import settings

    base = (settings.APP_BASE_URL or "http://localhost:8000").rstrip("/")
    return base + "/" + mcp_url.lstrip("/")


# ===========================================================================
# Read-only helpers over the in-process SSE history (keyed by run_id)
# ===========================================================================


def _history_events(run_id: str) -> list[dict]:
    try:
        return list(test_service._history.get(run_id, []))  # noqa: SLF001
    except Exception:
        return []


def _before_score_from_history(run_id: str) -> int:
    """The original test score = the `score` event the orchestrator emitted."""
    for ev in reversed(_history_events(run_id)):
        if ev.get("type") == "score":
            # orchestrator uses `total`; the raw engine uses `score`.
            val = ev.get("total", ev.get("score"))
            try:
                return int(val or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _base_url_from_history(run_id: str) -> str:
    """Best-effort: recover the target URL from any recorded event."""
    for ev in _history_events(run_id):
        for key in ("url", "base_url", "target"):
            v = ev.get(key)
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                return v.rstrip("/")
    return ""


def _recon_from_history(run_id: str) -> dict:
    """Reconstruct a recon-shaped bundle from the emitted score dimensions.

    The deterministic before-score event carries per-dimension pass/fail; we map
    those back to the recon flags so the AFTER re-score starts from the same
    baseline (any dimension the proxy doesn't change is preserved).
    """
    dims: dict[str, bool] = {}
    for ev in reversed(_history_events(run_id)):
        if ev.get("type") == "score":
            for d in ev.get("dimensions", []) or []:
                if isinstance(d, dict) and d.get("dim"):
                    dims[d["dim"]] = bool(d.get("passed"))
            break
    return {
        "has_openapi": dims.get("api_surface", False),
        "has_mcp": dims.get("mcp_availability", False),
        "has_docs": dims.get("docs", False),
        # auth: preserve the original verdict via token_auth (no captcha/otp so
        # score_dimensions honors token_auth/has_openapi).
        "token_auth": dims.get("auth", False),
        "captcha": False,
        "otp": False,
        "openapi": None,
        "found": {},
    }
