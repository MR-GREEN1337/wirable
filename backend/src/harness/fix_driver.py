#!/usr/bin/env python3
"""
Wirable FIX harness driver — runs INSIDE the sandbox.

Unlike the REST file-drop (github_fix.py over the Contents API), this driver does
REAL git work: it clones the user's connected repo with a token-authenticated URL,
inspects the codebase, asks Claude for grounded agent-readiness changes, writes the
agent-ready files into the working tree, commits them on a branch, and pushes the
branch back to origin. The orchestrator then opens the PR from the pushed branch.

argv: <authed_clone_url> <repo_full_name> <target_url> <audit_json_path>
  authed_clone_url already embeds the token:
    https://x-access-token:<token>@github.com/owner/repo.git

Writes /tmp/fix_output.json:
  {branch, files:[...], commit, pushed:bool, default_branch, summary, diff?, error?}

`diff` is the unified diff of the agent-ready changes (the staged tree vs HEAD,
captured before the commit), capped so the SSE payload stays small. It is omitted
(empty) when no diff could be produced so the frontend can degrade gracefully.

NEVER raises — all failures are captured into the json so the orchestrator can
fall back to the REST path and still deliver a PR.

stdlib + subprocess(git) only — this runs in the sandbox, no project deps.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

CLONE_URL = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
REPO_FULL = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
TARGET_URL = (sys.argv[3] if len(sys.argv) > 3 else "").strip()
AUDIT_PATH = (sys.argv[4] if len(sys.argv) > 4 else "").strip()

REPO_DIR = "/tmp/repo"
BRANCH = "wirable/agent-ready"
DIFF_CAP = 60_000  # cap the unified diff so the SSE payload stays small
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MCP_URL = os.environ.get("WIRABLE_MCP_URL", "").strip()

DIM_LABELS = {
    "api_surface": "Programmatic API surface",
    "auth": "Agent-deterministic auth",
    "error_quality": "Machine-readable errors",
    "idempotency": "Safe retries / idempotency",
    "mcp_availability": "MCP availability",
    "docs": "Agent-facing docs",
}
DIM_ORDER = list(DIM_LABELS.keys())

# Live progress: the orchestrator (github_harness_fix) tails this file while the
# driver runs and streams each line onto the run's SSE bus, so the user watches
# the GitHub fix agent work in real time on the run page.
PROGRESS_PATH = "/tmp/fix_progress.log"


def step(msg: str) -> None:
    """Append a progress line (tailed live) + print it. Never raises."""
    line = msg.rstrip()
    try:
        with open(PROGRESS_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        print(line, flush=True)
    except Exception:  # noqa: BLE001
        pass


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
    # Try to install git (sandbox may be a minimal image).
    sh("sudo", "apt-get", "update", "-y", timeout=180)
    sh("sudo", "apt-get", "install", "-y", "git", timeout=300)
    rc, _ = sh("git", "--version", timeout=30)
    return rc == 0


def claude(system: str, prompt: str, max_tokens: int = 1400) -> str:
    if not ANTHROPIC_KEY:
        return ""
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": max_tokens,
            "temperature": 0.2,
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
        resp = json.load(urllib.request.urlopen(req, timeout=90))
        return "".join(
            b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict)
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def read_text(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except Exception:  # noqa: BLE001
        return ""


def detect_framework(top: list[str]) -> str:
    names = {n.lower() for n in top}
    hints = []
    if "package.json" in names:
        hints.append("node/npm")
        pkg = read_text(os.path.join(REPO_DIR, "package.json"), 3000)
        if '"next"' in pkg:
            hints.append("next.js")
        if '"react"' in pkg:
            hints.append("react")
        if '"express"' in pkg:
            hints.append("express")
        if '"fastify"' in pkg:
            hints.append("fastify")
    if "next.config.js" in names or "next.config.ts" in names or "next.config.mjs" in names:
        if "next.js" not in hints:
            hints.append("next.js")
    if "pyproject.toml" in names:
        hints.append("python/pyproject")
        py = read_text(os.path.join(REPO_DIR, "pyproject.toml"), 3000)
        if "fastapi" in py.lower():
            hints.append("fastapi")
        if "django" in py.lower():
            hints.append("django")
        if "flask" in py.lower():
            hints.append("flask")
    if "requirements.txt" in names:
        hints.append("python/requirements")
        rq = read_text(os.path.join(REPO_DIR, "requirements.txt"), 2000).lower()
        for fw in ("fastapi", "django", "flask"):
            if fw in rq and fw not in hints:
                hints.append(fw)
    if "go.mod" in names:
        hints.append("go")
    if "cargo.toml" in names:
        hints.append("rust")
    return ", ".join(dict.fromkeys(hints)) or "unknown"


def load_audit() -> tuple[list[dict], list[dict], dict]:
    """Return (dims_as_list, cards, raw). dims as [{dim,passed,evidence}]."""
    raw: dict = {}
    if AUDIT_PATH:
        txt = read_text(AUDIT_PATH, 40000)
        if txt:
            try:
                raw = json.loads(txt)
            except Exception:  # noqa: BLE001
                raw = {}
    dims_in = raw.get("dimensions")
    dims: list[dict] = []
    if isinstance(dims_in, dict):
        for k, v in dims_in.items():
            v = v if isinstance(v, dict) else {}
            dims.append(
                {
                    "dim": k,
                    "passed": bool(v.get("passed", False)),
                    "evidence": str(v.get("evidence", "") or ""),
                }
            )
    elif isinstance(dims_in, list):
        for d in dims_in:
            if isinstance(d, dict):
                dims.append(
                    {
                        "dim": d.get("dim") or d.get("key") or "",
                        "passed": bool(d.get("passed", False)),
                        "evidence": str(d.get("evidence", "") or ""),
                    }
                )
    cards = raw.get("cards") if isinstance(raw.get("cards"), list) else []
    return dims, cards, raw


def score_from_dims(dims: list[dict]) -> int:
    weights = {
        "api_surface": 20, "auth": 20, "error_quality": 15,
        "idempotency": 15, "mcp_availability": 20, "docs": 10,
    }
    return sum(weights.get(d.get("dim", ""), 0) for d in dims if d.get("passed"))


def product_name() -> str:
    host = (TARGET_URL or REPO_FULL).split("//")[-1].split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    label = host.split(".")[0] if host else (REPO_FULL.split("/")[-1] or "This product")
    return label.capitalize() if label else "This product"


# ---------------------------------------------------------------------------
# File-content generation (Claude with deterministic fallback)
# ---------------------------------------------------------------------------

def gen_files(framework: str, readme: str, top: list[str], dims: list[dict], cards: list[dict]) -> dict[str, str]:
    failed = [DIM_LABELS.get(d["dim"], d["dim"]) for d in dims if not d.get("passed")]
    suggestions = [
        f"Fix: {lbl}" for lbl in failed
    ] or ["Maintain agent-usable endpoints (structured errors, idempotency, llms.txt)."]

    name = product_name()
    mcp_line = MCP_URL or "(Wirable hosted MCP endpoint, set at deploy)"

    # Ask Claude for grounded llms.txt + AGENTS.md bodies; fall back to templates.
    step("writing llms.txt — the agent-facing index…")
    llms = _gen_llms(name, framework, dims) or _tpl_llms(name, mcp_line)
    step("writing AGENTS.md — the coding-agent guide…")
    agents = _gen_agents(name, framework, top, suggestions) or _tpl_agents(name, mcp_line, suggestions)
    step("writing CLAUDE.md — repo instructions for Claude Code…")
    claude_md = _gen_claude_md(name, framework, suggestions) or _tpl_claude(name, mcp_line, suggestions)
    step("writing docs/agent-readiness.md — the audit report…")
    docs = _tpl_docs(dims, cards, suggestions)  # deterministic report
    step("writing .well-known/mcp.json — MCP discovery manifest…")
    wellknown = json.dumps(_wellknown(name), indent=2) + "\n"

    return {
        "llms.txt": llms.rstrip() + "\n",
        "AGENTS.md": agents.rstrip() + "\n",
        "CLAUDE.md": claude_md.rstrip() + "\n",
        "docs/agent-readiness.md": docs,
        ".well-known/mcp.json": wellknown,
    }


def _gen_llms(name: str, framework: str, dims: list[dict]) -> str:
    system = (
        "You write llms.txt files (llmstxt.org format): an H1 product name, a "
        "one-line blockquote summary, then H2 sections. Concise, factual. Write "
        "plainly. Do not use em-dashes. Avoid marketing buzzwords and AI-cliche "
        "phrasing. Output ONLY the markdown file content, no fences, no commentary."
    )
    prompt = (
        f"Product: {name}\nURL: {TARGET_URL}\nStack: {framework}\n"
        f"Hosted MCP endpoint agents call: {MCP_URL or '(none configured)'}\n\n"
        "Write an llms.txt that names the product, gives a one-line summary, has a "
        "'## Capabilities' section (infer from the stack), a '## MCP' section "
        "stating the endpoint URL + transport http + that agents authenticate with "
        "'Authorization: Bearer <agent-key>' (omit the MCP section if no endpoint), "
        "and a '## How agents should use this' section. Under 40 lines."
    )
    return claude(system, prompt, max_tokens=800)


def _gen_agents(name: str, framework: str, top: list[str], suggestions: list[str]) -> str:
    system = (
        "You write AGENTS.md files that coding agents load when working in a "
        "repository. Directive, concise, repo-specific. Write plainly. Do not use "
        "em-dashes. Avoid marketing buzzwords and AI-cliche phrasing. Output ONLY markdown, no fences."
    )
    checklist = "\n".join(f"- [ ] {s}" for s in suggestions)
    prompt = (
        f"Repo: {REPO_FULL}\nStack: {framework}\nTop-level: {', '.join(top[:25])}\n"
        f"Hosted MCP server: {MCP_URL or '(none)'}\n\n"
        "Write an AGENTS.md telling coding agents working IN THIS repo to keep the "
        "product agent-usable: return structured machine-readable errors with stable "
        "codes + a retryable flag, support an Idempotency-Key header on mutating "
        "endpoints, and list every endpoint in llms.txt. Reference the hosted MCP "
        "server if one is configured. Then include this exact improvement checklist "
        "verbatim under a '## Make it natively agent-ready' heading:\n" + checklist
    )
    return claude(system, prompt, max_tokens=1000)


def _gen_claude_md(name: str, framework: str, suggestions: list[str]) -> str:
    system = (
        "You write CLAUDE.md — instructions Claude Code loads for THIS repo. "
        "Short, imperative, repo-specific. Write plainly. Do not use em-dashes. "
        "Avoid marketing buzzwords and AI-cliche phrasing. Output ONLY markdown, no fences."
    )
    checklist = "\n".join(f"- [ ] {s}" for s in suggestions)
    prompt = (
        f"Repo: {REPO_FULL}\nStack: {framework}\n\n"
        "Write a CLAUDE.md that instructs coding agents to preserve agent-readiness "
        "when changing this codebase (structured errors, idempotency keys, keep "
        "llms.txt current, expose an MCP surface). Keep it under 30 lines. End with "
        "this checklist under '## Agent-readiness checklist':\n" + checklist
    )
    return claude(system, prompt, max_tokens=700)


def _wellknown(name: str) -> dict:
    body: dict = {
        "schema": "mcp-discovery/0.1",
        "name": f"{name} (agent-ready via Wirable)",
        "transport": "http",
    }
    if MCP_URL:
        body["mcp"] = {
            "endpoint": MCP_URL,
            "transport": "http",
            "methods": ["tools/list", "tools/call"],
            "auth": {"type": "bearer", "header": "Authorization"},
        }
    else:
        body["note"] = "Set the Wirable MCP endpoint at deploy time (WIRABLE_MCP_URL)."
    return body


# --- deterministic templates ------------------------------------------------

def _tpl_llms(name: str, mcp_line: str) -> str:
    out = [
        f"# {name}",
        "",
        f"> {name} is now agent-ready via Wirable: an llms.txt index plus a hosted "
        "MCP server with typed tools, normalized errors, and idempotent retries.",
        "",
        "## Product",
        f"- Website: {TARGET_URL or REPO_FULL}",
        "",
    ]
    if MCP_URL:
        out += [
            "## MCP",
            f"- Endpoint: {mcp_line} (transport: http)",
            "- Authentication: send `Authorization: Bearer <agent-key>` on each "
            "`tools/call`. Discover tools with `tools/list`.",
            "",
        ]
    out += [
        "## How agents should use this",
        "- List tools, then call them over MCP-over-HTTP (JSON-RPC).",
        "- Errors are normalized to `{success, error_code, retryable}`. Retry only "
        "when `retryable` is true.",
    ]
    return "\n".join(out)


def _tpl_agents(name: str, mcp_line: str, suggestions: list[str]) -> str:
    checklist = "\n".join(f"- [ ] {s}" for s in suggestions)
    mcp_block = (
        "## Use the MCP server\n"
        f"Agents should use the MCP server at `{mcp_line}` (transport: http). "
        "Authenticate with `Authorization: Bearer <agent-key>`. Use `tools/list` to "
        "discover tools and `tools/call` to invoke them.\n\n"
        if MCP_URL else ""
    )
    return (
        "# AGENTS.md\n\n"
        f"`{name}` is now **agent-ready** via [Wirable](https://wirable).\n\n"
        f"{mcp_block}"
        "## When changing this repo, keep endpoints agent-usable\n"
        "- Return **structured, machine-readable errors**: a stable `error_code`, a "
        "boolean `retryable`, and an actionable message. Never wrap a failure in a 200.\n"
        "- Support **idempotency keys** (`Idempotency-Key` header) on mutating "
        "endpoints so retries don't duplicate side effects.\n"
        "- **Expose new endpoints in `llms.txt`** so agents can discover them.\n\n"
        "## Make it natively agent-ready\n"
        f"{checklist}\n"
    )


def _tpl_claude(name: str, mcp_line: str, suggestions: list[str]) -> str:
    checklist = "\n".join(f"- [ ] {s}" for s in suggestions)
    return (
        "# CLAUDE.md\n\n"
        f"Instructions for coding agents working on `{name}`.\n\n"
        "## Preserve agent-readiness\n"
        "- Keep `llms.txt` current when endpoints change.\n"
        "- Return structured errors (`error_code` + `retryable`), never failures in a 200.\n"
        "- Support `Idempotency-Key` on mutations.\n"
        + (f"- Keep the MCP surface (`{mcp_line}`) in sync.\n" if MCP_URL else "")
        + "\n## Agent-readiness checklist\n"
        + checklist + "\n"
    )


def _tpl_docs(dims: list[dict], cards: list[dict], suggestions: list[str]) -> str:
    score = score_from_dims(dims)
    by_dim = {d.get("dim"): d for d in dims}
    lines = [
        "# Agent-readiness report\n",
        f"Audited target: **{TARGET_URL or REPO_FULL}**  \n"
        "Generated by [Wirable](https://wirable).\n",
        f"\n## Score: {score}/100\n",
        "\n## Dimensions\n",
        "| Dimension | Verdict | Evidence |",
        "| --- | --- | --- |",
    ]
    for key in DIM_ORDER:
        d = by_dim.get(key, {})
        verdict = "PASS" if d.get("passed") else "FAIL"
        evidence = str(d.get("evidence", "") or "—").replace("|", "\\|")[:300]
        lines.append(f"| {DIM_LABELS[key]} | {verdict} | {evidence} |")
    if cards:
        lines.append("\n## Findings\n")
        for c in cards:
            if not isinstance(c, dict):
                continue
            headline = str(c.get("headline", "") or "").strip()
            detail = str(c.get("detail", "") or "").strip()
            if headline or detail:
                lines.append(f"- **{headline}**: {detail}")
    lines.append("\n## Suggested improvements (make it natively agent-ready)\n")
    if suggestions:
        for i, s in enumerate(suggestions, 1):
            lines.append(f"{i}. {s}")
    else:
        lines.append("No gaps found. The product is already agent-ready.")
    lines.append(
        "\n---\n"
        "Until these land, the hosted Wirable MCP proxy bridges the gap so agents "
        "can use this product today.\n"
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_output(out: dict) -> None:
    try:
        with open("/tmp/fix_output.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh)
    except Exception as e:  # noqa: BLE001
        # last resort — print so the orchestrator can scrape it
        print(f"fix_driver: failed to write output.json: {e}")


def main() -> None:
    out: dict = {
        "branch": BRANCH,
        "files": [],
        "commit": "",
        "pushed": False,
        "default_branch": "",
        "summary": "",
        "diff": "",
        "error": None,
    }

    if not CLONE_URL or not REPO_FULL:
        out["error"] = "missing clone url or repo"
        write_output(out)
        print("fix_driver: missing args")
        return

    step("preparing the sandbox (git)…")
    if not ensure_git():
        out["error"] = "git unavailable in sandbox"
        write_output(out)
        step("git unavailable in sandbox — aborting")
        return

    # 1) clone --------------------------------------------------------------
    step(f"cloning {REPO_FULL}…")
    sh("rm", "-rf", REPO_DIR, timeout=60)
    rc, log = sh("git", "clone", "--depth", "1", CLONE_URL, REPO_DIR, timeout=300)
    if rc != 0:
        out["error"] = "clone failed: " + (log[-300:] if log else "unknown")
        write_output(out)
        step("clone failed — aborting")
        return
    sh("git", "config", "user.name", "wirable-bot", cwd=REPO_DIR, timeout=30)
    sh("git", "config", "user.email", "bot@wirable.dev", cwd=REPO_DIR, timeout=30)

    # default branch (from the clone's current HEAD)
    rc, head = sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=REPO_DIR, timeout=30)
    out["default_branch"] = head.strip() if rc == 0 and head.strip() else "main"

    # 2) inspect ------------------------------------------------------------
    try:
        top = sorted(os.listdir(REPO_DIR))
    except Exception:  # noqa: BLE001
        top = []
    top = [t for t in top if t != ".git"]
    framework = detect_framework(top)
    step(f"cloned — stack detected: {framework}")
    readme = ""
    for cand in ("README.md", "Readme.md", "readme.md", "README"):
        p = os.path.join(REPO_DIR, cand)
        if os.path.exists(p):
            readme = read_text(p, 3000)
            break

    # 3) load audit + generate file contents --------------------------------
    dims, cards, _raw = load_audit()
    failing = [DIM_LABELS.get(d["dim"], d["dim"]) for d in dims if not d.get("passed")]
    step(
        f"reading audit findings — {len(failing)} gap(s) to address"
        + (": " + ", ".join(failing[:4]) if failing else "")
    )
    files = gen_files(framework, readme, top, dims, cards)

    written: list[str] = []
    for rel, content in files.items():
        dest = os.path.join(REPO_DIR, rel)
        try:
            os.makedirs(os.path.dirname(dest) or REPO_DIR, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(content)
            written.append(rel)
        except Exception as e:  # noqa: BLE001
            print(f"fix_driver: failed to write {rel}: {e}")
    out["files"] = written
    step(f"wrote {len(written)} file(s): {', '.join(written)}")

    # 4) branch, commit, push ----------------------------------------------
    step(f"committing on branch {BRANCH}…")
    # checkout -B is idempotent (resets the branch if it already exists locally).
    sh("git", "checkout", "-B", BRANCH, cwd=REPO_DIR, timeout=60)
    sh("git", "add", "-A", cwd=REPO_DIR, timeout=60)

    # Capture the unified diff of the staged agent-ready changes (vs HEAD) BEFORE
    # committing — this is exactly what the PR will contain. Cap the size so the
    # SSE payload stays small; omit on any failure so the UI degrades gracefully.
    rc, diff_text = sh(
        "git", "diff", "--cached", "--no-color", cwd=REPO_DIR, timeout=60
    )
    if rc == 0 and diff_text and not diff_text.startswith("__err__"):
        if len(diff_text) > DIFF_CAP:
            diff_text = (
                diff_text[:DIFF_CAP]
                + "\n\n[... diff truncated by Wirable; open the PR for the full diff ...]\n"
            )
        out["diff"] = diff_text

    score = score_from_dims(dims)
    msg = (
        f"chore(wirable): make agent-ready ({score}/100)\n\n"
        "Adds llms.txt, AGENTS.md, CLAUDE.md, docs/agent-readiness.md and "
        ".well-known/mcp.json so autonomous AI agents can use this product.\n\n"
        "Generated by Wirable."
    )
    rc, commit_log = sh("git", "commit", "-m", msg, cwd=REPO_DIR, timeout=60)
    if rc != 0 and "nothing to commit" not in commit_log.lower():
        out["error"] = "commit failed: " + commit_log[-300:]
        write_output(out)
        print("fix_driver: commit failed")
        return
    rc, sha = sh("git", "rev-parse", "HEAD", cwd=REPO_DIR, timeout=30)
    out["commit"] = sha.strip() if rc == 0 else ""

    # push --force-with-lease; idempotent (branch may already exist on origin).
    step(f"pushing {BRANCH} to origin…")
    rc, push_log = sh(
        "git", "push", "-u", "origin", BRANCH, "--force-with-lease",
        cwd=REPO_DIR, timeout=300,
    )
    if rc != 0:
        # retry without lease (lease fails when the remote branch is brand new on
        # some git versions, or when there is no upstream tracking yet)
        rc, push_log = sh(
            "git", "push", "-u", "origin", BRANCH, "--force",
            cwd=REPO_DIR, timeout=300,
        )
    if rc != 0:
        out["pushed"] = False
        out["error"] = "push failed: " + (push_log[-300:] if push_log else "unknown")
        write_output(out)
        step("push failed — aborting")
        return

    out["pushed"] = True
    out["summary"] = (
        f"Pushed {len(written)} agent-ready file(s) to {BRANCH} "
        f"(score {score}/100, stack: {framework})."
    )
    write_output(out)
    step(f"pushed ✓ commit {out['commit'][:8]} — opening pull request…")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 — never raise out of the driver
        try:
            write_output(
                {
                    "branch": BRANCH, "files": [], "commit": "", "pushed": False,
                    "default_branch": "", "summary": "",
                    "error": f"driver crashed: {e}",
                }
            )
        except Exception:
            pass
        print(f"fix_driver: crashed: {e}")
