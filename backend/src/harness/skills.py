#!/usr/bin/env python3
"""
Wirable skill library — the "DO" layer for the dual-modality audit driver.

A SKILL is a named, documented procedure the NAV agent can deliberately invoke.
Each skill is a short spec the model reads (name + when-to-use + concrete steps)
and a callable the driver runs in the sandbox. Skills come in two families:

  BROWSER skills  drive `agent-browser` (the human/black-box modality)
  CODE skills     drive `bash` in the sandbox (the white-box / repo modality)

The catalog (CATALOG_TEXT) is injected into the NAV system prompt so the agent
chooses skills on purpose instead of free-styling clicks. Each skill returns an
evidence dict that the driver folds into the trajectory, and skills may emit a
live frame (via the `shot` callback) so the cockpit shows code/bash steps too.

stdlib only. Every skill is defensive: a failure returns an error evidence dict,
it NEVER raises into the loop.

The driver passes a small `ctx` object (duck-typed) exposing the helpers a skill
needs so this module stays import-light and stdlib-only:

  ctx.ab(*args, timeout=...)       -> agent-browser subprocess helper
  ctx.run_bash(cmd, timeout=...)   -> bash subprocess helper (stdout+stderr)
  ctx.shot(caption, dimension)     -> write a live frame
  ctx.url                          -> target URL
  ctx.origin                       -> target origin
  ctx.login_email / login_password -> WIRABLE_LOGIN_* creds (may be "")
  ctx.api_key / ctx.bearer         -> WIRABLE_API_KEY / WIRABLE_BEARER (may be "")
  ctx.test_email / ctx.test_password -> throwaway signup creds
  ctx.repo / ctx.gh_token          -> WIRABLE_REPO / WIRABLE_GH_TOKEN (may be "")
  ctx.repo_dir                     -> "/tmp/repo" clone target
  ctx.install_api_capture()        -> (re)arm the fetch/XHR interceptor
  ctx.read_captured_calls()        -> drain window.__wirable_calls
  ctx.http_get(url, ...)           -> stdlib HTTP GET (defensive)
  ctx.record_code_endpoint(ep)     -> register an endpoint found in code
  ctx.record_openapi_endpoint(ep)  -> register an endpoint found in a committed spec
"""
from __future__ import annotations

import json
import shlex
import urllib.parse

# ── small, shared helpers ──────────────────────────────────────────────────


def _trunc(s: str, n: int = 1600) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + f"\n…(+{len(s) - n} bytes truncated)"


def _ev(name: str, ok: bool, note: str, dimension: str = "general", **extra) -> dict:
    """Standard evidence record a skill returns; folded into the trajectory."""
    out = {"skill": name, "ok": bool(ok), "note": (note or "")[:600], "dimension": dimension}
    out.update(extra)
    return out


# ─────────────────────────────── BROWSER skills ──────────────────────────────


def skill_complete_signup(ctx, args: dict) -> dict:
    """Create a throwaway account through the human web UI and reach a dashboard."""
    email = (args.get("email") or ctx.test_email)
    pw = (args.get("password") or ctx.test_password)
    ctx.ab("open", ctx.url, timeout=90)
    ctx.install_api_capture()
    ctx.shot("skill: complete_signup", "auth")
    # Best-effort affordance hunt; the NAV loop refines field-level fills after.
    ctx.ab("find", "role", "link", "click", "--name", "Sign up")
    ctx.ab("find", "role", "button", "click", "--name", "Sign up")
    ctx.ab("find", "role", "textbox", "fill", "--name", "Email", email)
    ctx.ab("find", "role", "textbox", "fill", "--name", "Password", pw)
    ctx.ab("find", "role", "button", "click", "--name", "Create account")
    ctx.ab("find", "role", "button", "click", "--name", "Sign up")
    snap = ctx.ab("snapshot", timeout=45)
    ctx.shot("skill: signup submitted", "auth")
    return _ev("complete_signup", True, f"attempted signup as {email}; snapshot head: {_trunc(snap, 300)}", "auth")


def skill_login_with_creds(ctx, args: dict) -> dict:
    """Sign in with the human-provided WIRABLE_LOGIN_* credentials."""
    email = (args.get("email") or ctx.login_email)
    pw = (args.get("password") or ctx.login_password)
    if not (email or pw):
        return _ev("login_with_creds", False, "no WIRABLE_LOGIN_* credentials provided; skip", "auth")
    ctx.ab("open", ctx.url, timeout=90)
    ctx.install_api_capture()
    ctx.shot("skill: login_with_creds", "auth")
    ctx.ab("find", "role", "link", "click", "--name", "Log in")
    ctx.ab("find", "role", "button", "click", "--name", "Log in")
    ctx.ab("find", "role", "textbox", "fill", "--name", "Email", email)
    ctx.ab("find", "role", "textbox", "fill", "--name", "Password", pw)
    ctx.ab("find", "role", "button", "click", "--name", "Sign in")
    ctx.ab("find", "role", "button", "click", "--name", "Log in")
    snap = ctx.ab("snapshot", timeout=45)
    ctx.shot("skill: login submitted", "auth")
    return _ev("login_with_creds", True, f"attempted login as {email}; snapshot head: {_trunc(snap, 300)}", "auth")


def skill_call_endpoint(ctx, args: dict) -> dict:
    """Call a discovered API endpoint from inside the browser (same origin/cookies)
    via agent-browser `eval` + fetch, and read the response shape. Falls back to a
    stdlib HTTP GET (with any provided key/bearer) when the browser eval fails."""
    method = (args.get("method") or "GET").upper()
    path = args.get("path") or args.get("url") or "/"
    target = path if path.startswith("http") else (ctx.origin.rstrip("/") + "/" + path.lstrip("/"))
    body = args.get("body")
    # Try in-page fetch first (carries the authed session cookies).
    headers = {}
    if ctx.bearer:
        headers["Authorization"] = "Bearer " + ctx.bearer
    if ctx.api_key:
        headers["X-API-Key"] = ctx.api_key
    js = (
        "(async function(){try{var r=await fetch(%s,{method:%s,headers:%s%s});"
        "var t=await r.text();return JSON.stringify({status:r.status,ct:r.headers.get('content-type'),"
        "body:t.slice(0,1200)});}catch(e){return JSON.stringify({error:String(e)});}})()"
        % (
            json.dumps(target),
            json.dumps(method),
            json.dumps(headers),
            (",body:" + json.dumps(json.dumps(body))) if body is not None else "",
        )
    )
    raw = ctx.ab("eval", js, timeout=45)
    ctx.shot(f"skill: call {method} {path[:30]}", "api_surface")
    parsed = {}
    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s >= 0 and e >= 0:
            parsed = json.loads(raw[s:e + 1])
    except Exception:  # noqa: BLE001
        parsed = {}
    if parsed and not parsed.get("error"):
        return _ev("call_endpoint", True,
                   f"{method} {target} -> {parsed.get('status')} ct={parsed.get('ct')} body={_trunc(str(parsed.get('body')), 400)}",
                   "api_surface", status=parsed.get("status"), endpoint=f"{method} {target}")
    # Fallback: stdlib GET (no cookies, but proves reachability / auth shape).
    r = ctx.http_get(target, max_bytes=1500, timeout=8)
    return _ev("call_endpoint", True,
               f"{method} {target} (stdlib) -> {r.get('status')} ct={r.get('ct')} body={_trunc(r.get('body',''), 400)}",
               "api_surface", status=r.get("status"), endpoint=f"{method} {target}")


def skill_provoke_error(ctx, args: dict) -> dict:
    """Send a deliberately bad request and inspect the error envelope (shape, code,
    message, machine-readability) — the error_quality dimension's ground truth."""
    path = args.get("path") or "/api/__wirable_nonexistent__"
    method = (args.get("method") or "POST").upper()
    target = path if path.startswith("http") else (ctx.origin.rstrip("/") + "/" + path.lstrip("/"))
    bad_body = args.get("body", {"__wirable_bad__": True, "amount": "not-a-number"})
    js = (
        "(async function(){try{var r=await fetch(%s,{method:%s,headers:{'content-type':'application/json'},"
        "body:%s});var t=await r.text();return JSON.stringify({status:r.status,ct:r.headers.get('content-type'),"
        "body:t.slice(0,1500)});}catch(e){return JSON.stringify({error:String(e)});}})()"
        % (json.dumps(target), json.dumps(method), json.dumps(json.dumps(bad_body)))
    )
    raw = ctx.ab("eval", js, timeout=45)
    ctx.shot("skill: provoke_error", "error_quality")
    parsed = {}
    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s >= 0 and e >= 0:
            parsed = json.loads(raw[s:e + 1])
    except Exception:  # noqa: BLE001
        parsed = {}
    if not parsed or parsed.get("error"):
        r = ctx.http_get(target, max_bytes=1500, timeout=8)
        parsed = {"status": r.get("status"), "ct": r.get("ct"), "body": r.get("body", "")}
    structured = bool("json" in str(parsed.get("ct", "")).lower())
    return _ev("provoke_error", True,
               f"{method} {target} -> {parsed.get('status')} structured={structured} body={_trunc(str(parsed.get('body')), 500)}",
               "error_quality", status=parsed.get("status"), structured=structured)


def skill_test_idempotency(ctx, args: dict) -> dict:
    """Repeat the same write twice and compare responses to detect duplicate
    creation vs idempotent handling (Idempotency-Key honored, 409, or same id)."""
    path = args.get("path") or "/api/echo"
    target = path if path.startswith("http") else (ctx.origin.rstrip("/") + "/" + path.lstrip("/"))
    body = args.get("body", {"__wirable_idem__": "probe"})
    idem_key = "wirable-idem-" + str(abs(hash(json.dumps(body))) % 10_000_000)
    js = (
        "(async function(){var out=[];for(var i=0;i<2;i++){try{var r=await fetch(%s,{method:'POST',"
        "headers:{'content-type':'application/json','Idempotency-Key':%s},body:%s});var t=await r.text();"
        "out.push({status:r.status,body:t.slice(0,500)});}catch(e){out.push({error:String(e)});}}"
        "return JSON.stringify(out);})()"
        % (json.dumps(target), json.dumps(idem_key), json.dumps(json.dumps(body)))
    )
    raw = ctx.ab("eval", js, timeout=60)
    ctx.shot("skill: test_idempotency", "idempotency")
    pair = []
    try:
        s, e = raw.find("["), raw.rfind("]")
        if s >= 0 and e >= 0:
            pair = json.loads(raw[s:e + 1])
    except Exception:  # noqa: BLE001
        pair = []
    same = len(pair) == 2 and pair[0] == pair[1]
    note = f"two writes to {target} with Idempotency-Key={idem_key}: " + json.dumps(pair)[:400]
    return _ev("test_idempotency", True, note, "idempotency",
               identical_responses=same, attempts=len(pair))


def skill_connect_mcp(ctx, args: dict) -> dict:
    """Speak MCP to a declared server: POST tools/list and one safe tools/call.
    Uses in-page fetch (so any session/header applies) with stdlib fallback."""
    endpoint = args.get("endpoint") or args.get("url")
    if not endpoint:
        return _ev("connect_mcp", False, "no MCP endpoint provided; skip", "mcp_availability")
    target = endpoint if endpoint.startswith("http") else (ctx.origin.rstrip("/") + "/" + endpoint.lstrip("/"))
    list_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if ctx.bearer:
        headers["Authorization"] = "Bearer " + ctx.bearer
    js = (
        "(async function(){try{var r=await fetch(%s,{method:'POST',headers:%s,body:%s});"
        "var t=await r.text();return JSON.stringify({status:r.status,body:t.slice(0,1500)});}"
        "catch(e){return JSON.stringify({error:String(e)});}})()"
        % (json.dumps(target), json.dumps(headers), json.dumps(json.dumps(list_req)))
    )
    raw = ctx.ab("eval", js, timeout=45)
    ctx.shot("skill: connect_mcp tools/list", "mcp_availability")
    parsed = {}
    try:
        s, e = raw.find("{"), raw.rfind("}")
        if s >= 0 and e >= 0:
            parsed = json.loads(raw[s:e + 1])
    except Exception:  # noqa: BLE001
        parsed = {}
    body = str(parsed.get("body", "")) if parsed else ""
    tools_seen = '"tools"' in body or '"name"' in body
    return _ev("connect_mcp", bool(parsed and not parsed.get("error")),
               f"tools/list {target} -> {parsed.get('status')} tools_present={tools_seen} body={_trunc(body, 600)}",
               "mcp_availability", tools_present=tools_seen, endpoint=target)


def skill_capture_api_surface(ctx, args: dict) -> dict:
    """Drain the injected fetch/XHR interceptor to read the REAL backend endpoints
    the app has hit so far (the observed-traffic source for distilled endpoints)."""
    ctx.install_api_capture()
    calls = ctx.read_captured_calls()
    ctx.shot(f"skill: capture_api_surface ({len(calls)})", "api_surface")
    sample = "; ".join(f"{c.get('method')} {c.get('url')}" for c in calls[:8])
    return _ev("capture_api_surface", True,
               f"drained {len(calls)} live XHR/fetch endpoints. sample: {sample[:500]}",
               "api_surface", calls=calls)


# ──────────────────────────────── CODE skills ────────────────────────────────


def skill_clone_repo(ctx, args: dict) -> dict:
    """Shallow-clone the bound repo (WIRABLE_REPO) into /tmp/repo using the token."""
    if not (ctx.repo and ctx.gh_token):
        return _ev("clone_repo", False, "no WIRABLE_REPO / WIRABLE_GH_TOKEN bound; skip (live-only)", "general")
    repo = ctx.repo.strip().removeprefix("https://github.com/").removesuffix(".git")
    clone_url = f"https://x-access-token:{ctx.gh_token}@github.com/{repo}.git"
    # rm first so re-runs are clean; never print the token.
    ctx.run_bash(f"rm -rf {shlex.quote(ctx.repo_dir)}", timeout=30)
    out = ctx.run_bash(
        f"git clone --depth 1 {shlex.quote(clone_url)} {shlex.quote(ctx.repo_dir)} 2>&1 | sed 's#{shlex.quote(ctx.gh_token)}#***#g'",
        timeout=180,
    )
    ok = ctx.run_bash(f"test -d {shlex.quote(ctx.repo_dir)}/.git && echo OK || echo NO", timeout=15).strip().endswith("OK")
    ctx.shot(f"skill: clone_repo {repo}", "general")
    return _ev("clone_repo", ok, f"clone {repo}: {_trunc(out, 400)}", "general", repo=repo, cloned=ok)


# Framework -> route-decorator grep patterns. We grep broadly then let the model
# read the hits; this is detection, not a rigid parser.
_ROUTE_PATTERNS = (
    r"@app\.(get|post|put|patch|delete)",          # FastAPI / Flask app
    r"@router\.(get|post|put|patch|delete)",        # FastAPI APIRouter
    r"@(bp|blueprint)\.route",                       # Flask blueprint
    r"app\.(get|post|put|patch|delete)\(",          # Express / Hono / Koa
    r"router\.(get|post|put|patch|delete)\(",       # Express Router
    r"\.(MapGet|MapPost|MapPut|MapDelete)\(",       # ASP.NET minimal API
    r"Route::(get|post|put|patch|delete)",          # Laravel
    r"(get|post|put|patch|delete)\s+'/",            # Sinatra / Rails routes.rb
    r"def (index|show|create|update|destroy)",      # Rails controllers
    r"export (async )?function (GET|POST|PUT|PATCH|DELETE)",  # Next.js route handlers
    r"http\.(HandleFunc|Handle)\(",                 # Go net/http
    r"\.(GET|POST|PUT|PATCH|DELETE)\(\"",           # Gin / Echo (Go)
)


def skill_scan_routes(ctx, args: dict) -> dict:
    """Grep the cloned repo for framework route decorators / handlers and detect
    the framework. Records each plausible endpoint via ctx.record_code_endpoint."""
    if not _repo_present(ctx):
        return _ev("scan_routes", False, "repo not cloned; run clone_repo first", "api_surface")
    framework = _detect_framework(ctx)
    hits_text = []
    found = 0
    for pat in _ROUTE_PATTERNS:
        out = ctx.run_bash(
            "grep -rInE --include='*.py' --include='*.js' --include='*.ts' --include='*.tsx' "
            "--include='*.go' --include='*.rb' --include='*.php' --include='*.cs' "
            f"{shlex.quote(pat)} {shlex.quote(ctx.repo_dir)} 2>/dev/null | head -40",
            timeout=40,
        )
        if out.strip():
            hits_text.append(out)
            for line in out.splitlines():
                ep = _parse_route_line(line)
                if ep:
                    ctx.record_code_endpoint(ep)
                    found += 1
    ctx.shot(f"skill: scan_routes ({found}) {framework}", "api_surface")
    joined = _trunc("\n".join(hits_text), 1800)
    return _ev("scan_routes", True,
               f"framework={framework}; {found} route hits recorded.\n{joined}",
               "api_surface", framework=framework, endpoints_found=found)


def skill_read_auth(ctx, args: dict) -> dict:
    """Find auth middleware / guards / token verification in the source."""
    if not _repo_present(ctx):
        return _ev("read_auth", False, "repo not cloned; run clone_repo first", "auth")
    pat = (
        r"(Depends\(.*(auth|user|token|current_user)|@login_required|@jwt_required|"
        r"requireAuth|isAuthenticated|passport\.|verifyToken|Bearer |Authorization|"
        r"\[Authorize\]|before_action.*authenticate|middleware.*auth|getServerSession|"
        r"API_KEY|x-api-key|api_key)"
    )
    out = ctx.run_bash(
        "grep -rInE --include='*.py' --include='*.js' --include='*.ts' --include='*.tsx' "
        "--include='*.go' --include='*.rb' --include='*.php' --include='*.cs' "
        f"{shlex.quote(pat)} {shlex.quote(ctx.repo_dir)} 2>/dev/null | head -40",
        timeout=40,
    )
    ctx.shot("skill: read_auth", "auth")
    model = _infer_auth_model(out)
    return _ev("read_auth", True, f"auth model guess: {model}\n{_trunc(out, 1400)}", "auth", auth_model=model)


def skill_find_openapi(ctx, args: dict) -> dict:
    """Locate a committed OpenAPI/Swagger spec in the repo and parse its paths."""
    if not _repo_present(ctx):
        return _ev("find_openapi", False, "repo not cloned; run clone_repo first", "docs")
    listing = ctx.run_bash(
        rf"find {shlex.quote(ctx.repo_dir)} -maxdepth 6 -type f \( "
        r"-iname 'openapi.json' -o -iname 'openapi.yaml' -o -iname 'openapi.yml' "
        r"-o -iname 'swagger.json' -o -iname 'swagger.yaml' -o -iname 'api-docs.json' "
        r"\) 2>/dev/null | head -8",
        timeout=30,
    )
    files = [f for f in listing.splitlines() if f.strip()]
    if not files:
        ctx.shot("skill: find_openapi (none)", "docs")
        return _ev("find_openapi", False, "no committed OpenAPI/Swagger spec found", "docs", spec_found=False)
    spec_path = files[0]
    raw = ctx.run_bash(f"head -c 200000 {shlex.quote(spec_path)}", timeout=20)
    paths_found = 0
    if spec_path.endswith(".json"):
        try:
            spec = json.loads(raw)
            for p, ops in (spec.get("paths") or {}).items():
                methods = [m.upper() for m in (ops or {}) if m.lower() in ("get", "post", "put", "patch", "delete")]
                for m in methods:
                    ctx.record_openapi_endpoint({
                        "method": m, "path": p,
                        "summary": str((ops[m.lower()] or {}).get("summary", ""))[:120] if isinstance(ops.get(m.lower()), dict) else "",
                        "auth": None,
                    })
                    paths_found += 1
        except Exception:  # noqa: BLE001
            pass
    ctx.shot(f"skill: find_openapi ({paths_found})", "docs")
    return _ev("find_openapi", True, f"committed spec at {spec_path}; {paths_found} ops registered",
               "docs", spec_found=True, spec_path=spec_path, ops=paths_found)


def skill_map_code_to_live(ctx, args: dict) -> dict:
    """Cross-check a code-discovered endpoint against live behavior by calling it,
    so we know which source routes are actually reachable in production."""
    eps = args.get("endpoints") or ctx.code_endpoints_sample()
    checked = []
    for ep in (eps or [])[:5]:
        path = ep.get("path") if isinstance(ep, dict) else str(ep)
        if not path or "{" in str(path) or "<" in str(path) or ":" in str(path).split("/")[-1]:
            continue  # skip parameterized paths we can't fill safely
        r = skill_call_endpoint(ctx, {"method": "GET", "path": path})
        checked.append({"path": path, "result": r.get("note", "")[:160]})
    ctx.shot(f"skill: map_code_to_live ({len(checked)})", "api_surface")
    return _ev("map_code_to_live", True,
               "cross-checked code routes against live: " + json.dumps(checked)[:800],
               "api_surface", checked=checked)


# ── code-skill internals ────────────────────────────────────────────────────


def _repo_present(ctx) -> bool:
    out = ctx.run_bash(f"test -d {shlex.quote(ctx.repo_dir)} && echo Y || echo N", timeout=10)
    return out.strip().endswith("Y")


def _detect_framework(ctx) -> str:
    checks = [
        ("fastapi", "grep -rIl 'fastapi' --include='*.py' {d} 2>/dev/null | head -1"),
        ("flask", "grep -rIl 'from flask' --include='*.py' {d} 2>/dev/null | head -1"),
        ("django", "find {d} -name 'manage.py' 2>/dev/null | head -1"),
        ("express", "grep -rIl \"require('express')\\|from 'express'\" {d} 2>/dev/null | head -1"),
        ("nextjs", "find {d} -path '*app/api*route.ts' -o -path '*pages/api*' 2>/dev/null | head -1"),
        ("hono", "grep -rIl 'from .hono.' {d} 2>/dev/null | head -1"),
        ("nestjs", "grep -rIl '@nestjs' {d} 2>/dev/null | head -1"),
        ("go-net-http", "grep -rIl 'net/http' --include='*.go' {d} 2>/dev/null | head -1"),
        ("gin", "grep -rIl 'gin-gonic' --include='*.go' {d} 2>/dev/null | head -1"),
        ("rails", "find {d} -name 'routes.rb' 2>/dev/null | head -1"),
        ("laravel", "find {d} -path '*routes/api.php' 2>/dev/null | head -1"),
        ("aspnet", "grep -rIl 'Microsoft.AspNetCore' {d} 2>/dev/null | head -1"),
    ]
    for name, tmpl in checks:
        out = ctx.run_bash(tmpl.format(d=shlex.quote(ctx.repo_dir)), timeout=20)
        if out.strip():
            return name
    return "unknown"


def _infer_auth_model(grep_out: str) -> str:
    low = (grep_out or "").lower()
    bits = []
    if "bearer" in low or "jwt" in low or "jwt_required" in low:
        bits.append("bearer/JWT")
    if "api_key" in low or "x-api-key" in low or "api-key" in low:
        bits.append("api-key")
    if "getserversession" in low or "passport" in low or "login_required" in low or "current_user" in low:
        bits.append("session")
    if "[authorize]" in low or "isauthenticated" in low or "requireauth" in low or "before_action" in low:
        bits.append("guard-middleware")
    return ", ".join(dict.fromkeys(bits)) or "none/unknown"


def _parse_route_line(line: str) -> dict | None:
    """Best-effort extraction of {method, path} from a grep route hit. Returns a
    dict tagged source=code, or None when no path literal is recoverable."""
    try:
        # strip "file:lineno:" prefix from grep -In output
        parts = line.split(":", 2)
        code = parts[2] if len(parts) >= 3 else line
    except Exception:  # noqa: BLE001
        code = line
    low = code.lower()
    method = "ANY"
    for m in ("get", "post", "put", "patch", "delete"):
        if m in low:
            method = m.upper()
            break
    # pull the first quoted "/..." path literal
    path = ""
    for q in ('"', "'", "`"):
        i = code.find(q + "/")
        if i >= 0:
            j = code.find(q, i + 1)
            if j > i:
                path = code[i + 1:j]
                break
    if not path:
        return None
    return {"method": method, "path": path[:200], "summary": "", "auth": None, "source": "code"}


# ──────────────────────────── registry + catalog ─────────────────────────────

SKILLS = {
    # browser
    "complete_signup": skill_complete_signup,
    "login_with_creds": skill_login_with_creds,
    "call_endpoint": skill_call_endpoint,
    "provoke_error": skill_provoke_error,
    "test_idempotency": skill_test_idempotency,
    "connect_mcp": skill_connect_mcp,
    "capture_api_surface": skill_capture_api_surface,
    # code
    "clone_repo": skill_clone_repo,
    "scan_routes": skill_scan_routes,
    "read_auth": skill_read_auth,
    "find_openapi": skill_find_openapi,
    "map_code_to_live": skill_map_code_to_live,
}

# When-to-use specs injected into the NAV system prompt so the model picks
# skills deliberately. Kept terse; the model also has the raw bash/click actions.
_SPECS = [
    ("complete_signup", "browser", "no creds + no machine path: make a throwaway account and reach a dashboard. args: {email?,password?}"),
    ("login_with_creds", "browser", "WIRABLE_LOGIN_* creds exist: sign into the real product. args: {email?,password?}"),
    ("call_endpoint", "browser", "hit a discovered API endpoint (in-page fetch w/ session) and read the response. args: {method,path,body?}"),
    ("provoke_error", "browser", "send a bad request to inspect the error envelope (error_quality). args: {path?,method?,body?}"),
    ("test_idempotency", "browser", "POST the same write twice w/ Idempotency-Key, compare (idempotency). args: {path,body?}"),
    ("connect_mcp", "browser", "speak MCP to a declared server: tools/list + safe tools/call. args: {endpoint}"),
    ("capture_api_surface", "browser", "drain the live fetch/XHR interceptor to read REAL endpoints the app hit. args: {}"),
    ("clone_repo", "code/bash", "WIRABLE_REPO+token bound: shallow-clone source into /tmp/repo. RUN FIRST for white-box. args: {}"),
    ("scan_routes", "code/bash", "grep the repo for route decorators/handlers; detect framework + record code endpoints. args: {}"),
    ("read_auth", "code/bash", "find auth middleware/guards/token checks in source (auth model). args: {}"),
    ("find_openapi", "code/bash", "locate + parse a committed OpenAPI/Swagger spec in the repo. args: {}"),
    ("map_code_to_live", "code/bash", "cross-check code-found routes against live behavior by calling them. args: {endpoints?}"),
]


def catalog_text(has_repo: bool, has_creds: bool, machine_first: bool) -> str:
    """Human-readable skill catalog for the NAV system prompt, tagged with which
    skills are RELEVANT given the bound env so the agent prioritizes correctly."""
    lines = ["SKILL CATALOG (invoke via action 'skill' with fields 'skill' and 'args'):"]
    for name, fam, when in _SPECS:
        tag = ""
        if fam.startswith("code") and not has_repo:
            tag = "  [unavailable: no repo bound]"
        elif name == "login_with_creds" and not has_creds:
            tag = "  [creds not provided]"
        elif name in ("clone_repo", "scan_routes", "read_auth", "find_openapi", "map_code_to_live") and has_repo:
            tag = "  [WHITE-BOX: prefer early]"
        lines.append(f"  - {name} ({fam}): {when}{tag}")
    if has_repo:
        lines.append("STRATEGY: a repo IS bound. Begin white-box: clone_repo -> scan_routes/read_auth/find_openapi, "
                     "THEN cross-check with live (call_endpoint/map_code_to_live).")
    if machine_first:
        lines.append("STRATEGY: a machine surface exists; verify it programmatically (call_endpoint/connect_mcp) "
                     "instead of fighting the human web form.")
    return "\n".join(lines)


def run_skill(ctx, name: str, args: dict) -> dict:
    """Dispatch a skill by name. Never raises — unknown/failed skills degrade."""
    fn = SKILLS.get((name or "").strip())
    if not fn:
        return _ev(name or "?", False, f"unknown skill '{name}'", "general")
    try:
        return fn(ctx, args or {})
    except Exception as e:  # noqa: BLE001
        return _ev(name, False, f"skill error: {e}", "general")
