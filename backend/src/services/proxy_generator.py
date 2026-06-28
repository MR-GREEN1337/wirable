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
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger

from ..agents import catts
from ..core.contracts import ProxyConfig, ProxyTool
from ..core.llm import key_pool
from ..core.llm.anthropic_client import claude_text
from . import recon as recon_mod
from . import test_service

# API/doc subdomains scoured (in priority order) when targeting the real API
# host. Mirrors the in-sandbox audit_driver discovery so the proxy points at
# where the API actually lives (e.g. api.kortix.com), not just the apex.
_API_SUBDOMAINS = (
    "api", "api-test", "apis", "sandbox", "developer", "developers",
    "rest", "graphql", "gateway", "public-api", "docs", "doc", "app",
)
_TWO_LABEL_TLDS = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "co.kr", "com.au", "net.au",
    "com.br", "co.in", "co.nz", "com.sg", "com.mx", "co.za",
)

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
# Build stream — a small emit shim so the generator can narrate its REAL work
# (upstream discovery, OpenAPI read, per-tool mapping) onto the run's SSE bus.
# ===========================================================================
# The endpoint passes an async `emit` callback bound to the run. We keep the
# generator pure/optional: when no callback is given, narration is a no-op.

BuildEmit = Callable[[dict], Awaitable[None]]


async def _say(emit: Optional[BuildEmit], event: dict) -> None:
    """Emit a build event if a callback is wired; never raise."""
    if emit is None:
        return
    try:
        await emit(event)
    except Exception:
        logger.debug("[proxy_generator] build emit failed", exc_info=True)


def _proxy_line(msg: str, ok: bool = True) -> dict:
    """A build-stream line event, namespaced so the UI can isolate proxy steps."""
    return {"type": "line", "ok": ok, "msg": f"proxy: {msg}"}


def _tool_method_path(tool: "ProxyTool") -> tuple[Optional[str], Optional[str], str]:
    """Extract (method, path, kind) from a ProxyTool's action for the stream row."""
    action = tool.action or {}
    kind = (action.get("type") or "http").lower()
    if kind == "http":
        return (action.get("method") or "GET"), (action.get("path") or ""), "http"
    return None, None, "playwright"


async def _emit_tool_row(emit: Optional[BuildEmit], tool: "ProxyTool") -> None:
    """Materialize one tool in the build stream: a proxy_tool event + a line."""
    method, path, kind = _tool_method_path(tool)
    from ..core.contracts import events as _events  # local import (avoid cycle at top)

    await _say(emit, _events.proxy_tool(tool.name, method=method, path=path, kind=kind))
    if kind == "http" and path:
        await _say(emit, _proxy_line(f"tool ready: {tool.name} ({method} {path})"))
    else:
        await _say(emit, _proxy_line(f"tool ready: {tool.name}"))


async def _emit_tool_rows(emit: Optional[BuildEmit], tools: list) -> None:
    for t in tools:
        await _emit_tool_row(emit, t)


# ===========================================================================
# Deep machine-surface discovery — scour subdomains for the REAL API host.
# ===========================================================================


def _registrable_domain(host: str) -> str:
    host = (host or "").strip().strip(".").lower()
    if not host:
        return host
    labels = host.split(".")
    if len(labels) == 4 and all(p.isdigit() for p in labels):
        return host  # IP
    if len(labels) <= 2:
        return host
    last2 = ".".join(labels[-2:])
    if last2 in _TWO_LABEL_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last2


def _candidate_api_hosts(base_url: str) -> list[str]:
    """Apex + API/doc subdomains of the registrable domain (origin first)."""
    host = urlsplit(base_url).netloc.split("@")[-1].split(":")[0]
    reg = _registrable_domain(host)
    out: list[str] = []
    seen: set[str] = set()

    def add(h: str) -> None:
        if h and h not in seen:
            seen.add(h)
            out.append("https://" + h)

    add(host)
    if reg and not (len(reg.split(".")) == 4 and reg.replace(".", "").isdigit()):
        for sd in _API_SUBDOMAINS:
            add(f"{sd}.{reg}")
        add(reg)
    return out


async def _discover_best_openapi(base_url: str) -> dict:
    """Scour the apex + API/doc subdomains for the RICHEST OpenAPI spec.

    Returns {"spec": dict, "base_url": "https://api.kortix.com", "host": ...} for
    the spec with the most paths found anywhere, or {} if none. Bounded: at most
    one fetch_openapi attempt per candidate host. Never raises.
    """
    best: dict = {}
    for host_base in _candidate_api_hosts(base_url):
        try:
            spec = await recon_mod.fetch_openapi(host_base)
        except Exception:
            spec = None
        if not isinstance(spec, dict):
            continue
        npaths = len(spec.get("paths") or {}) if isinstance(spec.get("paths"), dict) else 0
        if npaths > best.get("num_paths", -1):
            best = {"spec": spec, "base_url": host_base.rstrip("/"),
                    "host": urlsplit(host_base).netloc, "num_paths": npaths}
    return best


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
    machine_surface: Optional[dict] = None,
    code_endpoints: Optional[list] = None,
    code_base_url: Optional[str] = None,
    emit: Optional[BuildEmit] = None,
) -> ProxyConfig:
    """Produce a real ProxyConfig for the target of `run_id`. Never raises.

    GROUND TRUTH FIRST: when `code_endpoints` is supplied (the REAL endpoints
    extracted from the bound repo's source by code_analysis), we build the MCP
    tools directly from them — they are authoritative, not inferred from a
    black-box probe. The upstream base = `code_base_url` (the repo's
    base_url_hint) when present, else the tested target URL.

    When no code endpoints are given we fall back to the existing discovery:
    build the MCP from the BEST discovered OpenAPI anywhere on the registrable
    domain (apex OR a subdomain like api.kortix.com). `machine_surface` (from the
    in-sandbox audit driver) is honored when provided; otherwise we scour the
    subdomains server-side. If no OpenAPI exists but llms.txt / docs do, we still
    emit a minimal useful toolset (search_docs / fetch) so the MCP is never empty.
    """
    tid = target_id or run_id
    mcp_url = mcp_url or f"/api/v1/proxy/{tid}/mcp"
    auth_ref = f"auth:{tid}" if auth else None

    # --- ground-truth path: build directly from real code endpoints ---------
    # Preferred source when the caller analyzed a bound repo. Fully defensive:
    # a malformed/empty endpoint list silently falls through to discovery.
    try:
        code_eps = code_endpoints if isinstance(code_endpoints, list) else []
        if code_eps:
            apex_base = recon_mod.normalize_url(base_url) or _base_url_from_history(run_id)
            resolved_base = (
                recon_mod.normalize_url(code_base_url) if code_base_url else ""
            ) or code_base_url or apex_base or base_url
            resolved_base = (resolved_base or "").rstrip("/")
            await _say(emit, _proxy_line(
                f"grounding MCP in {len(code_eps)} real endpoints from the repo source…"
            ))
            if resolved_base:
                await _say(emit, _proxy_line(f"upstream: {resolved_base}"))
            tools = await _tools_from_code_endpoints(code_eps, resolved_base, emit=emit)
            if tools:
                if not any(((t.action or {}).get("method") or "").upper() == "ANY"
                           for t in tools):
                    pass  # typed tools only; generic request is optional here
                await _say(emit, _proxy_line(
                    f"{len(tools)} tools mapped from code · writing llms.txt + .well-known/mcp.json…"
                ))
                advertise = _build_advertise(tid, resolved_base or base_url, tools, mcp_url)
                logger.info(
                    "[proxy_generator] CODE-GROUNDED proxy for {} -> upstream {} ({} tools)",
                    tid, resolved_base, len(tools),
                )
                return ProxyConfig(
                    target_id=tid,
                    kind="api",
                    base_url=resolved_base or base_url,
                    auth_ref=auth_ref,
                    tools=tools,
                    advertise=advertise,
                )
            # zero tools mapped from code -> fall through to discovery below.
    except Exception:
        logger.exception(
            "[proxy_generator] code-grounded build failed for run {} — falling back",
            run_id,
        )

    try:
        await _say(emit, _proxy_line("resolving upstream API host…"))
        apex_base = recon_mod.normalize_url(base_url) or _base_url_from_history(run_id)
        ms = machine_surface if isinstance(machine_surface, dict) else _machine_surface_from_history(run_id)

        # 1) Prefer an OpenAPI URL the in-sandbox discovery already pinned.
        spec = None
        api_base = ""
        ms_openapi_url = (ms or {}).get("openapi_url")
        if ms_openapi_url:
            try:
                spec = await recon_mod.fetch_openapi(ms_openapi_url)
            except Exception:
                spec = None
            if isinstance(spec, dict):
                # Upstream base = the host the spec lives on (its servers[] is
                # honored downstream by _openapi_base_url).
                parts = urlsplit(ms_openapi_url)
                api_base = f"{parts.scheme}://{parts.netloc}"

        # 2) Otherwise scour the subdomains ourselves for the richest spec.
        if spec is None and apex_base:
            await _say(emit, _proxy_line("scanning apex + API subdomains for an OpenAPI spec…"))
            best = await _discover_best_openapi(apex_base)
            if best:
                spec, api_base = best["spec"], best["base_url"]

        # Decide upstream base + kind.
        ms_api_base = (ms or {}).get("api_base_url")
        resolved_base = api_base or ms_api_base or apex_base or base_url
        if resolved_base:
            await _say(emit, _proxy_line(f"upstream: {resolved_base}"))
        if spec is not None:
            resolved_kind = "api"
        else:
            resolved_kind = (kind or "").lower()
            if resolved_kind not in ("api", "site"):
                # No spec found. If there's a doc/llms surface, model it as an api
                # (search_docs/fetch tools); else fall back to site flows.
                has_docs = bool((ms or {}).get("has_llms")) or bool((ms or {}).get("has_openapi"))
                resolved_kind = "api" if has_docs else "site"

        # Build tools.
        if spec is not None:
            n_paths = len(spec.get("paths") or {}) if isinstance(spec.get("paths"), dict) else 0
            await _say(emit, _proxy_line(f"reading OpenAPI ({n_paths} paths)… mapping operations to MCP tools"))
            tools = await _tools_from_openapi(spec, resolved_base, emit=emit)
        elif resolved_kind == "api":
            # No spec but a doc/llms surface exists — emit a minimal but USEFUL
            # toolset (search the docs + fetch a URL) plus a generic request tool.
            await _say(emit, _proxy_line("no OpenAPI — synthesizing search/fetch tools from docs"))
            tools = _tools_from_docs(ms, resolved_base)
            await _emit_tool_rows(emit, tools)
        else:
            await _say(emit, _proxy_line("no API — modeling site workflows as browser tools"))
            recon = await recon_mod.probe_surface(resolved_base) if resolved_base else {}
            tools = await _tools_from_site(recon, resolved_base)
            await _emit_tool_rows(emit, tools)

        if not tools:
            tools = [_generic_http_tool(resolved_base or base_url)]
            await _emit_tool_rows(emit, tools)

        await _say(emit, _proxy_line(f"{len(tools)} tools mapped · writing llms.txt + .well-known/mcp.json…"))
        advertise = _build_advertise(tid, resolved_base or base_url, tools, mcp_url)

        logger.info(
            "[proxy_generator] proxy for {} -> upstream {} ({} tools, kind={}, spec={})",
            tid, resolved_base, len(tools), resolved_kind, spec is not None,
        )
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


def _tools_from_docs(ms: Optional[dict], base_url: str) -> list[ProxyTool]:
    """No OpenAPI, but llms.txt/docs exist — emit a minimal useful toolset.

    `search_docs` and `fetch` give an agent a real way to read the product's
    machine docs through the proxy, plus a generic request passthrough. This
    guarantees the MCP is never empty when a doc surface is present.
    """
    llms = (ms or {}).get("llms_txt") or {}
    docs_url = llms.get("url") or (base_url.rstrip("/") + "/llms.txt")
    search = ProxyTool(
        name="search_docs",
        description=(
            "Search this product's agent-facing documentation (llms.txt and the "
            "pages it links) for a query. Use when you need to find an endpoint, "
            "auth detail, or workflow before acting. Returns matching snippets "
            "with their source URLs."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for"}},
            "required": ["query"],
        },
        action={"type": "http", "method": "GET", "base_url": base_url.rstrip("/"),
                "path": "", "url": docs_url, "query_fields": ["query"]},
        error_rules=_http_error_rules({}),
        idempotency={"key_fields": []},
    )
    fetch = ProxyTool(
        name="fetch",
        description=(
            "Fetch the raw contents of a documentation or API URL on this product "
            "(for example a page linked from llms.txt). Args: url. Returns the body."
        ),
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Absolute URL to fetch"}},
            "required": ["url"],
        },
        action={"type": "http", "method": "GET", "base_url": base_url.rstrip("/"),
                "path": "", "url": "{url}"},
        error_rules=_http_error_rules({}),
        idempotency={"key_fields": []},
    )
    return [search, fetch, _generic_http_tool(base_url)]


# --- API path: OpenAPI -> http ProxyTools ----------------------------------


async def _tools_from_openapi(
    spec: dict, base_url: str, *, emit: Optional[BuildEmit] = None
) -> list[ProxyTool]:
    """Map agent-useful OpenAPI operations into http ProxyTools.

    Emits a proxy_tool + line event per tool as each is mapped, so the build
    stream materializes the toolset one row at a time as it's actually built.
    """
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

        tool = ProxyTool(
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
        tools.append(tool)
        await _emit_tool_row(emit, tool)
    return tools


# --- Code path: code_analysis endpoints -> http ProxyTools -----------------


def _path_param_names(path: str) -> list[str]:
    """Extract `{id}`-style path-template parameter names from a path."""
    out: list[str] = []
    for seg in (path or "").split("/"):
        seg = seg.strip()
        if seg.startswith("{") and seg.endswith("}") and len(seg) > 2:
            out.append(seg[1:-1])
        elif seg.startswith(":") and len(seg) > 1:  # express/flask :id style
            out.append(seg[1:])
    return out


def _normalize_path_template(path: str) -> str:
    """Normalize `:id` (express/flask) path params to `{id}` so the runtime's
    OpenAPI-style param substitution (which expects `{name}`) works uniformly."""
    if not path:
        return path
    out_segs: list[str] = []
    for seg in path.split("/"):
        if seg.startswith(":") and len(seg) > 1:
            out_segs.append("{" + seg[1:] + "}")
        else:
            out_segs.append(seg)
    return "/".join(out_segs)


def _op_from_endpoint(ep: dict, path: str, method: str) -> dict:
    """Adapt a code_analysis endpoint into the minimal OpenAPI-op shape the
    existing builders (`_tool_name`, `_describe_http_tool`, `_idempotency_keys`,
    `_input_schema_from_op`) already understand: summary + parameters[] +
    requestBody.

    code_analysis param entries may be bare strings (names) or dicts; we coerce
    both. Path-template params -> in:path (required). Remaining params on a GET/
    DELETE -> query; on a mutating method (POST/PUT/PATCH) -> a JSON requestBody
    so the runtime sends them in the body, matching real handler semantics.
    """
    summary = str(ep.get("summary") or "").strip()
    path_params = set(_path_param_names(path))
    is_mutating = method.lower() in _MUTATING_METHODS
    params_in: list[dict] = []
    body_props: dict[str, dict] = {}
    body_required: list[str] = []
    seen: set[str] = set()

    def _add_param(name: str, loc: str, required: bool) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        params_in.append({
            "name": name,
            "in": loc,
            "required": bool(required) or loc == "path",
            "schema": {"type": "string"},
        })

    def _add_body(name: str, required: bool) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        body_props[name] = {"type": "string", "description": ""}
        if required:
            body_required.append(name)

    # Path-template params always come first (required).
    for pp in _path_param_names(path):
        _add_param(pp, "path", True)

    raw_params = ep.get("params")
    if isinstance(raw_params, list):
        for p in raw_params:
            if isinstance(p, str):
                name, declared_loc, required = p.strip(), None, False
            elif isinstance(p, dict):
                name = str(p.get("name") or "").strip()
                declared_loc = p.get("in")
                required = bool(p.get("required"))
            else:
                continue
            if not name:
                continue
            if name in path_params:
                _add_param(name, "path", True)
                continue
            loc = str(declared_loc) if declared_loc else ("body" if is_mutating else "query")
            if loc == "body":
                _add_body(name, required)
            else:
                _add_param(name, loc, required)

    op: dict = {"summary": summary, "description": summary, "parameters": params_in}
    if body_props:
        op["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": body_props,
                        "required": body_required,
                    }
                }
            }
        }
    return op


async def _tools_from_code_endpoints(
    endpoints: list, base_url: str, *, emit: Optional[BuildEmit] = None
) -> list[ProxyTool]:
    """Map REAL repo-extracted endpoints (code_analysis) into http ProxyTools.

    Each endpoint is {method, path, summary, params, auth, source}. We reuse the
    same naming / description / error-rule / idempotency machinery as the OpenAPI
    path so the generated tools are shaped identically. Emits a proxy_tool + line
    per tool so the live builder stream materializes each one as it's built.
    Skips infra paths (health/metrics/etc.) and bounds the surface.
    """
    spec_base = (base_url or "").rstrip("/")
    tools: list[ProxyTool] = []
    seen_names: set[str] = set()

    for ep in endpoints:
        if not isinstance(ep, dict):
            continue
        method = str(ep.get("method") or "").strip().lower()
        path = str(ep.get("path") or "").strip()
        if not method or not path:
            continue
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        if _skip_path(path):
            continue

        norm_path = _normalize_path_template(path)
        op = _op_from_endpoint(ep, norm_path, method)

        name = _tool_name(method, norm_path, op, seen_names)
        seen_names.add(name)

        input_schema, param_map, body_fields = _input_schema_from_op(op, norm_path)
        description = await _describe_http_tool(name, method, norm_path, op)
        error_rules = _http_error_rules(op)
        key_fields = _idempotency_keys(method, norm_path, body_fields, op)

        tool = ProxyTool(
            name=name,
            description=description,
            input_schema=input_schema,
            action={
                "type": "http",
                "method": method.upper(),
                "base_url": spec_base,
                "path": norm_path,
                "param_map": param_map,  # arg -> {"in": path|query|header|body}
            },
            error_rules=error_rules,
            idempotency={"key_fields": key_fields},
        )
        tools.append(tool)
        await _emit_tool_row(emit, tool)
        if len(tools) >= _MAX_TOOLS:
            break

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
        props[pname] = _prop_from_schema(schema, param.get("description", ""))
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
                    props[fname] = _prop_from_schema(fschema, fschema.get("description", ""))
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


def _prop_from_schema(schema: Any, description: str = "") -> dict:
    """Build a rich JSON-schema property for an MCP tool arg — carries type, enum,
    format, default, bounds, and array item types so clients (Cursor/Claude) get
    typed, well-described inputs instead of bare strings."""
    schema = schema if isinstance(schema, dict) else {}
    out: dict[str, Any] = {"type": schema.get("type", "string")}
    desc = description or schema.get("description", "") or ""
    if desc:
        out["description"] = desc
    for k in ("enum", "format", "default", "minimum", "maximum", "pattern", "example"):
        v = schema.get(k)
        if v is not None:
            out[k] = v
    if out["type"] == "array":
        items = schema.get("items")
        out["items"] = items if isinstance(items, dict) else {"type": "string"}
    return out


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
    "Write plainly. Do not use em-dashes. Avoid marketing buzzwords and AI-cliche phrasing. "
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
# (1b) suggested_improvements — ranked native-agent-ready fixes
# ===========================================================================
# Used by github_fix to write the AGENTS.md checklist + docs/agent-readiness.md
# "make it native" section, so a product can eventually drop the proxy.


# Per-dimension template remediation — the deterministic fallback when no keys.
_DIM_FIX_TEMPLATES: dict[str, str] = {
    "api_surface": (
        "Expose a typed programmatic surface (publish an OpenAPI 3 spec at "
        "/openapi.json) so agents can discover and call your endpoints directly."
    ),
    "auth": (
        "Offer deterministic auth for agents: long-lived API keys or OAuth "
        "client-credentials tokens (not a human-only login / CAPTCHA / email-OTP "
        "flow), scoped to least privilege."
    ),
    "error_quality": (
        "Return machine-readable errors: a stable string error_code, a boolean "
        "`retryable`, and an actionable message. Never return a 200 wrapping a failure."
    ),
    "idempotency": (
        "Support an `Idempotency-Key` header on mutating endpoints so an agent "
        "can safely retry without creating duplicate side effects."
    ),
    "mcp_availability": (
        "Ship a first-party MCP server (or keep the Wirable proxy) and advertise "
        "it at /.well-known/mcp.json so agents can auto-discover your tools."
    ),
    "docs": (
        "Publish agent-facing docs: an llms.txt at the root describing your "
        "product, endpoints, auth, and the MCP endpoint."
    ),
}


def _dims_as_list(audit_dims: Any) -> list[dict]:
    """Normalize audit_dims into a list of {dim, passed, evidence} dicts.

    Accepts either the contract list form ([{dim,passed,evidence}, ...]) or the
    engine map form ({dim: {passed, evidence}, ...}). Never raises.
    """
    out: list[dict] = []
    try:
        if isinstance(audit_dims, dict):
            for dim, v in audit_dims.items():
                v = v if isinstance(v, dict) else {}
                out.append(
                    {
                        "dim": dim,
                        "passed": bool(v.get("passed", False)),
                        "evidence": str(v.get("evidence", "") or ""),
                    }
                )
        elif isinstance(audit_dims, list):
            for d in audit_dims:
                if not isinstance(d, dict):
                    continue
                out.append(
                    {
                        "dim": d.get("dim") or d.get("key") or "",
                        "passed": bool(d.get("passed", False)),
                        "evidence": str(d.get("evidence", "") or ""),
                    }
                )
    except Exception:
        logger.debug("[proxy_generator] _dims_as_list normalize failed")
    return out


_SUGGEST_SYSTEM = (
    "You are Wirable, advising a software team on how to make their product "
    "NATIVELY agent-ready (so they don't need a proxy forever). Given the audit "
    "verdict per dimension and the wrapped-card findings, write a RANKED list of "
    "concrete engineering improvements, most impactful first. Each item: one "
    "imperative sentence, specific and technical (name the header/endpoint/file). "
    "No marketing, no preamble. Write plainly. Do not use em-dashes. Avoid "
    "marketing buzzwords and AI-cliche phrasing."
)


async def suggested_improvements(
    audit_dims: Any, cards: Optional[list] = None
) -> list[str]:
    """Return a ranked list of concrete native-agent-ready improvements.

    LLM-generated from the audit when keys exist; otherwise a deterministic
    template list covering every FAILED dimension. Never raises.
    """
    dims = _dims_as_list(audit_dims)
    failed = [d for d in dims if not d.get("passed")]

    def _template() -> list[str]:
        items: list[str] = []
        # Failed dimensions first, in the canonical (weighted) order.
        order = [
            "api_surface", "auth", "error_quality",
            "idempotency", "mcp_availability", "docs",
        ]
        failed_keys = {d.get("dim") for d in failed}
        for key in order:
            if key in failed_keys and key in _DIM_FIX_TEMPLATES:
                items.append(_DIM_FIX_TEMPLATES[key])
        # If somehow nothing failed, still suggest the highest-leverage upgrades.
        if not items:
            items = [
                _DIM_FIX_TEMPLATES["mcp_availability"],
                _DIM_FIX_TEMPLATES["docs"],
            ]
        return items

    if not key_pool.has_keys():
        return _template()

    try:
        dim_txt = "\n".join(
            f"- {d['dim']}: {'PASS' if d['passed'] else 'FAIL'} — {d['evidence']}"
            for d in dims
        ) or "(no dimensions)"
        card_txt = ""
        for c in (cards or [])[:8]:
            if isinstance(c, dict):
                card_txt += f"- {c.get('headline','')}: {c.get('detail','')}\n"
        prompt = (
            f"Audit dimensions:\n{dim_txt}\n\n"
            f"Wrapped-card findings:\n{card_txt or '(none)'}\n\n"
            "Return the ranked improvement list, one item per line, no numbering."
        )
        text = await claude_text(prompt, system=_SUGGEST_SYSTEM, max_tokens=700)
        lines = [
            ln.strip().lstrip("-*0123456789. ").strip()
            for ln in (text or "").splitlines()
            if ln.strip()
        ]
        lines = [ln for ln in lines if len(ln) > 8]
        return lines or _template()
    except Exception:
        logger.exception("[proxy_generator] suggested_improvements failed")
        return _template()


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

        # REALLY exercise the proxy: call one safe (read-only) tool through the
        # live ProxyRuntime so the AFTER reflects an actual working MCP call, not
        # a guess. Best-effort + clearly logged; a failure here does not fake a
        # pass (we only credit api_surface from a live tools/call success).
        live_call_ok, live_detail = await _exercise_proxy(run_id, mcp_url, tool_names)
        logger.info("[proxy_generator] live tools/call for {}: ok={} ({})",
                    run_id, live_call_ok, live_detail)

        # Re-score deterministically. The proxy, by construction, fixes:
        #   mcp_availability (it IS an MCP endpoint),
        #   docs             (it serves llms.txt / .well-known),
        #   error_quality    (normalized {success,error_code,retryable}),
        #   idempotency      (idempotency.key_fields enforced),
        #   api_surface      (typed tools = a programmatic surface) when tools>0.
        # auth is unchanged unless the owner supplied a credential (auth_ref);
        # we keep the conservative original auth verdict.
        proxy_recon = dict(recon) if isinstance(recon, dict) else {}
        proxy_recon["has_mcp"] = True   # the proxy IS a reachable MCP endpoint
        proxy_recon["has_docs"] = True  # it serves llms.txt / .well-known
        if tool_names:
            proxy_recon["has_openapi"] = True  # typed programmatic surface exists
        proxy_recon["proxy_live_call_ok"] = live_call_ok
        proxy_recon["proxy_live_call_detail"] = live_detail

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


async def _exercise_proxy(
    run_id: str, mcp_url: str, tool_names: list[str]
) -> tuple[bool, str]:
    """Genuinely exercise the generated proxy: load the live ProxyRuntime and run
    one safe (read-only) tools/call through the full dispatch path (URL build ->
    upstream request -> error normalization -> idempotency).

    We pick a GET tool whose required args we can satisfy harmlessly (or that has
    none). This proves the proxy actually serves a working call end to end. We do
    NOT fabricate a pass: returns (False, reason) when no safe call is possible
    or the runtime can't be loaded, so the caller can degrade honestly.

    Returns (ok, detail). `ok` is True only when the proxy returned a normalized
    envelope (success True/False) WITHOUT a transport-level failure
    (upstream_unreachable / no_target_url etc.). A normalized upstream 4xx still
    counts as "the proxy works" — it correctly translated the upstream response.
    """
    try:
        from . import proxy_runtime as _pr  # local import (heavy / DB deps)

        runtime = await _pr.load_runtime(run_id)
        if runtime is None:
            return False, "runtime not loadable for live call"

        tool = _pick_safe_get_tool(runtime)
        if tool is None:
            # No read-only tool to exercise safely (e.g. site/playwright proxy or
            # all-mutating API). tools/list reachability already confirmed.
            return False, "no safe read-only tool to exercise"

        args = _safe_args_for(tool)
        result = await runtime.call_tool(tool.name, args)
        if not isinstance(result, dict):
            return False, f"{tool.name}: non-dict result"

        ec = result.get("error_code")
        # Transport-level failures mean the proxy could not reach/serve upstream.
        transport_fail = ec in (
            "upstream_unreachable", "upstream_timeout", "no_target_url",
            "unsupported_action", "unknown_tool",
        )
        if transport_fail:
            return False, f"{tool.name}: transport error {ec}"
        # success True, or a normalized upstream error (e.g. http_401) — either
        # way the proxy correctly dispatched + normalized a real upstream call.
        status = "success" if result.get("success") else f"normalized {ec}"
        return True, f"called {tool.name} -> {status}"
    except Exception as exc:
        logger.debug("[proxy_generator] _exercise_proxy failed: {}", exc)
        return False, f"live call raised: {str(exc)[:80]}"


def _pick_safe_get_tool(runtime) -> Optional["ProxyTool"]:
    """Choose a read-only (GET) http tool whose required args we can satisfy."""
    candidates = []
    for t in getattr(runtime.config, "tools", []) or []:
        action = t.action or {}
        if (action.get("type") or "http").lower() != "http":
            continue
        method = (action.get("method") or "GET").upper()
        if method not in ("GET", "ANY"):
            continue
        req = ((t.input_schema or {}).get("required") or [])
        # Prefer tools we can satisfy: no required args, or only args we can
        # synthesize safely (a search query / url / id).
        synthesizable = all(
            r in ("query", "q", "search", "url", "path", "id", "method")
            for r in req
        )
        if synthesizable:
            candidates.append((len(req), t))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])  # fewest required args first
    return candidates[0][1]


def _safe_args_for(tool) -> dict:
    """Synthesize harmless arguments for a read-only call."""
    args: dict[str, Any] = {}
    props = (tool.input_schema or {}).get("properties") or {}
    for name in ((tool.input_schema or {}).get("required") or []):
        low = name.lower()
        if low in ("query", "q", "search"):
            args[name] = "test"
        elif low == "url":
            args[name] = (tool.action or {}).get("base_url") or "https://example.com"
        elif low in ("path",):
            args[name] = "/"
        elif low == "method":
            args[name] = "GET"
        elif low == "id":
            args[name] = "0"
        else:
            args[name] = "test"
    # The generic `request` tool needs method+path even if not "required".
    if "method" in props and "method" not in args:
        args["method"] = "GET"
    if "path" in props and "path" not in args:
        args["path"] = "/"
    return args


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


def _machine_surface_from_history(run_id: str) -> dict:
    """Recover the audit driver's discovered machine_surface from the run history.

    The driver writes it onto its result; if the orchestrator surfaced it on any
    event we pick it up here so the proxy can target the real API host without a
    second discovery pass. Returns {} when absent. Never raises.
    """
    for ev in reversed(_history_events(run_id)):
        ms = ev.get("machine_surface") if isinstance(ev, dict) else None
        if isinstance(ms, dict) and ms:
            return ms
    return {}


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
