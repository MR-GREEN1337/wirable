"""
Proxy runtime (Wirable) — the MOAT. Hosts a generated ProxyConfig as a live
MCP-over-HTTP server, keyed by proxy id.

What this module owns:
  * ProxyRuntime — loads a persisted ProxyConfig (from the MCP model) and serves
    MCP `tools/list` + `tools/call`. tools/call dispatches by action.type:
      - "http":       translate the call into a real upstream HTTP request to
                      base_url, inject the OWNER's credential server-side (from
                      auth_ref via the auth broker — NEVER exposed to the caller),
                      normalize the response to {success,error_code,retryable,data}
                      per the tool's error_rules, and apply idempotency caching
                      (hash of tool name + idempotency.key_fields → cached result
                      within a TTL, so a retry never double-charges/double-books).
      - "playwright": best-effort browser dispatch via the Daytona sandbox
                      against the live site (structure in place; full step
                      execution is a marked TODO — http is the demo-critical path).
  * Auth broker — two creds kept separate (CONTRACTS.md):
      1. the CALLER's scoped agent key (owner-issued, validated before serving
         tools/call), and
      2. the OWNER's backend credential (injected into the upstream request).
  * Owner-issued scoped agent keys — minted/persisted/validated here.
  * Persistence helpers — store/load the ProxyConfig + scoped keys + an
    agent-call counter on the existing MCP model (no migration; reuses the
    schemas_json / llms_txt / evals_json JSON columns).

Persistence layout on the MCP row (id == proxy_id == run_id):
    MCP.schemas_json            = ProxyConfig.to_dict()
    MCP.llms_txt                = advertise.llms_txt (convenience)
    MCP.evals_json = {
        "auth_secret":   <opaque owner credential, keyed by auth_ref>  # server-only
        "auth_ref":      <str>
        "keys":          { "<agent_key>": {"scopes": [...], "created_at": iso} }
        "agent_calls":   <int>                       # incremented per tools/call
        "idem":          { "<idem_hash>": {"result": {...}, "ts": <epoch>} }
        "mcp_url":       <str>                        # hosted absolute/relative url
    }
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid as _uuid
from typing import Any, Optional

import httpx
from loguru import logger

from ..core.contracts import ProxyConfig, ProxyTool
from ..core.config import settings
from ..core.database import AsyncSessionLocal
from ..models.mcp import MCP
from ..models.client import Client
from ..models.audit import Audit

# Idempotency cache TTL — a repeated call with the same key within this window
# returns the cached result instead of re-hitting the upstream (no double-charge).
_IDEM_TTL_S = 600
# Upstream HTTP call timeout for the http action dispatcher.
_HTTP_TIMEOUT_S = 20.0

# MCP protocol versions we know how to speak (Streamable HTTP transport).
# We echo back the client's requested version when it's one of these; otherwise
# we fall back to our default. 2025-06-18 is the current spec revision.
MCP_PROTOCOL_VERSION = "2025-06-18"
_SUPPORTED_PROTOCOL_VERSIONS = {
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
SERVER_VERSION = "1.0.0"


def negotiate_protocol_version(requested: Optional[str]) -> str:
    """Echo back the client's protocolVersion when we know it, else our default."""
    if isinstance(requested, str) and requested in _SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return MCP_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# proxy_id helper — proxy_id is the run_id (a uuid string).
# ---------------------------------------------------------------------------

def _as_uuid(proxy_id: str) -> Optional[_uuid.UUID]:
    try:
        return _uuid.UUID(str(proxy_id))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Normalized envelope — the single response shape every tools/call returns.
# ---------------------------------------------------------------------------

def _envelope(
    success: bool,
    *,
    data: Any = None,
    error_code: Optional[str] = None,
    retryable: bool = False,
) -> dict:
    return {
        "success": success,
        "error_code": error_code,
        "retryable": retryable,
        "data": data,
    }


# ---------------------------------------------------------------------------
# Persistence — store / load the ProxyConfig on the MCP row.
# ---------------------------------------------------------------------------

async def _resolve_client_id(db, company_id: Optional[_uuid.UUID]) -> Optional[_uuid.UUID]:
    """Find (or create) a Client for this company so MCP.client_id (NOT NULL)
    can be satisfied without a migration. Demo runs may have no logged-in user,
    so a company-scoped client with user_id=NULL is acceptable.
    """
    from sqlalchemy import select

    if company_id is not None:
        res = await db.execute(select(Client).where(Client.company_id == company_id))
        client = res.scalars().first()
        if client:
            return client.id
        client = Client(company_id=company_id)
        db.add(client)
        await db.flush()
        return client.id

    # No company — fall back to ANY client (last resort) or create a bare one.
    res = await db.execute(select(Client))
    client = res.scalars().first()
    if client:
        return client.id
    client = Client()
    db.add(client)
    await db.flush()
    return client.id


async def persist_proxy_config(
    proxy_id: str,
    config: ProxyConfig,
    *,
    auth_secret: Optional[dict] = None,
    mcp_url: Optional[str] = None,
    audit_id: Optional[_uuid.UUID] = None,
    company_id: Optional[_uuid.UUID] = None,
) -> None:
    """Upsert the generated ProxyConfig onto an MCP row keyed by proxy_id.

    Stores the owner credential (auth_secret) server-side in evals_json keyed by
    auth_ref — it is NEVER returned over the wire. Idempotent: a re-run for the
    same proxy_id overwrites the config but PRESERVES issued keys + the agent
    call counter + the auth secret if not re-supplied.
    """
    pid = _as_uuid(proxy_id)
    async with AsyncSessionLocal() as db:
        try:
            row: Optional[MCP] = None
            if pid is not None:
                row = await db.get(MCP, pid)

            evals: dict = {}
            if row is not None and isinstance(row.evals_json, dict):
                evals = dict(row.evals_json)

            # Preserve / update the owner credential, keyed by auth_ref.
            evals.setdefault("keys", {})
            evals.setdefault("agent_calls", 0)
            evals.setdefault("idem", {})
            evals["auth_ref"] = config.auth_ref
            if auth_secret is not None:
                evals["auth_secret"] = auth_secret
            if mcp_url:
                evals["mcp_url"] = mcp_url

            cfg_dict = config.to_dict()

            if row is None:
                client_id = await _resolve_client_id(db, company_id)
                row = MCP(
                    id=pid or _uuid.uuid4(),
                    client_id=client_id,
                    audit_id=audit_id,
                    schemas_json=cfg_dict,
                    llms_txt=(config.advertise or {}).get("llms_txt"),
                    evals_json=evals,
                    pr_status="hosted",
                )
                db.add(row)
            else:
                row.schemas_json = cfg_dict
                row.llms_txt = (config.advertise or {}).get("llms_txt")
                row.evals_json = evals
                row.pr_status = "hosted"
                if audit_id is not None:
                    row.audit_id = audit_id

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("persist_proxy_config failed for proxy %s", proxy_id)
            raise


async def _load_mcp_row(proxy_id: str) -> Optional[MCP]:
    pid = _as_uuid(proxy_id)
    if pid is None:
        return None
    async with AsyncSessionLocal() as db:
        return await db.get(MCP, pid)


async def get_advertise(proxy_id: str) -> Optional[dict]:
    """Return the persisted advertise bundle (for the .well-known endpoint)."""
    row = await _load_mcp_row(proxy_id)
    if row is None or not isinstance(row.schemas_json, dict):
        return None
    return (row.schemas_json or {}).get("advertise")


async def get_proxy_meta(proxy_id: str) -> Optional[dict]:
    """Dashboard helper: {proxy_status, mcp_url, agent_calls, tool_count}."""
    row = await _load_mcp_row(proxy_id)
    if row is None or not isinstance(row.schemas_json, dict):
        return None
    cfg = row.schemas_json or {}
    evals = row.evals_json if isinstance(row.evals_json, dict) else {}
    return {
        "proxy_status": "ready",
        "mcp_url": evals.get("mcp_url"),
        "agent_calls": int(evals.get("agent_calls", 0) or 0),
        "tool_count": len(cfg.get("tools", []) or []),
    }


# ---------------------------------------------------------------------------
# Auth broker
# ---------------------------------------------------------------------------

class AuthBroker:
    """Two-credential broker (CONTRACTS.md): validates the CALLER's scoped agent
    key, and resolves the OWNER's backend credential for upstream injection. The
    owner credential is never exposed to the caller.
    """

    def __init__(self, evals: dict) -> None:
        self._evals = evals or {}

    # -- caller side: scoped agent key ------------------------------------
    def validate_agent_key(self, key: Optional[str]) -> bool:
        """True if `key` is a valid owner-issued scoped key for this proxy.

        For the 36h demo a static scoped key is the model; full OAuth dynamic
        client registration is a documented prod follow-up.
        # TODO(prod): OAuth 2.1 dynamic client registration + per-scope checks.
        """
        if not key:
            return False
        keys = self._evals.get("keys") or {}
        return key in keys

    # -- owner side: backend credential injection -------------------------
    def owner_credential(self, auth_ref: Optional[str]) -> Optional[dict]:
        """Resolve the stored owner credential by auth_ref. Server-only."""
        if not auth_ref:
            return None
        secret = self._evals.get("auth_secret")
        # Single-tenant-per-proxy: the stored secret IS the owner credential.
        return secret if isinstance(secret, dict) else None

    def inject_auth(
        self,
        headers: dict,
        params: dict,
        auth_ref: Optional[str],
    ) -> None:
        """Mutate headers/params in place to carry the owner's credential.

        Supported credential shapes (owner supplies via POST /run/{id}/proxy):
          {"type":"bearer","token":"..."}        -> Authorization: Bearer ...
          {"type":"api_key","header":"X-Api-Key","value":"..."}
          {"type":"api_key","query":"api_key","value":"..."}
          {"type":"basic","username":"..","password":".."}  (pre-encoded header)
          {"header":"...","value":"..."}         -> raw header
          {"token":"..."}                        -> Authorization: Bearer ...
        """
        cred = self.owner_credential(auth_ref)
        if not cred:
            return
        ctype = (cred.get("type") or "").lower()
        if ctype == "bearer" or (not ctype and cred.get("token")):
            headers["Authorization"] = f"Bearer {cred.get('token', '')}"
        elif ctype == "api_key":
            if cred.get("header"):
                headers[cred["header"]] = cred.get("value", "")
            elif cred.get("query"):
                params[cred["query"]] = cred.get("value", "")
        elif ctype == "basic":
            import base64

            raw = f"{cred.get('username','')}:{cred.get('password','')}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        elif cred.get("header"):
            headers[cred["header"]] = cred.get("value", "")


# ---------------------------------------------------------------------------
# ProxyRuntime — serves a ProxyConfig as MCP-over-HTTP
# ---------------------------------------------------------------------------

class ProxyRuntime:
    """Serves one proxy's ProxyConfig as MCP-over-HTTP."""

    def __init__(self, proxy_id: str, config: ProxyConfig, evals: dict) -> None:
        self.proxy_id = proxy_id
        self.config = config
        self.evals = evals or {}
        self.broker = AuthBroker(self.evals)

    # -- MCP server identity ----------------------------------------------
    def server_name(self) -> str:
        """A stable, human-readable server name for serverInfo."""
        domain = None
        try:
            base = self.config.base_url or ""
            if base:
                from urllib.parse import urlparse

                domain = urlparse(base).netloc or base
        except Exception:
            domain = None
        return f"wirable-proxy:{domain or self.proxy_id}"

    def requires_auth(self) -> bool:
        """Whether this proxy enforces a scoped agent key on tools/call.

        A proxy is protected once the owner has minted at least one scoped key.
        With no keys issued, the endpoint stays open (current/demo behavior).
        """
        keys = self.evals.get("keys") or {}
        return bool(keys)

    def initialize_result(self, requested_version: Optional[str]) -> dict:
        """The MCP `initialize` result (capabilities + serverInfo)."""
        return {
            "protocolVersion": negotiate_protocol_version(requested_version),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.server_name(), "version": SERVER_VERSION},
        }

    # -- tool lookup ------------------------------------------------------
    def _tool(self, name: str) -> Optional[ProxyTool]:
        for t in self.config.tools:
            if t.name == name:
                return t
        return None

    # -- MCP tools/list ---------------------------------------------------
    async def list_tools(self) -> list[dict]:
        """MCP tools/list — name, description, inputSchema for each tool."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema or {"type": "object", "properties": {}},
            }
            for t in self.config.tools
        ]

    # -- MCP tools/call ---------------------------------------------------
    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Dispatch a tool call; return the normalized envelope.

        Applies idempotency BEFORE dispatch (cache hit short-circuits) and caches
        successful results after. Increments the persisted agent-call counter.
        """
        arguments = arguments or {}
        tool = self._tool(name)
        if tool is None:
            return _envelope(False, error_code="unknown_tool", retryable=False)

        # Increment the agent-call counter (best-effort persistence).
        await self._bump_calls()

        # Idempotency: short-circuit a repeat within the TTL.
        idem_hash = self._idem_hash(tool, arguments)
        if idem_hash:
            cached = self._idem_get(idem_hash)
            if cached is not None:
                logger.debug("idempotent cache hit for %s/%s", self.proxy_id, name)
                out = dict(cached)
                out["_idempotent_replay"] = True
                return out

        action = tool.action or {}
        atype = (action.get("type") or "http").lower()
        if atype == "http":
            result = await self._dispatch_http(tool, arguments)
        elif atype == "playwright":
            result = await self._dispatch_playwright(tool, arguments)
        else:
            result = _envelope(False, error_code="unsupported_action", retryable=False)

        # Cache only successful results (a failed call should be retryable).
        if idem_hash and result.get("success"):
            await self._idem_put(idem_hash, result)
        return result

    # -- http dispatcher --------------------------------------------------
    async def _dispatch_http(self, tool: ProxyTool, arguments: dict) -> dict:
        action = tool.action or {}
        method = (action.get("method") or "GET").upper()
        url = self._build_url(action, arguments)
        if not url:
            return _envelope(False, error_code="no_target_url", retryable=False)

        headers: dict = dict(action.get("headers") or {})
        params: dict = self._build_query(action, arguments)
        body = self._build_body(action, arguments)

        # Inject the OWNER credential server-side (never exposed to the caller).
        self.broker.inject_auth(headers, params, self.config.auth_ref)

        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S, follow_redirects=True
            ) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=headers or None,
                    params=params or None,
                    json=body if body is not None else None,
                )
        except httpx.TimeoutException:
            return _envelope(False, error_code="upstream_timeout", retryable=True)
        except Exception as exc:
            logger.debug("http dispatch failed for %s: %s", url, exc)
            return _envelope(False, error_code="upstream_unreachable", retryable=True)

        return self._normalize_http(tool, resp)

    def _normalize_http(self, tool: ProxyTool, resp: "httpx.Response") -> dict:
        """Normalize an upstream HTTP response to the standard envelope using the
        tool's error_rules. error_rules shape (from the generator):
            { "status": { "<code or code-range>": {"code": "...", "retryable": bool} },
              "retryable_statuses": [429, 503, ...] }
        Falls back to sane HTTP defaults when no rule matches.
        """
        status = resp.status_code
        # Parse body (json preferred, else text).
        try:
            data: Any = resp.json()
        except Exception:
            data = resp.text

        if 200 <= status < 300:
            return _envelope(True, data=data)

        rules = tool.error_rules or {}
        # Explicit per-status mapping wins.
        status_rules = rules.get("status") or {}
        rule = status_rules.get(str(status)) or status_rules.get(status)
        if isinstance(rule, dict):
            return _envelope(
                False,
                data=data,
                error_code=str(rule.get("code") or f"http_{status}"),
                retryable=bool(rule.get("retryable", False)),
            )

        retryable_statuses = set(rules.get("retryable_statuses") or [429, 500, 502, 503, 504])
        retryable = status in retryable_statuses
        return _envelope(
            False,
            data=data,
            error_code=f"http_{status}",
            retryable=retryable,
        )

    # -- playwright dispatcher (best-effort) ------------------------------
    async def _dispatch_playwright(self, tool: ProxyTool, arguments: dict) -> dict:
        """Drive the live site through the Daytona sandbox to perform the mapped
        browser action, returning the same normalized envelope.

        Structure is in place; full step execution against a generated Playwright
        script is the heavier path. http is the demo-critical path.
        # TODO(Wave2+): render action.steps/selectors into a Playwright script,
        #   run it in the sandbox, scrape the result, normalize via error_rules.
        """
        try:
            from ..core.sandbox import DaytonaClient  # local import; heavy dep

            action = tool.action or {}
            steps = action.get("steps") or []
            script = self._render_playwright_script(self.config.base_url, steps, arguments)
            async with DaytonaClient.sandbox() as sb:
                await sb.upload("/proxy_action.py", script.encode())
                raw = await sb.exec(
                    "python /proxy_action.py 2>&1 || true", timeout=120
                )
                out = await sb.read("/proxy_result.json")
            if out:
                try:
                    parsed = json.loads(out.decode())
                    return _envelope(
                        bool(parsed.get("success", True)),
                        data=parsed.get("data", parsed),
                        error_code=parsed.get("error_code"),
                        retryable=bool(parsed.get("retryable", False)),
                    )
                except Exception:
                    pass
            return _envelope(
                False,
                data={"log": (raw or "")[:2000]},
                error_code="playwright_no_result",
                retryable=True,
            )
        except Exception as exc:
            logger.debug("playwright dispatch failed: %s", exc)
            return _envelope(
                False, error_code="playwright_unavailable", retryable=True
            )

    @staticmethod
    def _render_playwright_script(base_url: str, steps: list, arguments: dict) -> str:
        """Best-effort: emit a tiny Playwright script that runs `steps` and writes
        /proxy_result.json. Steps are passed through; the generator owns shape.
        # TODO(Wave2+): richer step compilation (waits, assertions, extraction).
        """
        payload = json.dumps(
            {"base_url": base_url, "steps": steps, "arguments": arguments}
        )
        return (
            "import json\n"
            "spec = " + payload + "\n"
            "result = {'success': False, 'error_code': 'not_implemented', "
            "'retryable': False, 'data': {'spec': spec}}\n"
            "# TODO(Wave2+): drive playwright per spec['steps'] against base_url.\n"
            "open('/proxy_result.json','w').write(json.dumps(result))\n"
        )

    # -- request building -------------------------------------------------
    def _build_url(self, action: dict, arguments: dict) -> str:
        """Resolve the absolute upstream URL from the action mapping.

        Supports an absolute `url`, or a `path`/`url` template with {field}
        placeholders filled from arguments, joined onto base_url.
        """
        raw = action.get("url") or action.get("path") or ""
        raw = self._fill_template(raw, arguments)
        if raw.startswith(("http://", "https://")):
            return raw
        base = (self.config.base_url or "").rstrip("/")
        if not raw:
            return base
        return base + ("" if raw.startswith("/") else "/") + raw

    def _build_query(self, action: dict, arguments: dict) -> dict:
        """Build query params: explicit action.query (templated) + any args the
        mapping routes to the query string via action.query_fields.
        """
        params: dict = {}
        for k, v in (action.get("query") or {}).items():
            params[k] = self._fill_template(str(v), arguments)
        for field in action.get("query_fields") or []:
            if field in arguments:
                params[field] = arguments[field]
        return params

    def _build_body(self, action: dict, arguments: dict) -> Any:
        """Build the JSON body for write methods.

        Priority: explicit action.body template; else action.body_fields subset;
        else for non-GET pass through all arguments not consumed by the URL.
        """
        if "body" in action:
            return self._fill_obj(action["body"], arguments)
        body_fields = action.get("body_fields")
        if body_fields is not None:
            return {f: arguments[f] for f in body_fields if f in arguments}
        method = (action.get("method") or "GET").upper()
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            return arguments
        return None

    @staticmethod
    def _fill_template(tmpl: str, arguments: dict) -> str:
        out = tmpl
        for k, v in (arguments or {}).items():
            out = out.replace("{" + str(k) + "}", str(v))
        return out

    def _fill_obj(self, obj: Any, arguments: dict) -> Any:
        if isinstance(obj, str):
            # Whole-value substitution {field} -> raw arg value (preserve type).
            if obj.startswith("{") and obj.endswith("}") and obj[1:-1] in arguments:
                return arguments[obj[1:-1]]
            return self._fill_template(obj, arguments)
        if isinstance(obj, dict):
            return {k: self._fill_obj(v, arguments) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._fill_obj(v, arguments) for v in obj]
        return obj

    # -- idempotency ------------------------------------------------------
    def _idem_hash(self, tool: ProxyTool, arguments: dict) -> Optional[str]:
        key_fields = (tool.idempotency or {}).get("key_fields") or []
        if not key_fields:
            return None
        key_obj = {f: arguments.get(f) for f in key_fields}
        raw = tool.name + "|" + json.dumps(key_obj, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _idem_get(self, idem_hash: str) -> Optional[dict]:
        idem = self.evals.get("idem") or {}
        entry = idem.get(idem_hash)
        if not entry:
            return None
        if time.time() - float(entry.get("ts", 0)) > _IDEM_TTL_S:
            return None
        return entry.get("result")

    async def _idem_put(self, idem_hash: str, result: dict) -> None:
        # Persist into the MCP row's evals_json so the cache survives across
        # workers/requests (prevents double-charge even on a different process).
        pid = _as_uuid(self.proxy_id)
        if pid is None:
            return
        async with AsyncSessionLocal() as db:
            try:
                row = await db.get(MCP, pid)
                if row is None:
                    return
                evals = dict(row.evals_json) if isinstance(row.evals_json, dict) else {}
                idem = dict(evals.get("idem") or {})
                # Prune expired entries to bound growth.
                now = time.time()
                idem = {
                    h: e
                    for h, e in idem.items()
                    if now - float(e.get("ts", 0)) <= _IDEM_TTL_S
                }
                idem[idem_hash] = {"result": result, "ts": now}
                evals["idem"] = idem
                row.evals_json = evals
                self.evals = evals
                await db.commit()
            except Exception:
                await db.rollback()
                logger.debug("idem persist failed (non-fatal) for %s", self.proxy_id)

    async def _bump_calls(self) -> None:
        pid = _as_uuid(self.proxy_id)
        if pid is None:
            return
        async with AsyncSessionLocal() as db:
            try:
                row = await db.get(MCP, pid)
                if row is None:
                    return
                evals = dict(row.evals_json) if isinstance(row.evals_json, dict) else {}
                evals["agent_calls"] = int(evals.get("agent_calls", 0) or 0) + 1
                row.evals_json = evals
                self.evals = evals
                await db.commit()
            except Exception:
                await db.rollback()
                logger.debug("agent_calls bump failed (non-fatal) for %s", self.proxy_id)


# ---------------------------------------------------------------------------
# Loading + key issuance (module-level API used by the endpoints)
# ---------------------------------------------------------------------------

async def load_runtime(proxy_id: str) -> Optional[ProxyRuntime]:
    """Load the stored ProxyConfig for a proxy id and build a runtime, or None."""
    row = await _load_mcp_row(proxy_id)
    if row is None or not isinstance(row.schemas_json, dict):
        return None
    try:
        config = ProxyConfig.from_dict(row.schemas_json)
    except Exception:
        logger.exception("failed to deserialize ProxyConfig for %s", proxy_id)
        return None
    evals = row.evals_json if isinstance(row.evals_json, dict) else {}
    return ProxyRuntime(proxy_id, config, evals)


async def issue_agent_key(
    proxy_id: str, scopes: Optional[list[str]] = None
) -> dict:
    """Owner issues a scoped agent key for a proxy; persist + return it ONCE.

    For the 36h demo a static scoped key is the model.
    # TODO(prod): OAuth 2.1 dynamic client registration with refresh + revocation.
    """
    pid = _as_uuid(proxy_id)
    if pid is None:
        raise ValueError("invalid proxy id")

    key = "wk_" + secrets.token_urlsafe(32)
    async with AsyncSessionLocal() as db:
        row = await db.get(MCP, pid)
        if row is None:
            raise LookupError("no proxy for this id")
        evals = dict(row.evals_json) if isinstance(row.evals_json, dict) else {}
        keys = dict(evals.get("keys") or {})
        keys[key] = {
            "scopes": scopes or [],
            "created_at": _now_iso(),
        }
        evals["keys"] = keys
        row.evals_json = evals
        await db.commit()

    return {"proxy_id": proxy_id, "key": key, "scopes": scopes or []}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# mcp_url helper — shared by the endpoint + persistence
# ---------------------------------------------------------------------------

def mcp_url_for(proxy_id: str) -> str:
    """Absolute (when APP_BASE_URL set) MCP url for a proxy."""
    base = (settings.APP_BASE_URL or "").rstrip("/")
    path = f"/api/v1/proxy/{proxy_id}/mcp"
    return base + path if base else path


# ---------------------------------------------------------------------------
# Public directory / registry — every product with a live hosted proxy.
# ---------------------------------------------------------------------------

def _is_self_or_test_domain(domain: str) -> bool:
    """True for domains we never want in the public directory: Wirable's own
    infra, the box IP / sslip.io fallbacks, and localhost/test placeholders."""
    d = (domain or "").lower().strip()
    if not d:
        return True
    # Strip scheme/path if a full URL slipped in.
    d = d.replace("https://", "").replace("http://", "").split("/")[0]
    BAD_SUBSTR = (
        "wirable.dev", "sslip.io", "5.161.110.99", "localhost",
        "127.0.0.1", "0.0.0.0", "example.com", "test", "::1",
    )
    return any(s in d for s in BAD_SUBSTR)


async def list_public_registry() -> list[dict]:
    """Return the public directory of live hosted proxies.

    Shape: [{domain, score, mcp_url, tool_count}]. Joins each hosted MCP row to
    its company (for the domain + score). Hygiene: excludes self/test domains,
    requires a real score, dedups by domain (newest wins), and always builds the
    canonical https mcp_url (never the stale http://IP:3001). Fully defensive —
    never raises; on any error returns whatever was collected so far (or []).
    """
    from sqlalchemy import select

    from ..models.company import Company

    out: list[dict] = []
    seen_domains: set[str] = set()
    try:
        async with AsyncSessionLocal() as db:
            # Only proxies that have actually been hosted carry pr_status="hosted".
            res = await db.execute(
                select(MCP).where(MCP.pr_status.in_(("hosted", "verified")))
                .order_by(MCP.created_at.desc())
            )
            rows = res.scalars().all()

            # Resolve company per client in a small cache to avoid N+1 explosions.
            company_by_client: dict = {}

            for row in rows:
                try:
                    cfg = row.schemas_json if isinstance(row.schemas_json, dict) else {}
                    tools = cfg.get("tools") or []
                    if not tools:
                        # No tools => not a usable proxy; skip.
                        continue
                    # Always build the canonical https url from APP_BASE_URL —
                    # never trust the persisted value (old rows carry the stale
                    # http://5.161.110.99:3001 host from before wirable.dev).
                    mcp_url = mcp_url_for(str(row.id))

                    # Domain + score: prefer the linked company, fall back to base_url.
                    domain = None
                    score = None
                    cid = row.client_id
                    company = company_by_client.get(cid, "__miss__")
                    if company == "__miss__":
                        company = None
                        try:
                            cres = await db.execute(
                                select(Company)
                                .join(Client, Client.company_id == Company.id)
                                .where(Client.id == cid)
                            )
                            company = cres.scalars().first()
                        except Exception:
                            company = None
                        company_by_client[cid] = company
                    if company is not None:
                        domain = company.domain
                        score = company.score
                    if not domain:
                        base = cfg.get("base_url") or ""
                        if base:
                            try:
                                from urllib.parse import urlparse

                                domain = urlparse(base).netloc or base
                            except Exception:
                                domain = base
                    if score is None and isinstance(row.projected_score, int):
                        score = row.projected_score

                    if not domain or _is_self_or_test_domain(domain):
                        continue
                    # Require a real score — a directory entry with no grade
                    # looks broken to a visitor.
                    if not isinstance(score, int):
                        continue
                    # Dedup by domain; rows are newest-first so first wins.
                    dkey = domain.lower().strip()
                    if dkey in seen_domains:
                        continue
                    seen_domains.add(dkey)

                    out.append(
                        {
                            "domain": domain,
                            "score": score,
                            "mcp_url": mcp_url,
                            "tool_count": len(tools),
                        }
                    )
                except Exception:
                    logger.debug("registry row skipped for %s", getattr(row, "id", "?"))
                    continue
    except Exception:
        logger.exception("list_public_registry failed")
    return out
