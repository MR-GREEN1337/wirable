#!/usr/bin/env python3
"""
Wirable SOTA audit driver — runs INSIDE the sandbox.

Wirable's thesis: an AGENT should USE the MACHINE interface (llms.txt → docs →
API/MCP) when one exists, and only fall back to the human UI otherwise. So this
driver models that order of operations:

  Phase 0  Machine-surface DISCOVERY first — probe llms.txt / openapi.json /
           .well-known/mcp.json / ai-plugin / api / docs / robots / sitemap.
  Phase 1  Actually READ the machine surface — fetch llms.txt full text and
           follow a few of its links; parse openapi.json; note declared MCP /
           plugin servers. This is the agent INGESTING the machine-readable
           guidance — it is evidence, not just a status code.
  Phase 2  Goal attempt via the BEST available path. Decide whether a viable
           MACHINE path exists (from llms.txt + openapi). STILL drive
           `agent-browser` for the human path, but make it smart + bounded:
           CAPTCHA/OTP/bot-wall detector, loop-guard, a low step cap, and a
           "blocked" action so the agent NEVER brute-forces a wall (the old
           failure mode on stripe.com: 32 steps fighting hCaptcha).
  Phase 3  Verdict grounded in BOTH paths — rewarding a usable machine path,
           treating a CAPTCHA/OTP human wall as a blocker that only matters if
           there is NO machine path.

stdlib only (urllib/json/subprocess) — runs in the sandbox, no pip deps.

argv: <url> [mission]   mission = "deep" (agentic loop, default) | "fast" (probe-only)

Writes:
  /tmp/screenshots/NNNN.jpg + NNNN.json   (live frames the backend streams)
  /tmp/output.json                        ({domain, dimensions, cards, summary, frames})
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import urllib.parse
import urllib.request

# The skill library (the "DO" layer). Imported defensively so a missing/broken
# skills.py degrades to the live-only freeform loop instead of crashing the run.
try:
    import skills as _skills  # uploaded alongside this driver into /tmp
except Exception:  # noqa: BLE001
    try:
        from . import skills as _skills  # package import (local dev/tests)
    except Exception:  # noqa: BLE001
        _skills = None

URL = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
MISSION = (sys.argv[2] if len(sys.argv) > 2 else "deep").strip()
SHOT_DIR = "/tmp/screenshots"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# Machine discovery now carries the verdict weight, so the human path is short.
MAX_STEPS = int(os.environ.get("WIRABLE_MAX_STEPS", "12"))

os.makedirs(SHOT_DIR, exist_ok=True)
_frame = 0
TEST_EMAIL = f"wirable.audit+{random.randint(10000, 99999)}@gmail.com"
TEST_PASSWORD = "WirableAudit!" + str(random.randint(1000, 9999))

# ── Human-in-the-loop access (pre-run creds the user optionally handed us) ──
# These are injected into the sandbox env by test_service when the POST /run
# `access` grant is present. When set, the agent LOGS IN with them and exercises
# the AUTHED product instead of signing up a throwaway account.
LOGIN_EMAIL = os.environ.get("WIRABLE_LOGIN_EMAIL", "").strip()
LOGIN_PASSWORD = os.environ.get("WIRABLE_LOGIN_PASSWORD", "").strip()
ACCESS_API_KEY = os.environ.get("WIRABLE_API_KEY", "").strip()
ACCESS_BEARER = os.environ.get("WIRABLE_BEARER", "").strip()
ACCESS_NOTES = os.environ.get("WIRABLE_ACCESS_NOTES", "").strip()
HAS_CREDS = bool(LOGIN_EMAIL or LOGIN_PASSWORD or ACCESS_API_KEY or ACCESS_BEARER)

# ── White-box (code) modality: a bound source repo ──────────────────────────
# When BOTH are set, the agent clones the repo into REPO_DIR and analyzes the
# SOURCE (routes / auth / committed OpenAPI) alongside the live browser pass.
# When unset, the run is live-only (black-box), exactly as before.
WIRABLE_REPO = os.environ.get("WIRABLE_REPO", "").strip()
WIRABLE_GH_TOKEN = os.environ.get("WIRABLE_GH_TOKEN", "").strip()
REPO_DIR = os.environ.get("WIRABLE_REPO_DIR", "/tmp/repo").strip() or "/tmp/repo"
HAS_REPO = bool(WIRABLE_REPO and WIRABLE_GH_TOKEN)

# Endpoint registries populated by skills (white-box) — merged into the
# distilled endpoint set at the end. Live-traffic endpoints come from api_calls.
CODE_ENDPOINTS: list[dict] = []      # {method, path, summary, auth, source:"code"}
OPENAPI_ENDPOINTS: list[dict] = []   # {method, path, summary, auth, source:"openapi"}
_code_ep_seen: set[str] = set()
CODE_FRAMEWORK = {"name": "unknown"}  # mutable holder; skills set the framework


def record_code_endpoint(ep: dict) -> None:
    """Register an endpoint discovered in source code (deduped). Defensive."""
    try:
        method = str(ep.get("method", "ANY")).upper()
        path = str(ep.get("path", "")).strip()
        if not path:
            return
        key = f"{method} {path}"
        if key in _code_ep_seen:
            return
        _code_ep_seen.add(key)
        CODE_ENDPOINTS.append({
            "method": method, "path": path,
            "summary": str(ep.get("summary", "") or "")[:120],
            "auth": ep.get("auth"), "source": "code",
        })
    except Exception:  # noqa: BLE001
        pass


def record_openapi_endpoint(ep: dict) -> None:
    """Register an endpoint from a committed OpenAPI spec (deduped). Defensive."""
    try:
        method = str(ep.get("method", "ANY")).upper()
        path = str(ep.get("path", "")).strip()
        if not path:
            return
        key = f"openapi {method} {path}"
        if key in _code_ep_seen:
            return
        _code_ep_seen.add(key)
        OPENAPI_ENDPOINTS.append({
            "method": method, "path": path,
            "summary": str(ep.get("summary", "") or "")[:120],
            "auth": ep.get("auth"), "source": "openapi",
        })
    except Exception:  # noqa: BLE001
        pass

# Human-in-the-loop file contract (the camera loop in test_service bridges these
# to/from the SSE bus + POST /run/{id}/input):
#   /tmp/need_input.json   <- driver writes {prompt, kind, request_id} to ASK
#   /tmp/human_input.json  -> driver reads {value} when the human ANSWERS
NEED_INPUT_PATH = "/tmp/need_input.json"
HUMAN_INPUT_PATH = "/tmp/human_input.json"
_input_seq = 0

# Words that signal a wall an agent cannot legitimately pass.
HARD_WALLS = (
    "captcha", "hcaptcha", "recaptcha", "turnstile", "challenge",
    "verify you are human", "are you a robot", "i'm not a robot",
    "verification code", "one-time code", "one time passcode", "otp",
    "check your email", "we sent you a code", "enter the code",
    "confirm your email", "two-factor", "2fa", "cloudflare",
)

# A subset of walls a HUMAN can pass for the agent (OTP / 2FA / email codes).
# When the detector trips on one of these AND a human is in the loop, we ASK the
# human for the value instead of recording a dead-end blocker. True dead-ends
# (CAPTCHA / bot-check / cloudflare) stay hard blockers no human can resolve here.
HUMAN_RESOLVABLE_WALLS = (
    "verification code", "one-time code", "one time passcode", "otp",
    "check your email", "we sent you a code", "enter the code",
    "confirm your email", "two-factor", "2fa",
)


def origin(u: str) -> str:
    try:
        p = urllib.parse.urlsplit(u)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:  # noqa: BLE001
        pass
    return u.rstrip("/")


ORIGIN = origin(URL)


def _host(u: str) -> str:
    try:
        return urllib.parse.urlsplit(u if "://" in u else "https://" + u).netloc.split("@")[-1].split(":")[0]
    except Exception:  # noqa: BLE001
        return u


# Two-label public suffixes we should NOT collapse to the last 2 labels.
_TWO_LABEL_TLDS = (
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "co.kr", "com.au", "net.au",
    "com.br", "co.in", "co.nz", "com.sg", "com.mx", "co.za",
)


def registrable_domain(host: str) -> str:
    """Best-effort registrable domain from a hostname (stdlib only, no PSL).

    kortix.com           -> kortix.com
    api.kortix.com       -> kortix.com
    foo.bar.example.co.uk-> example.co.uk
    """
    host = (host or "").strip().strip(".").lower()
    if not host or _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last2 = ".".join(labels[-2:])
    if last2 in _TWO_LABEL_TLDS and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last2


def _is_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() for p in parts)


# Subdomains that commonly host the machine/API/doc surface. Probed in priority
# order; the API-ish ones come first so the request budget favors them.
API_SUBDOMAINS = (
    "api", "api-test", "apis", "sandbox", "developer", "developers",
    "rest", "graphql", "gateway", "public-api", "docs", "doc", "app",
)

# Paths probed on the apex AND each reachable subdomain. OpenAPI/MCP first so the
# richest machine entrypoint is found before the budget is spent.
SURFACE_PATHS = (
    "/openapi.json", "/swagger.json", "/openapi.yaml",
    "/.well-known/openapi.json", "/.well-known/mcp.json",
    "/.well-known/ai-plugin.json",
    "/llms.txt", "/llms-full.txt",
    "/api", "/api/v1", "/v1", "/graphql",
    "/docs", "/redoc", "/swagger",
    "/robots.txt", "/sitemap.xml",
)

# Hard ceiling on HTTP requests for the whole discovery sweep so a run stays
# under a couple of minutes even on a host with many live subdomains.
_DISCOVERY_BUDGET = int(os.environ.get("WIRABLE_DISCOVERY_BUDGET", "44"))
_REQ_TIMEOUT = 4
_request_count = 0


def _budget_left() -> bool:
    return _request_count < _DISCOVERY_BUDGET


def ab(*args: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(["agent-browser", *args], capture_output=True, text=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"__err__ {e}"


def ab_open(url: str, timeout: int = 90) -> str:
    """Open a URL and SETTLE before the caller snapshots/screenshots.

    Plain `open` can return before the page paints — heavy SPA landings then get
    a blank first frame (and the agent sees nothing and bails early). We wait for
    document.readyState=complete (capped) plus a short paint settle so the very
    first frame is the real rendered page. eval awaits promises here.
    """
    out = ab("open", url, timeout=timeout)
    # Wait for load (hard-capped at 6s so a never-idle page can't hang the step).
    ab("eval",
       "new Promise(r=>{if(document.readyState==='complete')return r();"
       "addEventListener('load',()=>r());setTimeout(r,6000)})",
       timeout=12)
    # Paint/layout settle for client-rendered content.
    ab("eval", "new Promise(r=>setTimeout(r,900))", timeout=4)
    return out


# Cap on bash output folded back to the model so a noisy command can't blow the
# context budget. Stdout+stderr are merged; the tail is dropped past the cap.
_BASH_MAX_OUT = int(os.environ.get("WIRABLE_BASH_MAX_OUT", "6000"))


def run_bash(cmd: str, timeout: int = 60) -> str:
    """Run a shell command in the sandbox (code/ops modality). Defensive + bounded.

    Captures stdout+stderr merged, truncates to _BASH_MAX_OUT, never raises. This
    is the agent's white-box hand: grep routes, cat files, ls, run repo tooling.
    """
    try:
        r = subprocess.run(
            ["bash", "-lc", cmd], capture_output=True, text=True,
            timeout=max(1, min(timeout, 300)),
        )
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        out = out.strip()
        if len(out) > _BASH_MAX_OUT:
            out = out[:_BASH_MAX_OUT] + f"\n…(+{len(out) - _BASH_MAX_OUT} bytes truncated)"
        return out or f"(exit {r.returncode}, no output)"
    except subprocess.TimeoutExpired:
        return f"__err__ bash timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return f"__err__ {e}"


def shot(caption: str, dimension: str = "general") -> None:
    global _frame
    _frame += 1
    stem = f"{SHOT_DIR}/{_frame:04d}"
    ab("screenshot", f"{stem}.jpg", timeout=30)
    try:
        json.dump({"caption": caption[:120], "dimension": dimension, "url": URL}, open(f"{stem}.json", "w"))
    except Exception:  # noqa: BLE001
        pass


def request_human(prompt: str, kind: str = "text", timeout_s: int = 150) -> str | None:
    """Ask the human for a value mid-run and block until it arrives (or timeout).

    Writes /tmp/need_input.json = {prompt, kind, request_id}; the backend camera
    loop picks that up and emits a `needs_input` SSE event. The human answers via
    POST /run/{id}/input, which the camera loop relays into /tmp/human_input.json.
    We poll that file every ~3s up to timeout_s. On arrival: read .value, delete
    BOTH files (so the next request starts clean), return the value. On timeout:
    clean up need_input.json and return None (caller records blocked + moves on).
    """
    global _input_seq
    _input_seq += 1
    request_id = f"{os.getpid()}-{_input_seq}"
    # Clear any stale answer before we ask.
    try:
        if os.path.exists(HUMAN_INPUT_PATH):
            os.remove(HUMAN_INPUT_PATH)
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(NEED_INPUT_PATH, "w") as f:
            json.dump({"prompt": prompt[:300], "kind": kind, "request_id": request_id}, f)
    except Exception:  # noqa: BLE001
        return None

    waited = 0
    while waited < timeout_s:
        try:
            if os.path.exists(HUMAN_INPUT_PATH):
                with open(HUMAN_INPUT_PATH) as f:
                    data = json.load(f)
                value = str(data.get("value", "") or "")
                for p in (NEED_INPUT_PATH, HUMAN_INPUT_PATH):
                    try:
                        os.remove(p)
                    except Exception:  # noqa: BLE001
                        pass
                return value
        except Exception:  # noqa: BLE001
            pass
        import time
        time.sleep(3)
        waited += 3

    # Timed out — withdraw the request so it isn't re-surfaced.
    try:
        os.remove(NEED_INPUT_PATH)
    except Exception:  # noqa: BLE001
        pass
    return None


def http_get(u: str, max_bytes: int = 60000, timeout: int = 10) -> dict:
    """Defensive HTTP GET (follows redirects via urllib default). Never raises.

    Counts toward the discovery request budget so a sweep stays bounded.
    """
    global _request_count
    _request_count += 1
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "wirable-agent/1.0"})
        r = urllib.request.urlopen(req, timeout=timeout)
        raw = r.read(max_bytes)
        return {
            "url": getattr(r, "url", u),
            "status": getattr(r, "status", 200),
            "ct": r.headers.get("content-type", ""),
            "body": raw.decode("utf-8", "replace"),
        }
    except Exception as e:  # noqa: BLE001
        return {"url": u, "status": getattr(e, "code", "ERR"), "ct": "", "body": str(e)[:200]}


def probe(path: str, base: str = ORIGIN) -> dict:
    """Quick status + short snippet for the discovery sweep, on any host."""
    full = base.rstrip("/") + path
    r = http_get(full, max_bytes=2000, timeout=_REQ_TIMEOUT)
    return {
        "url": r["url"],
        "status": r["status"],
        "ct": r["ct"],
        "snippet": (r["body"] or "")[:350],
    }


def host_reachable(base: str) -> bool:
    """One cheap request to a host root. Lets us skip a dead subdomain fast."""
    r = http_get(base.rstrip("/") + "/", max_bytes=400, timeout=_REQ_TIMEOUT)
    try:
        return r.get("status") != "ERR"
    except Exception:  # noqa: BLE001
        return False


def is_ok(p: dict) -> bool:
    try:
        return int(p.get("status", 0)) == 200
    except Exception:  # noqa: BLE001
        return False


def claude(system: str, prompt: str, max_tokens: int = 1200, image_b64: str = "") -> str:
    if not ANTHROPIC_KEY:
        return ""
    # Vision grounding: when a screenshot is provided, send it as an image block so
    # the model decides on what it SEES (catches visual/canvas/custom widgets the
    # accessibility tree misses) — the a11y @refs are the "set of marks".
    if image_b64:
        content: object = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": prompt},
        ]
    else:
        content = prompt
    body = json.dumps({"model": MODEL, "max_tokens": max_tokens, "temperature": 0.2,
                       "system": system, "messages": [{"role": "user", "content": content}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body,
                                 headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=90))
        return "".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict))
    except Exception:  # noqa: BLE001
        return ""


def claude_json(system: str, prompt: str, max_tokens: int = 2200, image_b64: str = "") -> dict:
    text = claude(system, prompt, max_tokens, image_b64=image_b64)
    try:
        return json.loads(text[text.find("{"): text.rfind("}") + 1])
    except Exception:  # noqa: BLE001
        return {}


def screen_b64() -> str:
    """Capture the current page as a base64 JPEG for vision grounding. Best-effort,
    size-capped so a huge frame never blows the request."""
    try:
        import base64 as _b64
        p = "/tmp/_vision.jpg"
        ab("screenshot", p, timeout=20)
        if os.path.exists(p):
            data = open(p, "rb").read()
            if data and len(data) < 1_400_000:
                return _b64.b64encode(data).decode()
    except Exception:  # noqa: BLE001
        pass
    return ""


DIMS = ["api_surface", "auth", "error_quality", "idempotency", "mcp_availability", "docs"]


# ───────────────────────── Phase 0 + 1: machine surface ──────────────────────

def extract_links(text: str, base: str, limit: int = 12) -> list[str]:
    """Pull URLs / markdown links out of an llms.txt-style document."""
    out: list[str] = []
    seen: set[str] = set()
    base = (base or ORIGIN).rstrip("/")
    for tok in text.replace("(", " ").replace(")", " ").replace("<", " ").replace(">", " ").split():
        t = tok.strip().strip(".,;\"'`")
        if t.startswith("http://") or t.startswith("https://"):
            cand = t
        elif t.startswith("/") and len(t) > 1:
            cand = base + t
        else:
            continue
        if cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def summarize_openapi(body: str) -> dict:
    try:
        spec = json.loads(body)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(spec, dict):
        return {}
    info = spec.get("info") or {}
    paths = spec.get("paths") or {}
    comps = (spec.get("components") or {}).get("securitySchemes") or {}
    sec_schemes = []
    for name, sch in (comps.items() if isinstance(comps, dict) else []):
        if isinstance(sch, dict):
            sec_schemes.append(f"{name}:{sch.get('type', '?')}/{sch.get('scheme', sch.get('in', ''))}".rstrip("/"))
    examples = []
    for p, ops in (paths.items() if isinstance(paths, dict) else []):
        methods = [m.upper() for m in (ops.keys() if isinstance(ops, dict) else []) if m.lower() in ("get", "post", "put", "patch", "delete")]
        examples.append(f"{','.join(methods) or '?'} {p}")
        if len(examples) >= 6:
            break
    return {
        "title": (info.get("title") or "")[:120],
        "version": str(info.get("version") or "")[:30],
        "num_paths": len(paths) if isinstance(paths, dict) else 0,
        "auth_schemes": sec_schemes[:6] or ["(none declared)"],
        "example_endpoints": examples,
    }


def summarize_well_known(body: str) -> dict:
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    # ai-plugin.json
    if data.get("name_for_model") or data.get("api"):
        api = data.get("api") or {}
        out["plugin_name"] = (data.get("name_for_model") or data.get("name_for_human") or "")[:80]
        out["plugin_api"] = (api.get("url") if isinstance(api, dict) else str(api))
        out["plugin_auth"] = (data.get("auth") or {}).get("type") if isinstance(data.get("auth"), dict) else None
    # mcp.json (loose — capture server url + capabilities if present)
    for k in ("mcpServers", "servers", "server", "url", "endpoint", "capabilities", "tools"):
        if k in data:
            out[f"mcp_{k}"] = data[k] if not isinstance(data[k], (dict, list)) else json.dumps(data[k])[:300]
    return out


def _looks_openapi(body: str) -> bool:
    try:
        spec = json.loads(body)
    except Exception:  # noqa: BLE001
        return False
    return isinstance(spec, dict) and bool(spec.get("openapi") or spec.get("swagger")) and isinstance(spec.get("paths"), dict)


def _candidate_hosts() -> list[str]:
    """Apex + API/doc subdomains of the registrable domain, in probe priority.

    e.g. kortix.com -> [https://kortix.com, https://www.kortix.com,
                        https://api.kortix.com, https://api-test.kortix.com, ...]
    Deduped; the apex/origin host is always first.
    """
    reg = registrable_domain(_host(URL))
    hosts: list[str] = [ORIGIN]
    seen = {_host(ORIGIN)}
    if not reg or _is_ip(reg):
        return hosts
    # www on the apex (only if origin wasn't already www.*)
    for cand in [f"www.{reg}"] + [f"{sd}.{reg}" for sd in API_SUBDOMAINS] + [reg]:
        if cand in seen:
            continue
        seen.add(cand)
        hosts.append("https://" + cand)
    return hosts


def discover_machine_surface() -> dict:
    """Phase 0 (probe) + Phase 1 (read) — SCOUR the whole reachable machine surface.

    Enumerates API/doc subdomains of the registrable domain, probes a matrix of
    machine-readable paths on each reachable host, and identifies the BEST
    machine entrypoint (richest OpenAPI URL anywhere — apex OR subdomain), any
    MCP/plugin manifest, and llms.txt. Bounded by a hard request budget so a run
    stays under a couple of minutes.
    """
    reg = registrable_domain(_host(URL))
    hosts = _candidate_hosts()

    surfaces: list[dict] = []   # every reachable surface found (full url/status/ct/snippet)
    probes_by_host: dict[str, dict] = {}
    hosts_probed: list[str] = []
    hosts_reachable: list[str] = []

    best_openapi: dict = {}     # {url, summary, num_paths}
    mcp_manifest: dict = {}
    plugin_manifest: dict = {}
    llms_hit: dict = {}

    for base in hosts:
        if not _budget_left():
            break
        is_apex = _host(base) == _host(ORIGIN)
        # The origin is known reachable (the agent will browse it); for derived
        # subdomains, spend ONE request to confirm the host resolves, else skip
        # the whole path matrix for that host.
        if not is_apex:
            if not _budget_left():
                break
            if not host_reachable(base):
                continue
        hosts_probed.append(base)

        host_probes: dict = {}
        host_reachable_flag = is_apex
        for path in SURFACE_PATHS:
            if not _budget_left():
                break
            r = probe(path, base)
            host_probes[path] = r
            if is_ok(r) or (isinstance(r.get("status"), int) and r["status"] < 400):
                host_reachable_flag = True
                surfaces.append({
                    "url": r["url"], "status": r["status"], "ct": r["ct"],
                    "snippet": r.get("snippet", "")[:200], "host": _host(base),
                })
            # Identify + parse a real OpenAPI as soon as we see one.
            if is_ok(r) and path in ("/openapi.json", "/swagger.json", "/.well-known/openapi.json"):
                if not _budget_left():
                    continue
                full = http_get(base.rstrip("/") + path, max_bytes=250000, timeout=6)
                body = full.get("body", "")
                if _looks_openapi(body):
                    summ = summarize_openapi(body)
                    num = summ.get("num_paths", 0)
                    if num > best_openapi.get("num_paths", -1):
                        best_openapi = {"url": full.get("url", base + path), "host": _host(base),
                                        "summary": summ, "num_paths": num}
            # MCP / plugin manifests.
            if is_ok(r) and path == "/.well-known/mcp.json" and not mcp_manifest:
                full = http_get(base.rstrip("/") + path, max_bytes=20000, timeout=5)
                mm = summarize_well_known(full.get("body", ""))
                if mm:
                    mcp_manifest = {"url": full.get("url"), "host": _host(base), **mm}
            if is_ok(r) and path == "/.well-known/ai-plugin.json" and not plugin_manifest:
                full = http_get(base.rstrip("/") + path, max_bytes=20000, timeout=5)
                pm = summarize_well_known(full.get("body", ""))
                if pm:
                    plugin_manifest = {"url": full.get("url"), "host": _host(base), **pm}
            # llms.txt — keep the first/richest (prefer llms-full.txt).
            if is_ok(r) and path in ("/llms.txt", "/llms-full.txt"):
                if (not llms_hit) or path == "/llms-full.txt":
                    llms_hit = {"url": r["url"], "host": _host(base), "path": path, "base": base}

        probes_by_host[_host(base)] = host_probes
        if host_reachable_flag:
            hosts_reachable.append(_host(base))

    surface: dict = {
        "origin": ORIGIN,
        "registrable_domain": reg,
        "hosts_probed": hosts_probed,
        "hosts_reachable": hosts_reachable,
        "requests_used": _request_count,
        "request_budget": _DISCOVERY_BUDGET,
        "surfaces": surfaces[:60],
        "probes_by_host": {h: {p: v.get("status") for p, v in pr.items()} for h, pr in probes_by_host.items()},
    }

    # ── llms.txt — read full text + follow up to 3 of its links ──
    if llms_hit:
        doc = http_get(llms_hit["url"], max_bytes=60000, timeout=6)
        text = doc.get("body", "")
        links = extract_links(text, llms_hit.get("base", ORIGIN), limit=8)
        followed = []
        for link in links[:3]:
            if not _budget_left():
                break
            sub = http_get(link, max_bytes=4000, timeout=_REQ_TIMEOUT)
            followed.append({"url": link, "status": sub.get("status"),
                             "snippet": (sub.get("body") or "")[:400]})
        surface["llms_txt"] = {
            "url": llms_hit["url"], "host": llms_hit["host"], "path": llms_hit["path"],
            "length": len(text), "text": text[:6000],
            "links_found": links, "followed": followed,
        }

    # ── best OpenAPI anywhere (apex or subdomain) ──
    if best_openapi:
        surface["openapi"] = best_openapi["summary"]
        surface["openapi_url"] = best_openapi["url"]
        surface["openapi_host"] = best_openapi["host"]

    if mcp_manifest:
        surface["mcp"] = mcp_manifest
    if plugin_manifest:
        surface["ai_plugin"] = plugin_manifest

    # ── BEST machine entrypoint the proxy should target ──
    entry = ""
    entry_kind = ""
    if best_openapi:
        entry, entry_kind = best_openapi["url"], "openapi"
    elif mcp_manifest.get("url"):
        entry, entry_kind = mcp_manifest["url"], "mcp"
    elif llms_hit:
        entry, entry_kind = llms_hit["url"], "llms_txt"
    surface["best_entrypoint"] = entry
    surface["best_entrypoint_kind"] = entry_kind
    # The host the real API lives on (drives the proxy's upstream base URL).
    api_host = (best_openapi.get("host") if best_openapi else "") or (mcp_manifest.get("host") if mcp_manifest else "") or _host(ORIGIN)
    surface["api_host"] = api_host
    surface["api_base_url"] = "https://" + api_host if api_host else ORIGIN

    surface["has_llms"] = "llms_txt" in surface
    surface["has_openapi"] = "openapi" in surface
    surface["has_mcp"] = bool(mcp_manifest) or bool(plugin_manifest)
    surface["machine_path_present"] = surface["has_llms"] or surface["has_openapi"] or surface["has_mcp"]
    return surface


# ─────────────────────────── Phase 2: human path ─────────────────────────────

NAV_SYSTEM = (
    "You are an autonomous browser agent auditing whether an AI agent can USE a web product "
    "through its HUMAN interface. You see the page's accessibility tree (elements have @refs like @e12). "
    "Decide the SINGLE next action toward the GOAL: dismiss cookie/consent, find and click signup/login, "
    "fill the form with the given credentials, submit, reach the dashboard, attempt the core action. "
    "HUMAN-IN-THE-LOOP: a real human is available to help you past a step ONLY they can do. If you hit an "
    "OTP / 2FA / email-verification / one-time-code wall, or a login that needs a value you were not given, "
    "DO NOT give up and DO NOT retry blindly — respond with action 'ask_human' and a 'prompt' field telling "
    "the human exactly what to provide (e.g. 'Enter the 6-digit code sent to your email'), plus 'kind' "
    "(otp|credential|text). The system will pause, collect the value, and give it back to you so you can fill "
    "it into the focused field and continue. "
    "CRITICAL RULE: A hard CAPTCHA / hCaptcha / reCAPTCHA / Turnstile / bot-check that a human cannot help "
    "you solve through this interface is a DEAD END — for those use action 'blocked' with a 'blocker' field "
    "naming the wall, and never retry the same blocked step twice. (Use 'ask_human' for things a human CAN "
    "supply like an OTP; use 'blocked' for true dead-ends like a CAPTCHA.) "
    "TWO TOOL FAMILIES + A SKILL LIBRARY: besides browser actions you have (a) action 'bash' with a 'cmd' "
    "field to run shell in the sandbox (grep routes, cat files, ls, run repo tooling) — use it for white-box "
    "code analysis when a repo is cloned at /tmp/repo; and (b) action 'skill' with a 'skill' name + 'args' "
    "object to invoke a documented procedure (signup/login/call an endpoint/provoke an error/test "
    "idempotency/speak MCP/capture live API traffic; or code skills: clone_repo/scan_routes/read_auth/"
    "find_openapi/map_code_to_live). Prefer skills for multi-step procedures and bash for ad-hoc inspection. "
    "When a repo is bound, START white-box (clone + scan source) before or alongside the browser pass. "
    'Reply ONLY JSON: {"action":"click|fill|find_click|open|wait|scroll|bash|skill|ask_human|blocked|done",'
    '"ref":"@e..","name":"button/link text for find_click","value":"text for fill",'
    '"cmd":"shell command (only for action=bash)",'
    '"skill":"skill name (only for action=skill)","args":{"...":"skill args (only for action=skill)"},'
    '"prompt":"what to ask the human (only for action=ask_human)",'
    '"kind":"otp|credential|text (only for action=ask_human)",'
    '"blocker":"name of the wall (only for action=blocked)",'
    '"caption":"<=8 words of what you are doing","dimension":"api_surface|auth|error_quality|idempotency|mcp_availability|docs|general",'
    '"note":"one-line observation of what you see"}. Use "done" only when you have analyzed the product '
    'DEEPLY via the best path (code + live) AND distilled the real endpoint surface. Use "blocked" only for a '
    'wall no human can pass through this interface.')


def fill_focused(value: str) -> None:
    """Type a value into whatever field is focused (used after an ask_human).

    We don't have a stable @ref for the OTP/code field at ask time, so we type
    into the currently-focused element via the active element. Best-effort.
    """
    try:
        ab("eval", "(function(v){var el=document.activeElement; if(el){el.focus(); "
                   "el.value=v; el.dispatchEvent(new Event('input',{bubbles:true})); "
                   "el.dispatchEvent(new Event('change',{bubbles:true}));}})("
                   + json.dumps(value) + ")")
    except Exception:  # noqa: BLE001
        pass


def run_action(a: dict) -> None:
    act = (a.get("action") or "").lower()
    if act == "click" and a.get("ref"):
        ab("click", a["ref"])
    elif act == "fill" and a.get("ref"):
        ab("fill", a["ref"], a.get("value", ""))
    elif act == "find_click" and a.get("name"):
        ab("find", "role", "button", "click", "--name", a["name"])
    elif act == "open" and a.get("value"):
        ab_open(a["value"], timeout=60)
    elif act == "scroll":
        ab("eval", "window.scrollBy(0, 700)")
    elif act == "wait":
        ab("eval", "new Promise(r=>setTimeout(r,1200))")


def detect_hard_wall(snapshot: str) -> str:
    """Scan the accessibility tree for a blocker. Returns the wall name or ''."""
    low = (snapshot or "").lower()
    for w in HARD_WALLS:
        if w in low:
            return w
    # A primary/submit button stuck [disabled] with no visible error often = silent gate.
    if "[disabled]" in low and ("submit" in low or "sign up" in low or "continue" in low or "create account" in low):
        return "disabled-submit (silent gate)"
    return ""


# JS injected once we begin: wrap fetch + XHR so every call the authed app makes
# is recorded into window.__wirable_calls. This reveals the TRUE backend surface
# (the XHR/fetch endpoints the product actually uses) which is the real meat.
_CAPTURE_INSTALL_JS = (
    "(function(){if(window.__wirable_installed)return;window.__wirable_installed=true;"
    "window.__wirable_calls=[];"
    "var of=window.fetch;"
    "window.fetch=function(){try{var a=arguments;var u=(a[0]&&a[0].url)||a[0];"
    "var m=(a[1]&&a[1].method)||(a[0]&&a[0].method)||'GET';"
    "return of.apply(this,a).then(function(r){try{window.__wirable_calls.push("
    "{method:String(m).toUpperCase(),url:String(u),status:r.status});}catch(e){}return r;});}"
    "catch(e){return of.apply(this,arguments);}};"
    "var oo=XMLHttpRequest.prototype.open;var os=XMLHttpRequest.prototype.send;"
    "XMLHttpRequest.prototype.open=function(m,u){this.__wm=m;this.__wu=u;return oo.apply(this,arguments);};"
    "XMLHttpRequest.prototype.send=function(){var x=this;"
    "x.addEventListener('loadend',function(){try{window.__wirable_calls.push("
    "{method:String(x.__wm||'GET').toUpperCase(),url:String(x.__wu||''),status:x.status});}catch(e){}});"
    "return os.apply(this,arguments);};})();"
)


def install_api_capture() -> None:
    """Best-effort: wrap fetch/XHR so authed API calls are recorded. Silent on failure."""
    try:
        ab("eval", _CAPTURE_INSTALL_JS)
    except Exception:  # noqa: BLE001
        pass


def read_captured_calls(limit: int = 40) -> list[dict]:
    """Best-effort: read back window.__wirable_calls, deduped {method,url,status}.

    These are the REAL backend endpoints the product hit while the agent used it.
    Strips query strings (keep path) for readability; returns [] on any failure.
    """
    try:
        raw = ab("eval", "JSON.stringify(window.__wirable_calls||[])")
    except Exception:  # noqa: BLE001
        return []
    if not raw or raw.startswith("__err__"):
        return []
    # agent-browser may wrap the eval result; pull the JSON array out.
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end < 0:
            return []
        calls = json.loads(raw[start:end + 1])
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for c in calls if isinstance(calls, list) else []:
        if not isinstance(c, dict):
            continue
        u = str(c.get("url", "") or "")
        # Keep scheme+host+path, drop query/hash, skip static asset noise.
        try:
            sp = urllib.parse.urlsplit(u)
            path = sp.path or u
            clean = (f"{sp.scheme}://{sp.netloc}{path}" if sp.scheme else path)
        except Exception:  # noqa: BLE001
            clean = u
        low = clean.lower()
        if any(low.endswith(ext) for ext in (
            ".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".woff",
            ".woff2", ".ico", ".map", ".webp", ".mp4")):
            continue
        method = str(c.get("method", "GET") or "GET").upper()
        key = f"{method} {clean}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"method": method, "url": clean, "status": c.get("status")})
        if len(out) >= limit:
            break
    return out


# ── Skill execution context ─────────────────────────────────────────────────
# A duck-typed bundle of the helpers skills.py needs (see skills.py docstring).
# Keeps skills.py import-light + stdlib-only; the driver owns all the primitives.


class SkillContext:
    def __init__(self) -> None:
        self.ab = ab
        self.run_bash = run_bash
        self.shot = shot
        self.http_get = http_get
        self.install_api_capture = install_api_capture
        self.read_captured_calls = read_captured_calls
        self.record_code_endpoint = record_code_endpoint
        self.record_openapi_endpoint = record_openapi_endpoint
        self.url = URL
        self.origin = ORIGIN
        self.login_email = LOGIN_EMAIL
        self.login_password = LOGIN_PASSWORD
        self.api_key = ACCESS_API_KEY
        self.bearer = ACCESS_BEARER
        self.test_email = TEST_EMAIL
        self.test_password = TEST_PASSWORD
        self.repo = WIRABLE_REPO
        self.gh_token = WIRABLE_GH_TOKEN
        self.repo_dir = REPO_DIR

    def code_endpoints_sample(self) -> list[dict]:
        return (CODE_ENDPOINTS or OPENAPI_ENDPOINTS)[:8]


SKILL_CTX = SkillContext()


def run_skill_action(name: str, args: dict) -> dict:
    """Invoke a named skill via the registry, fold its framework hint, return evidence."""
    if _skills is None:
        return {"skill": name, "ok": False, "note": "skills library unavailable", "dimension": "general"}
    ev = _skills.run_skill(SKILL_CTX, name, args or {})
    try:
        fw = ev.get("framework")
        if fw and fw != "unknown":
            CODE_FRAMEWORK["name"] = fw
    except Exception:  # noqa: BLE001
        pass
    return ev if isinstance(ev, dict) else {"skill": name, "ok": False, "note": "no evidence", "dimension": "general"}


def analyze_codebase() -> dict:
    """White-box pass: clone the bound repo and scan source for routes / auth /
    committed OpenAPI. Runs BEFORE the browser pass when a repo is bound. Every
    step is a skill (so it records evidence + streams a frame); any failure
    degrades to 'not cloned' rather than crashing. Returns a code_analysis dict.
    """
    notes: list[str] = []
    cloned = False
    if not HAS_REPO:
        return {"cloned": False, "framework": "unknown", "endpoints_from_code": 0,
                "notes": "no WIRABLE_REPO/WIRABLE_GH_TOKEN bound (live-only audit)"}
    if _skills is None:
        return {"cloned": False, "framework": "unknown", "endpoints_from_code": 0,
                "notes": "skills library unavailable; skipped code analysis"}
    shot(f"White-box: cloning {WIRABLE_REPO}", "general")
    clone_ev = run_skill_action("clone_repo", {})
    cloned = bool(clone_ev.get("cloned") or clone_ev.get("ok"))
    notes.append(clone_ev.get("note", "")[:200])
    if cloned:
        for sk in ("scan_routes", "read_auth", "find_openapi"):
            ev = run_skill_action(sk, {})
            notes.append(f"{sk}: {ev.get('note','')[:160]}")
    return {
        "cloned": cloned,
        "framework": CODE_FRAMEWORK["name"],
        "endpoints_from_code": len(CODE_ENDPOINTS) + len(OPENAPI_ENDPOINTS),
        "notes": " | ".join(n for n in notes if n)[:1200],
    }


def deep_explore(machine: dict) -> dict:
    """Bounded, CAPTCHA-aware exploration. Returns {trajectory, blockers, api_calls}.

    MACHINE-FIRST: if the product exposes a usable machine surface (llms.txt /
    OpenAPI / MCP / a reachable API), an agent would USE THE API, not the human
    web form. In that case we do NOT sign up or log in and we do NOT fight auth
    walls — we confirm the docs/API are agent-navigable and how an agent gets a
    key, in a few steps. The human signup/login path is the FALLBACK, only when
    no machine surface exists.
    """
    trajectory: list[dict] = []
    blockers: list[str] = []
    machine_first = bool(machine.get("machine_path_present"))
    step_cap = 7 if machine_first else MAX_STEPS
    # A bound repo means real white-box work to do in the loop; give a few more
    # steps so the agent can cross-check code routes against live behavior.
    if HAS_REPO:
        step_cap = max(step_cap, MAX_STEPS) + 4
    ab_open(URL, timeout=90)
    # Best-effort: start recording the real API calls the app makes from here on.
    install_api_capture()
    shot("Landing — what an agent first sees", "general")
    if machine_first:
        found = []
        if machine.get("has_llms"):
            found.append("llms.txt")
        if machine.get("has_openapi"):
            found.append(f"OpenAPI ({machine.get('openapi_url') or 'spec'})")
        if machine.get("has_mcp"):
            found.append("an MCP manifest")
        found_txt = ", ".join(found) or "a machine API"
        api_host = machine.get("api_host") or ""
        key_hint = (
            " You were given an API key/token, so note an agent could call the API directly with it."
            if (ACCESS_API_KEY or ACCESS_BEARER) else ""
        )
        goal = (
            f"{URL} exposes a MACHINE interface for agents (found: {found_txt}"
            f"{(' on ' + api_host) if api_host else ''}). Evaluate it the way an autonomous AGENT would "
            "actually use this product: open the landing, then the docs / API reference / llms.txt, and "
            "confirm they are reachable and agent-navigable. Understand how an agent AUTHENTICATES "
            "programmatically (an API key or token from a docs or settings page). "
            "Do NOT create an account or log in through the human web UI, and do NOT fight CAPTCHAs or "
            f"verification codes — an agent uses the API, not the web form.{key_hint} "
            "Use action 'done' as soon as you've confirmed whether the machine path is usable."
        )
    elif HAS_CREDS:
        # Pre-run access granted: LOG IN with the human's real credentials and
        # exercise the AUTHED product (the real meat — see the agent using it).
        cred_line = []
        if LOGIN_EMAIL:
            cred_line.append(f"email '{LOGIN_EMAIL}'")
        if LOGIN_PASSWORD:
            cred_line.append(f"password '{LOGIN_PASSWORD}'")
        if ACCESS_API_KEY:
            cred_line.append(f"API key '{ACCESS_API_KEY}'")
        if ACCESS_BEARER:
            cred_line.append(f"bearer token '{ACCESS_BEARER}'")
        creds_txt = ", ".join(cred_line) or "the provided credentials"
        notes_txt = f" Notes from the user: {ACCESS_NOTES}." if ACCESS_NOTES else ""
        goal = (
            f"You have been given real credentials to SIGN IN to {URL}. Find the login (not signup) and "
            f"sign in with {creds_txt}.{notes_txt} Reach the authenticated dashboard, then perform the "
            "product's core action so we can see an agent actually USING the authed product. After that, try "
            "one invalid action to observe the error, then repeat a write to test idempotency. If a login "
            "needs an OTP / 2FA code or an extra value you were not given, use action 'ask_human' to request "
            "it from the user. Avoid destructive actions.")
    else:
        goal = (
            f"Create an account on {URL} and reach the dashboard, then do the product's core action. "
            f"Use email '{TEST_EMAIL}' and password '{TEST_PASSWORD}'. If signup needs an OTP / email "
            "verification code, use action 'ask_human' to request it. After that, try one invalid action "
            "to see the error, then repeat a write to test idempotency. Avoid destructive actions.")

    # Append the white-box hint + skill catalog so the agent picks tools/skills
    # deliberately. The codebase clone+scan already ran in analyze_codebase()
    # before this loop when a repo is bound; here the agent cross-checks live.
    if HAS_REPO:
        goal += (
            f" A source repo is cloned at {REPO_DIR} (framework: {CODE_FRAMEWORK['name']}, "
            f"{len(CODE_ENDPOINTS) + len(OPENAPI_ENDPOINTS)} endpoints already extracted from code). "
            "Use bash to read the source and skills to cross-check those code endpoints against LIVE behavior "
            "(call_endpoint / map_code_to_live) and to capture real API traffic (capture_api_surface)."
        )
    if _skills is not None:
        try:
            goal += "\n\n" + _skills.catalog_text(HAS_REPO, HAS_CREDS, machine_first)
        except Exception:  # noqa: BLE001
            pass

    recent_sigs: list[str] = []  # loop-guard signatures
    asked_walls: set[str] = set()  # walls we've already asked the human about
    api_calls: list[dict] = []  # accumulated real backend calls (deduped below)
    _api_seen: set[str] = set()

    def _accumulate_calls() -> None:
        # Read whatever the current document captured, fold into the running list,
        # then re-arm (a navigation may have reset window state). Best-effort.
        for c in read_captured_calls():
            key = f"{c.get('method')} {c.get('url')}"
            if key not in _api_seen:
                _api_seen.add(key)
                api_calls.append(c)
        install_api_capture()

    for step in range(step_cap):
        # Drain + re-arm the API capture each step so calls survive navigations.
        _accumulate_calls()
        snap = ab("snapshot", timeout=45)
        # Frame the page state at the START of each step (a "before" frame). With
        # the post-action shot below this roughly doubles the live frame rate, so
        # the viewport updates ~twice per step instead of once.
        shot(f"Step {step+1}: observing page", "general")

        # Hard-wall detector fires BEFORE we ask the model to act again.
        wall = detect_hard_wall(snap)
        if wall:
            # If it's a wall a human can pass (OTP / 2FA / email code) and a human
            # is in the loop, ASK them for the value and continue instead of
            # recording a dead-end. Each wall is asked at most once (asked_walls).
            if wall in HUMAN_RESOLVABLE_WALLS and wall not in asked_walls:
                asked_walls.add(wall)
                shot(f"Asking human: {wall}", "auth")
                trajectory.append({"caption": f"asking human for {wall[:30]}",
                                   "note": "human-in-the-loop request", "dimension": "auth"})
                value = request_human(
                    f"The product needs a value to continue ({wall}). Please provide it.",
                    kind="otp",
                )
                if value:
                    fill_focused(value)
                    # Try to advance past the code entry (common submit affordances).
                    ab("find", "role", "button", "click", "--name", "Verify")
                    ab("find", "role", "button", "click", "--name", "Continue")
                    ab("find", "role", "button", "click", "--name", "Submit")
                    shot("Submitted human-provided value", "auth")
                    trajectory.append({"caption": "human value submitted", "note": f"resolved wall: {wall}",
                                       "dimension": "auth"})
                    continue
                # Timed out — fall through to recording it as a blocker.
            blockers.append(wall)
            trajectory.append({"caption": f"hit wall: {wall}", "note": f"hard-wall detector: {wall}",
                               "dimension": "auth", "blocked": True})
            shot(f"Blocked by {wall}", "auth")
            break

        hist = "\n".join(f"- {t['caption']} ({t.get('note','')[:60]})" for t in trajectory[-8:])
        # Vision grounding: decide on the SCREENSHOT + the a11y tree together. The
        # tree's @refs are the marks; the image lets the agent act on visual/canvas
        # widgets the tree misses. Best-effort — falls back to tree-only if no image.
        vis = screen_b64()
        a = claude_json(
            NAV_SYSTEM,
            f"GOAL: {goal}\n\nSteps so far:\n{hist or '(none)'}\n\n"
            "You are shown a SCREENSHOT of the current page plus its accessibility "
            "tree. Use BOTH: the image to see what's actually rendered, the tree's "
            f"@refs to target elements.\n\nAccessibility tree (truncated):\n{snap[:6500]}",
            max_tokens=400,
            image_b64=vis,
        )

        if not a or a.get("action") in (None, "done"):
            trajectory.append({"caption": a.get("caption", "exploration complete") if a else "stop",
                               "note": a.get("note", "") if a else "", "dimension": "general"})
            break

        act = (a.get("action") or "").lower()
        if act == "ask_human":
            prompt = (a.get("prompt") or a.get("note") or "Please provide the value to continue.").strip()
            kind = (a.get("kind") or "text").strip().lower()
            if kind not in ("otp", "credential", "text"):
                kind = "text"
            shot(f"Asking human: {prompt[:40]}", a.get("dimension", "auth"))
            trajectory.append({"caption": (a.get("caption") or "asking human")[:60],
                               "note": prompt[:120], "dimension": a.get("dimension", "auth")})
            value = request_human(prompt, kind=kind)
            if value:
                # If the model named a target field, fill it; else type into focus.
                if a.get("ref"):
                    ab("fill", a["ref"], value)
                else:
                    fill_focused(value)
                shot("Human value provided — continuing", a.get("dimension", "auth"))
                trajectory.append({"caption": "human value submitted", "note": "resumed after human input",
                                   "dimension": a.get("dimension", "auth")})
                continue
            # Timeout — no human answer. Record as blocked and stop.
            blockers.append(f"awaiting human ({kind}) — no response")
            trajectory.append({"caption": "human input timed out", "note": prompt[:120],
                               "dimension": a.get("dimension", "auth"), "blocked": True})
            shot("Human input timed out", a.get("dimension", "auth"))
            break

        if act == "blocked":
            blk = (a.get("blocker") or a.get("note") or "unspecified wall").strip()
            blockers.append(blk)
            trajectory.append({"caption": f"blocked: {blk[:40]}", "note": a.get("note", "")[:120],
                               "dimension": a.get("dimension", "auth"), "blocked": True})
            shot(f"Agent reports blocked: {blk[:40]}", a.get("dimension", "auth"))
            break

        # ── code/ops modality: run shell in the sandbox ──
        if act == "bash":
            cmd = (a.get("cmd") or a.get("value") or "").strip()
            dim = a.get("dimension", "general")
            cap = (a.get("caption") or ("bash: " + cmd[:30]))[:60]
            if not cmd:
                trajectory.append({"caption": "bash (no cmd)", "note": "model emitted bash with no cmd",
                                   "dimension": dim})
            else:
                out = run_bash(cmd, timeout=int(a.get("timeout") or 60))
                shot(cap, dim)
                trajectory.append({"caption": cap, "note": f"$ {cmd[:80]}\n{out[:400]}", "dimension": dim})
            # fall through to loop-guard so a repeated identical bash still bails
            sig = f"bash|{cmd[:40]}|{cap[:30]}"
            recent_sigs.append(sig)
            recent_sigs = recent_sigs[-4:]
            if len(recent_sigs) >= 3 and len(set(recent_sigs[-3:])) == 1:
                trajectory.append({"caption": "loop detected — stopping", "note": f"repeated: {sig}",
                                   "dimension": "general"})
                shot("Loop-guard tripped", "general")
                break
            continue

        # ── skill-library modality: invoke a documented procedure ──
        if act == "skill":
            name = (a.get("skill") or "").strip()
            args = a.get("args") if isinstance(a.get("args"), dict) else {}
            cap = (a.get("caption") or ("skill: " + name))[:60]
            ev = run_skill_action(name, args)
            dim = ev.get("dimension") or a.get("dimension", "general")
            shot(cap, dim)
            trajectory.append({
                "caption": cap,
                "note": f"skill {name}: {ev.get('note','')[:300]}",
                "dimension": dim,
                "blocked": False,
            })
            sig = f"skill|{name}|{json.dumps(args, sort_keys=True)[:40]}"
            recent_sigs.append(sig)
            recent_sigs = recent_sigs[-4:]
            if len(recent_sigs) >= 3 and len(set(recent_sigs[-3:])) == 1:
                trajectory.append({"caption": "loop detected — stopping", "note": f"repeated: {sig}",
                                   "dimension": "general"})
                shot("Loop-guard tripped", "general")
                break
            continue

        # Loop-guard: same action signature repeating >=2 times → bail (the old
        # accessibility-cookie retry loop).
        sig = f"{act}|{(a.get('ref') or a.get('name') or a.get('value') or '')[:40]}|{(a.get('caption') or '')[:30]}"
        recent_sigs.append(sig)
        recent_sigs = recent_sigs[-4:]
        if recent_sigs.count(sig) >= 3 or (len(recent_sigs) >= 3 and len(set(recent_sigs[-3:])) == 1):
            trajectory.append({"caption": "loop detected — stopping", "note": f"repeated: {sig}",
                               "dimension": "general", "blocked": False})
            shot("Loop-guard tripped", "general")
            break

        cap = (a.get("caption") or a.get("action") or "step")[:60]
        run_action(a)
        shot(f"{step+1}. {cap}", a.get("dimension", "general"))
        trajectory.append({"caption": cap, "note": a.get("note", "")[:120], "dimension": a.get("dimension", "general")})

    # Final drain — catch calls fired by the last action.
    _accumulate_calls()
    return {"trajectory": trajectory, "blockers": blockers, "api_calls": api_calls[:40]}


# ──────────────────────── endpoint distillation (white+black) ────────────────


def _norm_path(u: str) -> str:
    """Reduce a live URL to a path for dedup/merge against code/openapi paths."""
    try:
        sp = urllib.parse.urlsplit(u)
        return sp.path or u
    except Exception:  # noqa: BLE001
        return u


def distill_endpoints(api_calls: list[dict], machine: dict) -> list[dict]:
    """Merge endpoints from (a) the codebase, (b) observed live traffic, and
    (c) a discovered OpenAPI into ONE deduped list. Each item:
        {method, path, summary, source: "code"|"traffic"|"openapi", auth}

    Dedup key = METHOD + normalized path. When the same endpoint appears in more
    than one source we keep the first seen and prefer a richer summary/auth.
    The proxy generator consumes this as the real endpoint set to target.
    """
    merged: dict[str, dict] = {}

    def _add(method: str, path: str, summary: str, source: str, auth) -> None:
        method = (method or "ANY").upper()
        path = (path or "").strip()
        if not path:
            return
        key = f"{method} {_norm_path(path)}"
        if key in merged:
            cur = merged[key]
            if not cur.get("summary") and summary:
                cur["summary"] = summary[:120]
            if cur.get("auth") in (None, "") and auth:
                cur["auth"] = auth
            return
        merged[key] = {"method": method, "path": _norm_path(path),
                       "summary": (summary or "")[:120], "source": source, "auth": auth}

    # (a) codebase routes — ground truth from source.
    for ep in CODE_ENDPOINTS:
        _add(ep.get("method"), ep.get("path"), ep.get("summary", ""), "code", ep.get("auth"))
    # (c) committed OpenAPI.
    for ep in OPENAPI_ENDPOINTS:
        _add(ep.get("method"), ep.get("path"), ep.get("summary", ""), "openapi", ep.get("auth"))
    # (c) discovered live OpenAPI (from machine-surface discovery, if present).
    oa = machine.get("openapi") or {}
    for line in (oa.get("example_endpoints") or []):
        # "GET,POST /widgets" -> one entry per method
        try:
            parts = str(line).split(None, 1)
            methods = parts[0].split(",") if len(parts) == 2 else ["ANY"]
            path = parts[1] if len(parts) == 2 else parts[0]
            for m in methods:
                _add(m, path, "", "openapi", None)
        except Exception:  # noqa: BLE001
            continue
    # (b) observed live traffic — real XHR/fetch the app made.
    for c in (api_calls or []):
        _add(c.get("method"), c.get("url", ""), "", "traffic", None)

    return list(merged.values())[:120]


# ─────────────────────────────── Phase 3: verdict ────────────────────────────

VERDICT_SYSTEM = (
    "You are Wirable, a rigorous auditor of whether autonomous AI agents can USE a web product. "
    "An agent prefers the MACHINE interface (llms.txt → docs → API/MCP) and only falls back to the human UI. "
    "Judge whether an autonomous agent could accomplish the goal via the BEST available path. "
    "REWARD a usable machine path (llms.txt/openapi/mcp the agent can actually act on). "
    "Treat a CAPTCHA/OTP/email-verify HUMAN wall as a blocker that ONLY matters if there is NO machine path. "
    "Be skeptical; every evidence line must be literal and cite the real machine-surface read or the trajectory. "
    "Write plainly. Do not use em-dashes. Avoid marketing buzzwords and AI-cliche phrasing.")


def main() -> None:
    # Phase 0 + 1 — machine surface FIRST.
    machine = discover_machine_surface()

    # Phase 1.5 — WHITE-BOX code analysis (clone + scan source) when a repo is
    # bound. Runs before the browser pass so the live cross-check has code routes
    # to test. Skipped (live-only) when no repo env is set. Never crashes the run.
    code_analysis = {"cloned": False, "framework": "unknown", "endpoints_from_code": 0,
                     "notes": "no WIRABLE_REPO/WIRABLE_GH_TOKEN bound (live-only audit)"}
    if MISSION != "fast":
        try:
            code_analysis = analyze_codebase()
        except Exception as e:  # noqa: BLE001
            code_analysis = {"cloned": False, "framework": "unknown", "endpoints_from_code": 0,
                             "notes": f"code analysis failed: {e}"}

    # Phase 2 — human path (bounded, CAPTCHA-aware), unless probe-only.
    if MISSION == "fast":
        ab_open(URL, timeout=60)
        shot("Landing (probe pass)", "general")
        explore = {"trajectory": [], "blockers": [], "api_calls": []}
        final_snap = ab("snapshot", timeout=45)
        ab("close")
    else:
        # Frame: announce we read the machine surface first (matches Wirable's thesis).
        m_caption = "Read machine surface: " + ", ".join(
            k for k, present in (("llms.txt", machine.get("has_llms")),
                                 ("openapi", machine.get("has_openapi")),
                                 ("mcp", machine.get("has_mcp"))) if present
        ) if machine.get("machine_path_present") else "No machine surface found"
        shot(m_caption[:120], "mcp_availability")
        explore = deep_explore(machine)
        final_snap = ab("snapshot", timeout=45)
        ab("close")

    trajectory = explore["trajectory"]
    blockers = explore["blockers"]
    api_calls = explore.get("api_calls", []) or []

    # Build the machine-surface summary the verdict will read (trim the heavy probe blob).
    machine_summary = {
        "machine_path_present": machine.get("machine_path_present"),
        "has_llms": machine.get("has_llms"),
        "has_openapi": machine.get("has_openapi"),
        "has_mcp": machine.get("has_mcp"),
        "registrable_domain": machine.get("registrable_domain"),
        "hosts_reachable": machine.get("hosts_reachable"),
        "best_entrypoint": machine.get("best_entrypoint"),
        "best_entrypoint_kind": machine.get("best_entrypoint_kind"),
        "api_host": machine.get("api_host"),
        "openapi_url": machine.get("openapi_url"),
        "surfaces": machine.get("surfaces", [])[:30],
        "probe_status": machine.get("probes_by_host"),
    }
    for k in ("llms_txt", "openapi", "mcp", "ai_plugin"):
        if k in machine:
            machine_summary[k] = machine[k]

    traj_txt = "\n".join(
        f"{i+1}. {t['caption']} — {t.get('note','')}" + (" [BLOCKED]" if t.get("blocked") else "")
        for i, t in enumerate(trajectory)
    ) or "(probe-only pass)"
    blockers_txt = ", ".join(dict.fromkeys(blockers)) or "(none detected)"

    # The REAL backend surface captured while the agent used the (often authed)
    # product — the XHR/fetch calls the app actually made. This is ground truth
    # about the endpoint surface, distinct from the declared machine surface.
    if api_calls:
        api_txt = "\n".join(
            f"{c.get('method','GET')} {c.get('url','')} -> {c.get('status','?')}"
            for c in api_calls[:40]
        )
    else:
        api_txt = "(no XHR/fetch calls captured)"

    # Merge code + traffic + openapi endpoints into one deduped distilled set
    # (white-box + black-box). The proxy generator consumes this directly.
    distilled = distill_endpoints(api_calls, machine)
    distilled_txt = "\n".join(
        f"{e['method']} {e['path']} [{e['source']}]" + (f" auth={e['auth']}" if e.get("auth") else "")
        for e in distilled[:50]
    ) or "(none distilled)"

    # White-box code-analysis summary the verdict reasons over alongside live.
    code_txt = (
        f"cloned={code_analysis.get('cloned')} framework={code_analysis.get('framework')} "
        f"endpoints_from_code={code_analysis.get('endpoints_from_code')}\n"
        f"notes: {code_analysis.get('notes','')[:1200]}"
    )

    prompt = (
        f"Target: {URL}\n\n"
        f"MACHINE SURFACE (the agent's preferred path — llms.txt text + linked docs + openapi + mcp):\n"
        f"{json.dumps(machine_summary)[:6000]}\n\n"
        f"WHITE-BOX CODE ANALYSIS (source cloned from the bound repo; routes/auth/openapi extracted from "
        f"the actual code — this is ground truth, not inference):\n{code_txt}\n\n"
        f"DISTILLED ENDPOINTS (merged + deduped from code + live traffic + openapi):\n{distilled_txt[:2500]}\n\n"
        f"BROWSER TRAJECTORY (what the agent did — code skills, bash, machine-first when a machine surface "
        f"exists, else the human signup/login fallback):\n{traj_txt[:3200]}\n\n"
        f"OBSERVED BACKEND API CALLS (the REAL XHR/fetch the product made while the agent used it — this is "
        f"the true LIVE endpoint surface, captured live):\n{api_txt[:2200]}\n\n"
        f"DETECTED BLOCKERS on the human path (CAPTCHA/OTP/bot-walls/loops): {blockers_txt}\n\n"
        f"Final accessibility snapshot (truncated):\n{final_snap[:2200]}\n\n"
        "Reason over BOTH the white-box code analysis (source of truth) AND the live black-box trajectory.\n"
        "Judge the 6 dimensions — for each: passed(bool), confidence(0-1), evidence(ONE literal line "
        "citing the code analysis, the machine-surface read, or the trajectory):\n"
        "- api_surface: is there an agent-usable API/endpoint surface? Count the DISTILLED ENDPOINTS above "
        "(code + live traffic + openapi), the declared machine surface, AND the observed backend calls.\n"
        "- auth: could the agent ACTUALLY authenticate via the BEST path — use the code-derived auth model "
        "AND any API-key auth documented in llms.txt/openapi (counts even if web signup is CAPTCHA-walled). "
        "Note the wall but don't fail auth if a machine auth path exists.\n"
        "- error_quality, idempotency\n"
        "- mcp_availability: is an MCP/plugin server declared (.well-known/mcp.json or ai-plugin.json)?\n"
        "- docs: did llms.txt / openapi / linked docs give an agent real, actionable guidance?\n\n"
        "ALSO write 6-8 'wrapped' cards (punchy, shareable, evidence-grounded). Lead with the MOST "
        "decision-relevant finding: is there ANY agent-usable path or not? Capture the nuance — e.g. a card "
        "contrasting 'llms.txt exists ✓' vs 'web signup is CAPTCHA-walled' vs 'API path is viable/not'. "
        'Each card: {eyebrow:question, headline:<=5 words, detail:one specific sentence citing evidence, '
        'dimension:one of the 6 or general, tone:good|bad|warn}.\n\n'
        'Return ONLY JSON: {"domain":"...","dimensions":{"api_surface":{"passed":false,"confidence":0.9,'
        '"evidence":"..."},...all 6...},"cards":[...],"summary":"..."}'
    )
    out = claude_json(VERDICT_SYSTEM, prompt)

    if not isinstance(out, dict):
        out = {}
    out.setdefault("domain", URL)
    dims = out.get("dimensions") or {}
    for d in DIMS:
        cur = dims.get(d) or {}
        dims[d] = {"passed": bool(cur.get("passed", False)),
                   "confidence": float(cur.get("confidence", 0.5) or 0.5),
                   "evidence": str(cur.get("evidence", "") or "not evaluated")[:240]}
    out["dimensions"] = dims
    if not isinstance(out.get("cards"), list):
        out["cards"] = []
    out.setdefault("summary", "")
    out["frames"] = _frame
    # Carry the discovered machine surface so the proxy generator can target the
    # REAL API host (e.g. api.kortix.com), not just the apex.
    out["machine_surface"] = machine_summary
    # The REAL backend endpoints observed while the agent used the authed product.
    out["observed_api"] = api_calls[:40]
    # NEW: merged white-box + black-box endpoint set the proxy generator targets.
    out["distilled_endpoints"] = distilled
    # NEW: white-box code-analysis summary (cloned/framework/#endpoints/notes).
    out["code_analysis"] = {
        "cloned": bool(code_analysis.get("cloned")),
        "framework": str(code_analysis.get("framework") or "unknown"),
        "endpoints_from_code": int(code_analysis.get("endpoints_from_code") or 0),
        "notes": str(code_analysis.get("notes") or "")[:1500],
    }

    shot("Verdict computed", "general")
    try:
        json.dump(out, open("/tmp/output.json", "w"))
    except Exception:  # noqa: BLE001
        pass
    print(f"audit_driver: {_frame} frames, mission={MISSION}, machine_path={machine.get('machine_path_present')}, "
          f"repo={'yes' if HAS_REPO else 'no'}, code_endpoints={code_analysis.get('endpoints_from_code')}, "
          f"distilled={len(distilled)}, blockers={len(blockers)}, wrote /tmp/output.json")


if __name__ == "__main__":
    main()
