#!/usr/bin/env python3
"""
Wirable CODE harness driver — runs INSIDE the sandbox.

This is the CODE-GROUNDED endpoint extractor. When a GitHub repo is bound to a
tested product we clone it (shallow, token-auth'd URL) and extract the REAL API
endpoints from the SOURCE CODE — ground truth, not black-box probes — so the
orchestrator can persist the analyzed commit + endpoint set and diff it on
future commits.

argv: <authed_clone_url> <repo_full_name> [ref]
  authed_clone_url already embeds the token:
    https://x-access-token:<token>@github.com/owner/repo.git
  ref (optional): a branch, tag, or commit sha to analyze instead of HEAD.

Writes /tmp/code_output.json:
  {commit_sha, branch, framework, endpoints:[...], base_url_hint, scanned_files, error?}

endpoints[] item: {method, path, summary, params:[...], auth: str|None, source: "file:line"}

Extraction priority (first productive source wins, but lower tiers can augment):
  1. A committed OpenAPI/Swagger doc (openapi.json/yaml, swagger.json) -> parse.
  2. Framework route detection by scanning source files:
     FastAPI, Flask, Express, Next.js App Router, Django urls.py, Go (chi/gin/mux),
     Rails routes.rb. Captures {method, path, source file:line}.
  3. If route files were found but parsing stayed thin, ONE Claude pass over the
     most relevant route files (capped total chars) -> JSON list of endpoints.

NEVER raises — every failure is captured into the json so the orchestrator can
degrade gracefully. stdlib + subprocess(git) only (no project deps in sandbox).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request

CLONE_URL = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
REPO_FULL = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
REF = (sys.argv[3] if len(sys.argv) > 3 else "").strip()

REPO_DIR = "/tmp/code_repo"
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

MAX_ENDPOINTS = 200
MAX_SCAN_FILES = 4000
MAX_FILE_BYTES = 200_000
PAYLOAD_CAP = 600_000  # cap the serialized output so the read stays cheap

# Directories we never descend into when scanning source.
SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "out", "vendor",
    "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
    "target", "bin", "obj", ".idea", ".vscode", "coverage", ".cache",
    "site-packages", "migrations",
}

HTTP_VERBS = ("get", "post", "put", "patch", "delete", "head", "options")


# ---------------------------------------------------------------------------
# subprocess / git helpers (mirror fix_driver.py)
# ---------------------------------------------------------------------------

def sh(*args: str, cwd: str | None = None, timeout: int = 180) -> tuple[int, str]:
    """Run a subprocess; return (returncode, combined stdout+stderr). Never raises."""
    try:
        r = subprocess.run(
            list(args), cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"__err__ {e}"


def ensure_git() -> bool:
    rc, _ = sh("git", "--version", timeout=30)
    if rc == 0:
        return True
    sh("sudo", "apt-get", "update", "-y", timeout=180)
    sh("sudo", "apt-get", "install", "-y", "git", timeout=300)
    rc, _ = sh("git", "--version", timeout=30)
    return rc == 0


def read_text(path: str, limit: int = MAX_FILE_BYTES) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except Exception:  # noqa: BLE001
        return ""


def rel(path: str) -> str:
    """Repo-relative path for `source` fields."""
    try:
        return os.path.relpath(path, REPO_DIR)
    except Exception:  # noqa: BLE001
        return path


# ---------------------------------------------------------------------------
# Claude helper (urllib, mirrors audit_driver.py)
# ---------------------------------------------------------------------------

def claude(system: str, prompt: str, max_tokens: int = 2200) -> str:
    if not ANTHROPIC_KEY:
        return ""
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": max_tokens,
            "temperature": 0.1,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=120))
        return "".join(
            b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict)
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def claude_json_list(system: str, prompt: str) -> list:
    """Ask Claude and best-effort parse a JSON list out of the reply."""
    raw = claude(system, prompt)
    if not raw:
        return []
    # strip code fences if present
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)
        txt = txt[1] if len(txt) > 1 else raw
        if txt.lstrip().lower().startswith("json"):
            txt = txt.split("\n", 1)[-1]
    # find the first [...] block
    start = txt.find("[")
    end = txt.rfind("]")
    if start >= 0 and end > start:
        chunk = txt[start : end + 1]
        try:
            data = json.loads(chunk)
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001
            pass
    return []


# ---------------------------------------------------------------------------
# endpoint normalization / collection
# ---------------------------------------------------------------------------

def _norm_path(p: str) -> str:
    p = (p or "").strip().strip("'\"`")
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def make_ep(method: str, path: str, source: str = "",
            summary: str = "", params=None, auth=None) -> dict | None:
    method = (method or "").strip().upper()
    path = _norm_path(path)
    if not method or not path:
        return None
    if method.lower() not in HTTP_VERBS and method != "ALL":
        return None
    return {
        "method": method,
        "path": path,
        "summary": (summary or "").strip(),
        "params": list(params or []),
        "auth": auth if auth else None,
        "source": source or "",
    }


def dedupe(endpoints: list) -> list:
    """Key by 'METHOD PATH'; first wins, but fill blank summary/source from dups."""
    out: dict[str, dict] = {}
    for ep in endpoints:
        if not ep:
            continue
        key = f"{ep['method']} {ep['path']}"
        if key not in out:
            out[key] = ep
        else:
            cur = out[key]
            if not cur.get("summary") and ep.get("summary"):
                cur["summary"] = ep["summary"]
            if not cur.get("source") and ep.get("source"):
                cur["source"] = ep["source"]
            if not cur.get("auth") and ep.get("auth"):
                cur["auth"] = ep["auth"]
            if not cur.get("params") and ep.get("params"):
                cur["params"] = ep["params"]
    return list(out.values())


# ---------------------------------------------------------------------------
# file walking
# ---------------------------------------------------------------------------

def walk_files(exts: tuple[str, ...] | None = None,
               names: tuple[str, ...] | None = None):
    """Yield absolute file paths under REPO_DIR, skipping vendored/build dirs."""
    count = 0
    for root, dirs, files in os.walk(REPO_DIR):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".git")]
        for f in files:
            if count >= MAX_SCAN_FILES:
                return
            lf = f.lower()
            ok = False
            if names and lf in names:
                ok = True
            if exts and lf.endswith(exts):
                ok = True
            if ok:
                count += 1
                yield os.path.join(root, f)


def line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


# ---------------------------------------------------------------------------
# Tier 1: committed OpenAPI / Swagger document
# ---------------------------------------------------------------------------

OPENAPI_NAMES = (
    "openapi.json", "openapi.yaml", "openapi.yml",
    "swagger.json", "swagger.yaml", "swagger.yml",
)


def _yaml_to_obj(text: str) -> dict | None:
    """Try a real yaml parser; if absent, a tiny defensive fallback returns None."""
    try:
        import yaml  # not guaranteed in the sandbox

        obj = yaml.safe_load(text)
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001
        return None


def parse_openapi(path: str) -> tuple[list, str | None]:
    """Parse an OpenAPI/Swagger doc -> (endpoints, base_url_hint). Never raises."""
    txt = read_text(path)
    if not txt:
        return [], None
    obj: dict | None = None
    if path.lower().endswith(".json"):
        try:
            obj = json.loads(txt)
        except Exception:  # noqa: BLE001
            obj = None
    else:
        obj = _yaml_to_obj(txt)
        if obj is None:
            # last resort: a yaml doc that is actually valid json
            try:
                obj = json.loads(txt)
            except Exception:  # noqa: BLE001
                obj = None
    if not isinstance(obj, dict):
        return [], None

    paths = obj.get("paths")
    if not isinstance(paths, dict):
        return [], None

    base_hint = None
    # OpenAPI 3: servers[0].url ; Swagger 2: host + basePath
    servers = obj.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        base_hint = servers[0].get("url") or None
    if not base_hint:
        host = obj.get("host")
        base_path = obj.get("basePath") or ""
        if host:
            base_hint = host + base_path

    src_rel = rel(path)
    eps: list = []
    for raw_path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for verb, op in item.items():
            if verb.lower() not in HTTP_VERBS:
                continue
            op = op if isinstance(op, dict) else {}
            summary = op.get("summary") or op.get("operationId") or op.get("description") or ""
            params = []
            for p in op.get("parameters", []) if isinstance(op.get("parameters"), list) else []:
                if isinstance(p, dict) and p.get("name"):
                    params.append(str(p["name"]))
            auth = None
            sec = op.get("security")
            if isinstance(sec, list) and sec:
                try:
                    auth = ", ".join(
                        k for s in sec if isinstance(s, dict) for k in s.keys()
                    ) or None
                except Exception:  # noqa: BLE001
                    auth = None
            ep = make_ep(verb, str(raw_path), source=src_rel,
                         summary=str(summary)[:200], params=params, auth=auth)
            if ep:
                eps.append(ep)
    return eps, (str(base_hint) if base_hint else None)


# ---------------------------------------------------------------------------
# Tier 2: framework route detection
# ---------------------------------------------------------------------------

# FastAPI / Flask decorators:  @app.get("/x")  @router.post('/y', ...)  @app.route("/z", methods=["POST"])
_PY_VERB_RE = re.compile(
    r"@(\w+)\.(get|post|put|patch|delete|head|options)\s*\(\s*([\"'])(?P<path>[^\"']*)\3",
    re.IGNORECASE,
)
_FLASK_ROUTE_RE = re.compile(
    r"@(\w+)\.route\s*\(\s*([\"'])(?P<path>[^\"']*)\2(?P<rest>[^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)
_FLASK_METHODS_RE = re.compile(r"methods\s*=\s*\[([^\]]*)\]", re.IGNORECASE)


def scan_python(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    eps: list = []
    for m in _PY_VERB_RE.finditer(txt):
        ep = make_ep(m.group(2), m.group("path"),
                     source=f"{src}:{line_of(txt, m.start())}")
        if ep:
            eps.append(ep)
    for m in _FLASK_ROUTE_RE.finditer(txt):
        methods = ["GET"]
        mm = _FLASK_METHODS_RE.search(m.group("rest") or "")
        if mm:
            methods = [
                v.strip().strip("'\"").upper()
                for v in mm.group(1).split(",")
                if v.strip()
            ] or ["GET"]
        ln = line_of(txt, m.start())
        for verb in methods:
            ep = make_ep(verb, m.group("path"), source=f"{src}:{ln}")
            if ep:
                eps.append(ep)
    return eps


# Express:  app.get('/x', ...)  router.post("/y", ...)
_EXPRESS_RE = re.compile(
    r"\b(\w+)\.(get|post|put|patch|delete|head|options|all)\s*\(\s*([\"'`])(?P<path>[^\"'`]*)\3",
    re.IGNORECASE,
)
# Skip obvious non-route call sites (http client libs etc.)
_EXPRESS_SKIP_OBJ = {"axios", "fetch", "http", "https", "request", "got", "supertest"}


def scan_js(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    eps: list = []
    for m in _EXPRESS_RE.finditer(txt):
        obj = m.group(1).lower()
        if obj in _EXPRESS_SKIP_OBJ:
            continue
        p = m.group("path")
        # require it to look like a route (starts with / or is empty-ish)
        if p and not p.startswith("/"):
            continue
        ep = make_ep(m.group(2), p or "/", source=f"{src}:{line_of(txt, m.start())}")
        if ep:
            eps.append(ep)
    return eps


# Next.js App Router: app/**/route.{ts,js} exporting GET/POST/... ; infer path from dir.
_NEXT_EXPORT_RE = re.compile(
    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
)
_NEXT_CONST_RE = re.compile(
    r"export\s+const\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
)


def _next_route_path(path: str) -> str:
    """Infer the URL path from an app-router file path. e.g.
    app/api/users/[id]/route.ts -> /api/users/{id}"""
    r = rel(path).replace(os.sep, "/")
    # strip leading src/ then locate the app dir segment
    parts = r.split("/")
    if "app" in parts:
        parts = parts[parts.index("app") + 1:]
    # drop the filename (route.ts)
    if parts and parts[-1].lower().startswith("route."):
        parts = parts[:-1]
    seg: list[str] = []
    for p in parts:
        if not p:
            continue
        # route groups (group) are not part of the URL
        if p.startswith("(") and p.endswith(")"):
            continue
        if p.startswith("[") and p.endswith("]"):
            inner = p[1:-1].lstrip(".")  # [...slug] / [[...slug]]
            seg.append("{" + inner + "}")
        else:
            seg.append(p)
    return "/" + "/".join(seg) if seg else "/"


def scan_next_route(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    url_path = _next_route_path(path)
    eps: list = []
    seen = set()
    for rx in (_NEXT_EXPORT_RE, _NEXT_CONST_RE):
        for m in rx.finditer(txt):
            verb = m.group(1).upper()
            if verb in seen:
                continue
            seen.add(verb)
            ep = make_ep(verb, url_path, source=f"{src}:{line_of(txt, m.start())}")
            if ep:
                eps.append(ep)
    return eps


# Django urls.py:  path('users/', ...)  re_path(r'^x$', ...)  url(...)
_DJANGO_RE = re.compile(
    r"\b(?:re_)?path\s*\(\s*([\"'])(?P<path>[^\"']*)\1|\burl\s*\(\s*([\"'])(?P<path2>[^\"']*)\3",
)


def scan_django(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    eps: list = []
    for m in _DJANGO_RE.finditer(txt):
        raw = m.group("path") or m.group("path2") or ""
        raw = raw.lstrip("^").rstrip("$")
        if not raw and raw != "":
            continue
        # Django routes don't carry a verb at the urlconf level.
        ep = make_ep("GET", "/" + raw.lstrip("/"),
                     source=f"{src}:{line_of(txt, m.start())}",
                     summary="(django urlconf; verb unknown)")
        if ep:
            eps.append(ep)
    return eps


# Go: r.Get("/x", h)  router.GET("/y", h)  mux.HandleFunc("/z", h)  e.POST(...)
_GO_VERB_RE = re.compile(
    r"\.\s*(Get|Post|Put|Patch|Delete|Head|Options|GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s*\(\s*\"(?P<path>[^\"]*)\"",
)
_GO_HANDLEFUNC_RE = re.compile(
    r"\.\s*(?:HandleFunc|Handle)\s*\(\s*\"(?P<path>[^\"]*)\"",
)
_GO_METHODS_RE = re.compile(
    r"\.\s*Methods\s*\(\s*\"(?P<m>[^\"]*)\"",
)


def scan_go(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    eps: list = []
    for m in _GO_VERB_RE.finditer(txt):
        ep = make_ep(m.group(1), m.group("path"),
                     source=f"{src}:{line_of(txt, m.start())}")
        if ep:
            eps.append(ep)
    for m in _GO_HANDLEFUNC_RE.finditer(txt):
        # mux-style: verb often set via .Methods("GET") on the same chain — best
        # effort: peek the rest of the line.
        ln_start = txt.rfind("\n", 0, m.start()) + 1
        ln_end = txt.find("\n", m.end())
        line = txt[ln_start: ln_end if ln_end > 0 else len(txt)]
        verb = "GET"
        vm = _GO_METHODS_RE.search(line)
        if vm:
            verb = vm.group("m")
        ep = make_ep(verb, m.group("path"),
                     source=f"{src}:{line_of(txt, m.start())}")
        if ep:
            eps.append(ep)
    return eps


# Rails config/routes.rb:  get '/x', to: ...  resources :users  post 'y'
_RAILS_VERB_RE = re.compile(
    r"^\s*(get|post|put|patch|delete)\s+[\"']([^\"']+)[\"']",
    re.IGNORECASE | re.MULTILINE,
)
_RAILS_RESOURCES_RE = re.compile(
    r"^\s*resources?\s+:(\w+)",
    re.IGNORECASE | re.MULTILINE,
)


def scan_rails(path: str) -> list:
    txt = read_text(path)
    if not txt:
        return []
    src = rel(path)
    eps: list = []
    for m in _RAILS_VERB_RE.finditer(txt):
        ep = make_ep(m.group(1), m.group(2),
                     source=f"{src}:{line_of(txt, m.start())}")
        if ep:
            eps.append(ep)
    for m in _RAILS_RESOURCES_RE.finditer(txt):
        name = m.group(1)
        ln = f"{src}:{line_of(txt, m.start())}"
        # the conventional REST 7 for a resources :name declaration
        for verb, p, summ in (
            ("GET", f"/{name}", "index"),
            ("POST", f"/{name}", "create"),
            ("GET", f"/{name}/{{id}}", "show"),
            ("PATCH", f"/{name}/{{id}}", "update"),
            ("PUT", f"/{name}/{{id}}", "update"),
            ("DELETE", f"/{name}/{{id}}", "destroy"),
        ):
            ep = make_ep(verb, p, source=ln, summary=f"rails resources :{name} ({summ})")
            if ep:
                eps.append(ep)
    return eps


def detect_and_scan() -> tuple[str, list, list[str]]:
    """Return (framework, endpoints, route_files_for_llm_fallback)."""
    framework = "unknown"
    eps: list = []
    route_files: list[str] = []

    # --- Python (FastAPI / Flask / Django) ---
    py_files = list(walk_files(exts=(".py",)))
    py_eps: list = []
    has_fastapi = has_flask = False
    for f in py_files:
        head = read_text(f, 4000)
        low = head.lower()
        got = scan_python(f)
        if got:
            py_eps.extend(got)
            route_files.append(f)
        if "fastapi" in low or "apirouter" in low:
            has_fastapi = True
        if "from flask" in low or "import flask" in low:
            has_flask = True
    dj_eps: list = []
    for f in py_files:
        if os.path.basename(f).lower() in ("urls.py",):
            got = scan_django(f)
            if got:
                dj_eps.extend(got)
                route_files.append(f)
    if py_eps:
        eps.extend(py_eps)
        framework = "fastapi" if has_fastapi else ("flask" if has_flask else "python")
    if dj_eps:
        eps.extend(dj_eps)
        if framework == "unknown":
            framework = "django"

    # --- Next.js App Router (app/**/route.{ts,ts,js}) ---
    next_eps: list = []
    for f in walk_files(exts=(".ts", ".tsx", ".js", ".jsx", ".mjs")):
        base = os.path.basename(f).lower()
        if base.startswith("route.") and ("/app/" in f.replace(os.sep, "/") or
                                          f.replace(os.sep, "/").split("/")[-3:][0] == "app" or
                                          "app" in rel(f).replace(os.sep, "/").split("/")):
            got = scan_next_route(f)
            if got:
                next_eps.extend(got)
                route_files.append(f)
    if next_eps:
        eps.extend(next_eps)
        if framework == "unknown":
            framework = "nextjs"
        elif "next" not in framework:
            framework = framework + "+nextjs"

    # --- Express / generic JS routers ---
    js_eps: list = []
    js_route_files: list[str] = []
    for f in walk_files(exts=(".ts", ".js", ".mjs", ".tsx", ".jsx")):
        base = os.path.basename(f).lower()
        if base.startswith("route."):
            continue  # handled by next-router pass
        got = scan_js(f)
        if got:
            js_eps.extend(got)
            js_route_files.append(f)
    if js_eps:
        eps.extend(js_eps)
        js_route_files = js_route_files[:30]
        route_files.extend(js_route_files)
        if framework == "unknown":
            framework = "express"

    # --- Go ---
    go_eps: list = []
    for f in walk_files(exts=(".go",)):
        got = scan_go(f)
        if got:
            go_eps.extend(got)
            route_files.append(f)
    if go_eps:
        eps.extend(go_eps)
        if framework == "unknown":
            framework = "go"

    # --- Rails ---
    rails_eps: list = []
    for f in walk_files(names=("routes.rb",)):
        got = scan_rails(f)
        if got:
            rails_eps.extend(got)
            route_files.append(f)
    if rails_eps:
        eps.extend(rails_eps)
        if framework == "unknown":
            framework = "rails"

    return framework, eps, route_files


# ---------------------------------------------------------------------------
# Tier 3: Claude pass over the most relevant route files
# ---------------------------------------------------------------------------

LLM_CHAR_CAP = 60_000


def llm_extract(route_files: list[str], framework: str) -> list:
    if not ANTHROPIC_KEY or not route_files:
        return []
    # Rank: prefer files whose name screams "routes/api/controller".
    def score(f: str) -> int:
        n = rel(f).lower()
        s = 0
        for kw in ("route", "api", "controller", "endpoint", "url", "handler", "view"):
            if kw in n:
                s += 2
        return s

    ranked = sorted(set(route_files), key=score, reverse=True)
    blob_parts: list[str] = []
    total = 0
    for f in ranked:
        body = read_text(f, 8000)
        if not body:
            continue
        piece = f"\n\n===== FILE: {rel(f)} =====\n{body}"
        if total + len(piece) > LLM_CHAR_CAP:
            break
        blob_parts.append(piece)
        total += len(piece)
    if not blob_parts:
        return []

    system = (
        "You extract HTTP API endpoints from source code. Output ONLY a JSON array, "
        "no prose, no code fences. Each item: "
        '{"method","path","summary","params","auth"}. method is the HTTP verb '
        "(uppercase). path is the route path (use {name} for path params). summary "
        "is a short phrase of what it does. params is a list of param names "
        "(path/query/body) you can see. auth is the auth scheme name if the route is "
        "protected, else null. Only include real HTTP routes."
    )
    prompt = (
        f"Framework: {framework}\nExtract every HTTP endpoint from these files:\n"
        + "".join(blob_parts)
    )
    data = claude_json_list(system, prompt)
    eps: list = []
    for d in data:
        if not isinstance(d, dict):
            continue
        params = d.get("params")
        if isinstance(params, list):
            params = [str(p) for p in params]
        else:
            params = []
        auth = d.get("auth")
        auth = str(auth) if auth not in (None, "", "null", "none", "None") else None
        ep = make_ep(
            str(d.get("method", "")),
            str(d.get("path", "")),
            source="(llm)",
            summary=str(d.get("summary", ""))[:200],
            params=params,
            auth=auth,
        )
        if ep:
            eps.append(ep)
    return eps


# ---------------------------------------------------------------------------
# base_url_hint inference (README / config)
# ---------------------------------------------------------------------------

_BASEPATH_RE = re.compile(
    r"(?:base[_\s-]?url|baseURL|API_BASE|api_base|api_url|root[_\s-]?path|basePath)\s*[:=]\s*[\"']?(https?://[^\s\"'`,)]+|/[^\s\"'`,)]+)",
    re.IGNORECASE,
)


def infer_base_url() -> str | None:
    candidates: list[str] = []
    for cand in ("README.md", "Readme.md", "readme.md", "README"):
        p = os.path.join(REPO_DIR, cand)
        if os.path.exists(p):
            txt = read_text(p, 8000)
            m = re.search(r"https?://[a-z0-9.\-]+\.[a-z]{2,}(?:/[^\s)\]\">]*)?", txt, re.IGNORECASE)
            if m:
                candidates.append(m.group(0).rstrip(".,);"))
            break
    # config-ish files for a declared base path
    for f in list(walk_files(names=(
        "next.config.js", "next.config.ts", "next.config.mjs",
        "vercel.json", "app.json", ".env.example", "config.py", "settings.py",
    )))[:20]:
        m = _BASEPATH_RE.search(read_text(f, 6000))
        if m:
            candidates.append(m.group(1))
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_output(out: dict) -> None:
    try:
        # enforce a hard payload cap (defensive — endpoints already capped)
        blob = json.dumps(out)
        if len(blob) > PAYLOAD_CAP:
            out["endpoints"] = out.get("endpoints", [])[:100]
            blob = json.dumps(out)
        with open("/tmp/code_output.json", "w", encoding="utf-8") as fh:
            fh.write(blob)
    except Exception as e:  # noqa: BLE001
        print(f"code_driver: failed to write output.json: {e}")


def main() -> None:
    out: dict = {
        "commit_sha": "",
        "branch": "",
        "framework": "unknown",
        "endpoints": [],
        "base_url_hint": None,
        "scanned_files": 0,
        "error": None,
    }

    if not CLONE_URL or not REPO_FULL:
        out["error"] = "missing clone url or repo"
        write_output(out)
        print("code_driver: missing args")
        return

    if not ensure_git():
        out["error"] = "git unavailable in sandbox"
        write_output(out)
        print("code_driver: git unavailable")
        return

    # 1) clone --------------------------------------------------------------
    sh("rm", "-rf", REPO_DIR, timeout=60)
    rc, log = sh("git", "clone", "--depth", "1", CLONE_URL, REPO_DIR, timeout=300)
    if rc != 0:
        out["error"] = "clone failed: " + (log[-300:] if log else "unknown")
        write_output(out)
        print("code_driver: clone failed")
        return

    # 1b) checkout a specific ref if requested ------------------------------
    if REF:
        # depth-1 clone may not contain the ref; fetch it first (best effort).
        sh("git", "fetch", "--depth", "1", "origin", REF, cwd=REPO_DIR, timeout=180)
        rc, _ = sh("git", "checkout", REF, cwd=REPO_DIR, timeout=120)
        if rc != 0:
            # try the fetched FETCH_HEAD
            sh("git", "checkout", "FETCH_HEAD", cwd=REPO_DIR, timeout=120)

    # 2) record commit + branch --------------------------------------------
    rc, sha = sh("git", "rev-parse", "HEAD", cwd=REPO_DIR, timeout=30)
    out["commit_sha"] = sha.strip() if rc == 0 and not sha.startswith("__err__") else ""
    rc, head = sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=REPO_DIR, timeout=30)
    branch = head.strip() if rc == 0 and not head.startswith("__err__") else ""
    if branch in ("", "HEAD"):
        branch = REF or "main"
    out["branch"] = branch

    # 3) extract endpoints --------------------------------------------------
    all_eps: list = []
    base_hint: str | None = None
    scanned = 0

    # Tier 1: committed OpenAPI/Swagger doc(s)
    openapi_eps: list = []
    for f in walk_files(names=OPENAPI_NAMES):
        scanned += 1
        got, bh = parse_openapi(f)
        if got:
            openapi_eps.extend(got)
            if bh and not base_hint:
                base_hint = bh
    framework = "unknown"
    if openapi_eps:
        framework = "openapi"
        all_eps.extend(openapi_eps)

    # Tier 2: framework route detection from source
    fw_detected, route_eps, route_files = detect_and_scan()
    scanned += len(route_files)
    if route_eps:
        all_eps.extend(route_eps)
        # prefer the concrete framework name over the generic "openapi" label
        framework = fw_detected if framework == "unknown" else (
            f"{framework}+{fw_detected}" if fw_detected != "unknown" else framework
        )
    elif framework == "unknown":
        framework = fw_detected

    # Tier 3: thin parse + we found route files -> one Claude pass to augment.
    # "thin" = no endpoints at all, or far fewer than the number of route files.
    thin = len(route_eps) == 0 or (route_files and len(route_eps) < max(2, len(route_files)))
    if thin and route_files and ANTHROPIC_KEY:
        llm_eps = llm_extract(route_files, framework)
        if llm_eps:
            all_eps.extend(llm_eps)

    out["endpoints"] = dedupe(all_eps)[:MAX_ENDPOINTS]
    out["framework"] = framework
    out["scanned_files"] = scanned

    # 4) base_url_hint ------------------------------------------------------
    if not base_hint:
        base_hint = infer_base_url()
    out["base_url_hint"] = base_hint

    write_output(out)
    print(
        f"code_driver: {framework} | {len(out['endpoints'])} endpoints | "
        f"commit {out['commit_sha'][:8]} | scanned {scanned} files"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — never raise out of the driver
        try:
            write_output(
                {
                    "commit_sha": "", "branch": "", "framework": "unknown",
                    "endpoints": [], "base_url_hint": None, "scanned_files": 0,
                    "error": f"driver crashed: {e}",
                }
            )
        except Exception:
            pass
        print(f"code_driver: crashed: {e}")
