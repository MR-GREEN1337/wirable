# Wirable

**Can an AI agent actually use your product? Wirable finds out, then fixes it.**

Paris Builds 2026 — software for agents.

Wirable is a black-box service that (1) tests whether an autonomous AI agent can complete real workflows on any product, (2) scores it 0–100 across six deterministic dimensions, (3) hosts an MCP server *in front of* the unchanged product that repairs the semantic breakage, and (4) proves the fix by re-running the exact same audit through the proxy and reporting the before/after delta. No code access. Nothing installed on the target's side.

```
URL ──▶  AUDIT  ──▶  SCORE  ──▶  PROXY (the fix)  ──▶  VERIFY
       3 agents      0–100      hosted MCP server      40 → 85
       (sandboxes)   6 dims     (no code change)       (measured)
```

---

## Why this exists

Almost every SaaS product was built for a human clicking a UI. An agent needs something different: a stable machine entrypoint, machine-readable errors it can branch on, retry-safe (idempotent) writes, deterministic auth that doesn't strand it in a human OAuth dance, and discoverable docs. When those are missing, the agent doesn't get a clean "no" — it gets an ambiguous 200, a CAPTCHA, or a wall of HTML, and it fails silently.

Wirable measures that gap honestly, then closes it without asking the product team to ship anything.

---

## The four stages

### 1. Audit — *can an agent use this?*

`POST /api/v1/run {url}` spins up **three independent agents in parallel**, each in its own isolated [Daytona](https://daytona.io) sandbox. Each agent drives a real headless browser (`agent-browser`) using the page's accessibility tree **plus a vision screenshot** (set-of-marks grounding), and also has a `bash` tool and a **skill library** (signup, login, call-endpoint, provoke-error, test-idempotency, connect-mcp, clone-repo, scan-routes, find-openapi, …) so it can act like a real agent, not just scrape.

Each agent walks the product the way an agent would — find the machine surface (llms.txt / OpenAPI / MCP), read the docs and API reference, locate auth, attempt the core action, trigger an error, retry a write — and emits a live screenshot stream you watch in the run cockpit.

**CATTS consensus.** All three agents run the full (deep) walk and vote independently. For each dimension the verdicts are combined by **majority vote**; on a close split a Claude **arbiter** adjudicates using the pooled evidence (`catts_aggregate_with_arbiter`). This is what makes a score robust to a single agent hallucinating a pass or fail. Each agent pulls its own Claude key from a pool so the fan-out doesn't trip per-key rate limits.

### 2. Score — *six deterministic dimensions*

| Dimension | Weight | What it asks |
|---|---|---|
| `api_surface` | 20 | Is there a programmatic surface an agent can call? |
| `auth` | 20 | Can an agent authenticate deterministically (key/token, not human-only OAuth/CAPTCHA)? |
| `mcp_availability` | 20 | Is there an MCP endpoint / machine manifest? |
| `error_quality` | 15 | Are errors machine-readable (stable code + retryable flag)? |
| `idempotency` | 15 | Are mutating operations safe to retry? |
| `docs` | 10 | Are agent-facing docs discoverable (llms.txt / OpenAPI)? |

The score is the sum of passed dimension weights — fully deterministic given the agents' verdicts.

### 3. Proxy — *the fix, with no code change*

`POST /api/v1/run/{id}/proxy` generates and **hosts a real MCP server that sits in front of the unchanged product**. It is served at:

```
https://wirable.dev/api/v1/proxy/<run_id>/mcp
```

(The Next.js frontend rewrites `/api/v1/*` to the backend, so that public URL is the live MCP endpoint an agent connects its client to.)

The proxy is **grounded in the target's real interface**: if the product publishes an OpenAPI spec (or its repo is bound), Wirable maps the actual endpoints into typed MCP tools; otherwise it synthesizes tools from what the audit discovered. It then fixes the exact things the audit flagged:

- **MCP availability** — it *is* a spec-compliant MCP endpoint (protocol `2025-06-18`: `initialize`, `tools/list`, `tools/call`, Streamable-HTTP, bearer auth + `.well-known/oauth-protected-resource`).
- **Error quality** — every upstream response is normalized to `{success, error_code, retryable}`.
- **Idempotency** — `Idempotency-Key` is enforced on mutating tools.
- **Auth** — the owner's API key is stored **server-side** and injected on each call, so the agent never sees a secret.
- **Docs** — it serves `llms.txt` and `.well-known/mcp.json` so agents discover it.

One click adds it to Cursor (deep link) or copies the config for any MCP client.

### 4. Verify — *prove it, don't claim it*

`verify_against_proxy` takes the **before** score (the original audit) and re-runs the **same deterministic rubric through the live proxy** — including a real read-only `tools/call` to confirm the MCP is reachable and working — then reports the **after** score. The delta (e.g. `40 → 85`) is a measurement, not marketing. The proxy is strictly additive, so it never reports a regression.

---

## The GitHub fix (Pro)

If the owner connects a repo, Wirable also runs an agentic fix harness in a sandbox: it clones the repo, generates grounded `llms.txt`, `AGENTS.md`, `CLAUDE.md`, `docs/agent-readiness.md`, and `.well-known/mcp.json` (referencing the hosted MCP), commits on a branch, pushes, and opens a PR — streaming each step live onto the run page. Wirable hosts the MCP; the PR only *references* it.

## The registry

`/registry` is a public directory of products Wirable has tested that have a live hosted MCP — each with its real score and a one-click "Add to Cursor" / copy-config. Backed by `GET /api/v1/registry` (self/test domains and unscored entries are filtered out).

---

## Architecture

```
Next.js 15 (App Router, Lyra design system)
   │  /api/v1/* rewrite ──▶  FastAPI backend
   │                           ├─ orchestrator → test_service (3 agents)
   │                           │     └─ Daytona sandboxes: agent-browser + skills + bash
   │                           │     └─ CATTS consensus + Claude arbiter
   │                           ├─ proxy_generator → proxy_runtime (hosted MCP-over-HTTP)
   │                           ├─ verification_service (before/after)
   │                           ├─ github_harness_fix (clone → PR, streamed)
   │                           └─ entitlements / billing (Stripe)
   └─ Postgres (companies / audits / mcp / clients / users)
```

- **Agents / LLM:** Anthropic Claude (audit reasoning, arbiter, tool descriptions), key pool for fan-out.
- **Sandboxes:** Daytona declarative snapshots (`WIRABLE_SANDBOX_IMAGE`).
- **Auth:** email/password (bcrypt) + Google OAuth + guest, JWT bearer; Cloudflare Turnstile on signup.
- **Billing:** Stripe Checkout (Pro = host the proxy + open the GitHub PR); the audit is always free. Access codes grant unlimited.
- **Email:** Resend (welcome). **Observability:** Sentry + PostHog.
- **Deploy:** Hetzner box, `docker-compose`, Traefik TLS for `wirable.dev`.

## Key endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/run` | Start an audit for a URL → `{run_id}` |
| `GET` | `/api/v1/run/{id}/stream` | SSE of run events (frames, lines, score) |
| `GET` | `/api/v1/run/{id}/state?cursor=N` | Poll the same events as JSON |
| `POST` | `/api/v1/run/{id}/proxy` | Generate → host → verify the MCP proxy (Pro) |
| `POST` | `/api/v1/run/{id}/fix` | Open the agent-ready GitHub PR (Pro) |
| `POST/GET` | `/api/v1/proxy/{id}/mcp` | The hosted MCP endpoint (JSON-RPC: initialize / tools/list / tools/call) |
| `GET` | `/api/v1/registry` | Public directory of hosted MCPs |
| `GET` | `/api/v1/dashboard` | The signed-in user's audited targets |
| `POST` | `/api/v1/billing/checkout` | Stripe Checkout session (Pro) |

## Repo layout

```
backend/
  src/
    api/v1/endpoints/   run, proxy, dashboard, access(+billing), auth, github
    services/           orchestrator, test_service, proxy_generator, proxy_runtime,
                        verification_service, recon, code_analysis, github_harness_fix,
                        entitlements, email, turnstile, score_service, mcp_monitor
    agents/catts.py     consensus aggregation + arbiter
    harness/            audit_driver.py, fix_driver.py, skills.py  (uploaded into sandboxes)
    core/               config, auth, database, sandbox/daytona_client, llm/key_pool
  scripts/              seed_registry.py, diag_frames.py, build_snapshot.py
web/
  src/app/              (marketing), (dashboard), run/[id], access, registry, signin, terms
  src/components/run/   AgentGrid, LiveAgentViewport, ProxyPanel, FixWithGithub, ...
```

## Running locally

**Backend** (Python 3.12):
```bash
cd backend && uv sync
# .env: DATABASE_URL, ANTHROPIC_API_KEYS, DAYTONA_API_KEY, JWT_SECRET, APP_BASE_URL,
#       STRIPE_SECRET_KEY/STRIPE_PRICE_ID, RESEND_API_KEY/WIRABLE_EMAIL_FROM,
#       TURNSTILE_SECRET_KEY, SENTRY_DSN, POSTHOG_KEY, WIRABLE_SANDBOX_IMAGE
uvicorn src.main:app --reload
```

**Frontend** (Node):
```bash
cd web && npm install && npm run dev
# NEXT_PUBLIC_BACKEND_URL (or the /api/v1 rewrite via BACKEND_URL), and the
# NEXT_PUBLIC_* keys for Turnstile / Sentry / PostHog
```

Key tunables: `WIRABLE_MAX_STEPS` (audit depth, default 20), `WIRABLE_REQUIRE_AUTH`, `WIRABLE_UNLIMITED_CODES` / `WIRABLE_ACCESS_CODES`, `WIRABLE_SANDBOX_IMAGE`.

## Seeding / diagnostics

- `python -m scripts.seed_registry "<url>[|openapi_spec_url[|api_base]]" ...` — run real audits + host MCPs to populate the registry (grounds tools in real specs when available).
- `python -m scripts.diag_frames <url>` — run the harness in a sandbox and dump every captured frame (byte size + caption) to debug the live screenshot stream.

---

*Wirable hosts the bridge so agents can use a product **today**, while the PR makes it natively agent-ready for tomorrow.*
