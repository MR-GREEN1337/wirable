# Wirable

Paris Builds 2026 hackathon submission.

Wirable, internally branded in the app as AgentReady, audits whether a SaaS product is usable by autonomous agents, then generates the missing agent interface and opens a GitHub pull request to fix it.

The core loop is simple:

1. Find SaaS products that are likely blocked for agents.
2. Run live browser and machine-endpoint audits in isolated sandboxes.
3. Score the product against a 7-part agent-readiness rubric.
4. Send a founder-facing report when a reachable contact is available.
5. Generate MCP, `llms.txt`, docs, OpenAPI, and eval artifacts.
6. Open a PR against the customer's repo.
7. Re-audit after the fix to prove the score improved.

## Why This Exists

Most SaaS products were built for humans clicking around a UI. Agents need something different: stable machine entrypoints, machine-readable errors, retry-safe operations, explicit rate-limit metadata, discoverable docs, and auth that does not strand them in human-only OAuth flows.

Wirable turns that gap into a measurable score and a concrete code change.

## Hackathon Demo

Recommended demo path:

1. Open the landing page and run a free audit for a public SaaS domain.
2. Watch the SSE audit terminal stream progress and screenshots from the sandbox.
3. Open the generated public report with the weighted score and evidence.
4. Sign in, claim a company, and connect GitHub.
5. Start a fix run for `owner/repo`.
6. Watch the fix stream while a Daytona sandbox clones the repo and runs the OpenCode agent.
7. Open the generated PR containing the MCP server, `llms.txt`, agent guide, and eval.
8. Trigger verification to re-audit and compare before/after scores.

The product also includes an autonomous scout console that can discover targets, audit them, enrich founder contacts, and send outbound report emails when configured.

## Agent-Readiness Rubric

Wirable scores products out of 100 across seven dimensions:

| Dimension | Weight | What It Checks |
| --- | ---: | --- |
| Auth | 20 | Agents can obtain and use credentials without human-only dead ends. |
| MCP | 20 | There is a served MCP interface or equivalent machine-callable surface. |
| Discoverability | 15 | Agents can find `/llms.txt`, manifests, docs, or machine entrypoints. |
| Errors | 15 | Failures return machine-readable codes and recovery hints. |
| Idempotency | 15 | Retries and repeated actions are safe and deduplicable. |
| Rate limits | 10 | Responses expose limits, remaining budget, and retry timing. |
| Docs | 5 | Reference material is structured and parseable by agents. |

Multiple audit agents inspect the target independently. CATTS, Consensus Aggregation Through Threshold Scoring, merges their evidence. When agents disagree and Claude keys are configured, a stricter arbiter adjudicates the disputed dimension.

## What Is Built

- Public marketing page with an embedded audit launcher.
- FastAPI backend with audit, fix, report, dashboard, onboarding, GitHub, discovery, tracking, and outbound endpoints.
- Next.js dashboard for onboarding, live audit streams, fix streams, GitHub connection, reports, and scout console.
- Daytona sandbox orchestration for isolated audit and fix jobs.
- OpenCode-based harness prompts for audit, fix, outbound, discovery, and enrichment agents.
- Live screenshot streaming from the audit sandbox via SSE.
- Postgres persistence for companies, audits, audit steps, clients, MCP fixes, and outbound email logs.
- GitHub PR generation for agent-readiness fixes.
- Optional Unipile outbound email integration with tracking pixels.
- Docker Compose for local Postgres, backend, and web services.

## Architecture

```text
Next.js web app
  |-- public audit flow
  |-- authenticated dashboard
  |-- SSE terminals for audit/fix progress
  |
FastAPI backend
  |-- /api/v1/audit       live CATTS audit jobs
  |-- /api/v1/fix         repo fix jobs and verification
  |-- /api/v1/report      public audit reports
  |-- /api/v1/discovery   autonomous scout pipeline
  |-- /api/v1/outbound    founder report emails
  |-- /api/v1/github      repo connection flow
  |
Postgres
  |-- companies, clients, audits, audit steps, MCP fixes, outbound logs
  |
Daytona sandboxes
  |-- OpenCode audit agents
  |-- browser screenshots
  |-- repo cloning and fix generation
  |
External services
  |-- Anthropic Claude for agents and arbitration
  |-- GitHub OAuth and pull requests
  |-- Google OAuth sign-in
  |-- Unipile email sending, optional
```

## Tech Stack

- Frontend: Next.js 15, React 19, TypeScript, Tailwind CSS, NextAuth.
- Backend: FastAPI, SQLAlchemy async, Alembic, Pydantic Settings.
- Database: Postgres 16.
- Agents: OpenCode, Claude, CATTS aggregation.
- Sandboxes: Daytona with a custom Chromium/Playwright/OpenCode image.
- Integrations: GitHub, Google OAuth, Unipile.
- Local runtime: Docker Compose, Python 3.12, Node 20.

## Repository Layout

```text
.
|-- backend/              # FastAPI API, models, services, Alembic migrations
|-- web/                  # Next.js app, marketing site, dashboard, reports
|-- harness/              # OpenCode prompts and skill playbooks
|-- docker/sandbox/       # Daytona sandbox image with Chromium and OpenCode
|-- components/           # Shared landing/global components
|-- styles/               # Shared CSS and styling utilities
|-- docker-compose.yml    # Local Postgres, backend, and web
|-- setup.sh              # One-shot macOS/Linux dev setup
|-- setup.ps1             # One-shot Windows dev setup
`-- .env.example          # Root environment template
```

## Quick Start

Prerequisites:

- Python 3.12+
- Node.js 20+
- Docker
- Git
- A Daytona API key
- An Anthropic API key
- Google OAuth credentials for sign-in
- GitHub OAuth credentials for repo connection

Run the one-shot setup:

```sh
cp .env.example .env
bash setup.sh
```

Fill the required values in `.env`, then start the full stack:

```sh
docker compose up
```

Open:

- Web app: http://localhost:3000
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Manual Local Development

Start Postgres:

```sh
docker compose up db
```

Run the backend:

```sh
cd backend
PYTHONPATH=. .venv/bin/alembic upgrade head
PYTHONPATH=. .venv/bin/uvicorn src.main:app --reload --port 8000
```

Run the frontend:

```sh
cd web
npm run dev
```

## Environment

Important variables:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | Yes | Async Postgres URL for FastAPI and Alembic. |
| `JWT_SECRET` | Yes | Backend JWT signing secret. |
| `NEXTAUTH_SECRET` | Yes | NextAuth session secret. |
| `NEXTAUTH_URL` | Yes | Web app base URL, usually `http://localhost:3000`. |
| `NEXT_PUBLIC_BACKEND_URL` | Yes | Browser-visible backend URL. |
| `BACKEND_URL` | Yes | Server-side backend URL used by Next.js. |
| `DAYTONA_API_KEY` | Yes | Creates isolated audit and fix sandboxes. |
| `DAYTONA_SERVER_URL` | Yes | Daytona API URL, defaults to `https://app.daytona.io`. |
| `ANTHROPIC_API_KEY` | Yes | Powers OpenCode agents and CATTS arbitration. |
| `ANTHROPIC_API_KEYS` | Optional | Comma-separated Claude key pool for parallel runs. |
| `ANTHROPIC_MODEL` | Optional | Defaults to `claude-sonnet-4-6`. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Yes for auth | Google sign-in. |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Yes for fixes | GitHub repo connection. |
| `AGENTREADY_SANDBOX_IMAGE` | Optional | Custom Daytona image with Chromium, Playwright, and OpenCode. |
| `SCOUT_ENABLED` | Optional | Enables scheduled autonomous scout cycles. |
| `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID` | Optional | Outbound email sending. |
| `REPORT_BASE_URL` | Optional | Base URL used in public report links. |

## Sandbox Image

The audit and fix agents work best with the custom sandbox image in `docker/sandbox/`.

Build locally:

```sh
cd docker/sandbox
chmod +x build.sh entrypoint.sh
./build.sh
```

To push a registry image Daytona can pull:

```sh
./build.sh ghcr.io/YOUR_ORG/agentready-sandbox:latest
```

Then set:

```sh
AGENTREADY_SANDBOX_IMAGE=ghcr.io/YOUR_ORG/agentready-sandbox:latest
```

If no custom image is configured, the backend falls back to Daytona's default Python snapshot and tries to self-heal OpenCode. Browser screenshots require the custom image.

## API Overview

Key endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness probe. |
| `POST` | `/api/v1/audit/request` | Start a public audit for a domain. |
| `GET` | `/api/v1/audit/{job_id}/stream` | Stream audit events and screenshots over SSE. |
| `POST` | `/api/v1/fix/start` | Start a repo fix job for an authenticated client. |
| `GET` | `/api/v1/fix/{job_id}/stream` | Stream fix job events over SSE. |
| `POST` | `/api/v1/fix/verify` | Re-audit after a fix PR. |
| `GET` | `/api/v1/report/{company_id}` | Public report JSON for outbound links. |
| `POST` | `/api/v1/discovery/scout` | Trigger one scout cycle. |
| `GET` | `/api/v1/discovery/targets` | List discovered and audited targets. |
| `POST` | `/api/v1/outbound/send` | Send an audit report email for a company. |

## Fix PR Output

The fix agent attempts to generate:

- `mcp-server/index.ts`
- `mcp-server/package.json`
- `mcp-server/tools/*`
- `llms.txt`
- `docs/agent-guide.md`
- `openapi.json`
- `evals/basic.ts`

The backend scores the generated artifacts, opens a GitHub PR, stores the result, and exposes before/after score projections in the dashboard.

## Current Limitations

- The app still uses the internal `AgentReady` brand in UI copy and code while this repo is named `wirable`.
- Production deployment needs hardened secrets, OAuth callback URLs, sandbox image registry access, and email compliance review.
- The in-process SSE history is simple and effective for a hackathon demo, but should move to durable pub/sub for multi-instance production.
- The autonomous scout and enrichment loop is best-effort and intentionally conservative about contact data.
- Post-fix score is partly projected until the verification re-audit runs against the live deployment.

## Submission Notes

Wirable is a working prototype for the moment when agents become a first-class integration surface. Instead of asking teams to read another checklist, it runs the checklist, shows evidence, writes the missing interface, and ships the fix as a PR.

Built for Paris Builds 2026.
