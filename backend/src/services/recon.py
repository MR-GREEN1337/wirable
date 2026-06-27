"""
Recon helpers (Wirable) — black-box probing of a target's agent-facing surface.

Pure, dependency-light functions used by the deterministic scorer (catts) and the
proxy generator. Everything here is DEFENSIVE: any network/parse failure is
treated as "absent" and never raises. No DB access.

Two layers:
  1. probe_surface(url)   — single round-trip-ish probe of the canonical markers
     (openapi/swagger, .well-known/mcp.json, llms.txt, /api) plus a homepage GET
     so we can sniff captcha/otp and machine-docs signals.
  2. fetch_openapi(url)   — locate + parse an OpenAPI/Swagger document and return
     the raw spec dict (or None).

`build_evidence(...)` turns a probe bundle into the deterministic per-dimension
{passed, evidence} verdicts (the scoring rubric lives in catts; this just exposes
the raw signals so both the test path and verify path see the SAME facts).
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx
from loguru import logger

_PROBE_TIMEOUT_S = 8.0

# Candidate locations for a machine-readable API description.
OPENAPI_PATHS: tuple[str, ...] = (
    "/openapi.json",
    "/swagger.json",
    "/v1/openapi.json",
    "/api/openapi.json",
    "/api-docs",
    "/swagger/v1/swagger.json",
    "/openapi.yaml",
)

# Markers that indicate an MCP / agent-docs surface already exists.
MCP_PATHS: tuple[str, ...] = (
    "/.well-known/mcp.json",
    "/mcp",
    "/sse",
)
DOCS_PATHS: tuple[str, ...] = (
    "/llms.txt",
    "/.well-known/ai-plugin.json",
)

# Signals (in homepage HTML) that auth is human-gated, not agent-drivable.
_CAPTCHA_SIGNALS = (
    "recaptcha",
    "g-recaptcha",
    "hcaptcha",
    "cf-turnstile",
    "data-sitekey",
    "captcha",
)
_OTP_SIGNALS = (
    "one-time password",
    "verification code",
    "magic link",
    "sign in with email",
    "we sent you a code",
    "enter the code",
)
# Signals that a deterministic agent auth exists (token/key/oauth client-creds).
_TOKEN_SIGNALS = (
    "api key",
    "api-key",
    "bearer token",
    "personal access token",
    "client_credentials",
    "x-api-key",
    "authorization: bearer",
)


def normalize_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    return raw.rstrip("/")


async def _get(client: httpx.AsyncClient, url: str) -> Optional[httpx.Response]:
    try:
        return await client.get(url)
    except Exception as exc:  # network / DNS / timeout -> absent
        logger.debug("recon GET failed for {}: {}", url, exc)
        return None


async def fetch_openapi(base_url: str) -> Optional[dict]:
    """Locate and parse an OpenAPI/Swagger spec. Returns the spec dict or None.

    Tries the common locations; accepts any 2xx JSON body that looks like an
    OpenAPI/Swagger document (has `openapi`/`swagger` + `paths`). YAML specs are
    not parsed (no yaml dep) — they return None and the caller degrades.
    """
    base_url = normalize_url(base_url)
    if not base_url:
        return None
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT_S, follow_redirects=True
    ) as client:
        for path in OPENAPI_PATHS:
            if path.endswith((".yaml", ".yml")):
                continue  # no yaml parser available; skip
            resp = await _get(client, base_url + path)
            if resp is None or resp.status_code >= 400:
                continue
            try:
                spec = resp.json()
            except Exception:
                continue
            if isinstance(spec, dict) and (
                spec.get("openapi") or spec.get("swagger")
            ) and isinstance(spec.get("paths"), dict):
                logger.debug("recon: found OpenAPI at {}{}", base_url, path)
                return spec
    return None


async def probe_surface(base_url: str) -> dict:
    """Probe a target for every agent-facing signal we score against.

    Returns a structured bundle (NEVER raises):
      {
        "base_url": str,
        "kind": "api" | "site",
        "openapi": dict | None,          # parsed spec if discoverable
        "has_openapi": bool,
        "mcp": {path: bool},             # which mcp markers responded 2xx
        "docs": {path: bool},            # which docs markers responded 2xx
        "has_mcp": bool,
        "has_docs": bool,
        "homepage_status": int | None,
        "captcha": bool,                 # human-gated auth signal seen
        "otp": bool,
        "token_auth": bool,              # deterministic agent-auth signal seen
        "found": {marker: bool},         # back-compat flat marker map
      }
    """
    base_url = normalize_url(base_url)
    bundle: dict[str, Any] = {
        "base_url": base_url,
        "kind": "site",
        "openapi": None,
        "has_openapi": False,
        "mcp": {},
        "docs": {},
        "has_mcp": False,
        "has_docs": False,
        "homepage_status": None,
        "captcha": False,
        "otp": False,
        "token_auth": False,
        "found": {},
    }
    if not base_url:
        return bundle

    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT_S, follow_redirects=True
    ) as client:
        # MCP markers.
        for path in MCP_PATHS:
            resp = await _get(client, base_url + path)
            ok = bool(resp is not None and resp.status_code < 400)
            bundle["mcp"][path] = ok
            bundle["found"][path] = ok
        bundle["has_mcp"] = any(bundle["mcp"].values())

        # Docs markers.
        for path in DOCS_PATHS:
            resp = await _get(client, base_url + path)
            ok = bool(resp is not None and resp.status_code < 400)
            bundle["docs"][path] = ok
            bundle["found"][path] = ok
        bundle["has_docs"] = any(bundle["docs"].values())

        # Homepage HTML — sniff captcha / otp / token-auth language + link tags.
        home = await _get(client, base_url)
        if home is not None:
            bundle["homepage_status"] = home.status_code
            html = ""
            try:
                html = (home.text or "")[:200_000].lower()
            except Exception:
                html = ""
            bundle["captcha"] = any(s in html for s in _CAPTCHA_SIGNALS)
            bundle["otp"] = any(s in html for s in _OTP_SIGNALS)
            bundle["token_auth"] = any(s in html for s in _TOKEN_SIGNALS)
            # A <link rel="mcp..."> in the page also counts as an MCP signal.
            if re.search(r'rel=["\']mcp', html):
                bundle["has_mcp"] = True
                bundle["found"]["<link rel=mcp>"] = True

    # OpenAPI (parsed) — strongest api_surface signal.
    spec = await fetch_openapi(base_url)
    if spec:
        bundle["openapi"] = spec
        bundle["has_openapi"] = True
        bundle["found"]["/openapi.json"] = True

    bundle["kind"] = "api" if bundle["has_openapi"] else "site"
    return bundle


# ---------------------------------------------------------------------------
# Deterministic error / idempotency black-box probes
# ---------------------------------------------------------------------------


async def probe_error_quality(base_url: str) -> tuple[bool, str]:
    """Black-box: does a known-bad request return a proper 4xx + structured body?

    We hit a path that almost certainly doesn't exist with a malformed accept and
    check for (a) a 4xx status (NOT 200-with-error) and (b) a JSON body carrying
    a stable error code / message field. Returns (passed, evidence).
    """
    base_url = normalize_url(base_url)
    if not base_url:
        return False, "no base url"
    bad_url = base_url + "/__wirable_nonexistent__/agent-probe"
    async with httpx.AsyncClient(
        timeout=_PROBE_TIMEOUT_S, follow_redirects=True
    ) as client:
        resp = await _get(client, bad_url)
    if resp is None:
        return False, "bad request unreachable"
    status = resp.status_code
    body_json: Any = None
    try:
        body_json = resp.json()
    except Exception:
        body_json = None

    if status == 200:
        # 200-with-error-body is the canonical anti-pattern.
        return False, "known-bad request returned 200 (error hidden in a 200 body)"
    if 400 <= status < 500 and isinstance(body_json, dict):
        keys = {k.lower() for k in body_json.keys()}
        if keys & {"error", "code", "error_code", "errorcode", "message", "detail", "type"}:
            return True, f"known-bad request -> {status} with structured error body ({sorted(keys)[:4]})"
        return False, f"known-bad request -> {status} but unstructured body"
    if 400 <= status < 500:
        return False, f"known-bad request -> {status} but non-JSON body"
    return False, f"known-bad request -> {status} (no machine-readable 4xx)"


def detect_idempotency(openapi: Optional[dict]) -> tuple[bool, str]:
    """Black-box: does the surface advertise an idempotency mechanism?

    Heuristic over the OpenAPI spec: an `Idempotency-Key` header parameter, or
    PUT semantics (idempotent by HTTP contract) on mutating routes. Without a
    spec we cannot prove safe-retry, so we fail closed.
    """
    if not isinstance(openapi, dict):
        return False, "no spec — cannot prove safe-retry / idempotency"
    paths = openapi.get("paths") or {}
    if not isinstance(paths, dict):
        return False, "spec has no paths"
    has_idem_header = False
    has_put = False
    for _path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            ml = str(method).lower()
            if ml == "put":
                has_put = True
            if not isinstance(op, dict):
                continue
            for param in op.get("parameters", []) or []:
                if isinstance(param, dict):
                    pname = str(param.get("name", "")).lower()
                    if "idempotency" in pname or pname == "idempotency-key":
                        has_idem_header = True
    if has_idem_header:
        return True, "explicit Idempotency-Key header parameter in the API"
    if has_put:
        return True, "PUT routes present (idempotent by HTTP contract)"
    return False, "no Idempotency-Key header and no PUT (mutations may duplicate)"
