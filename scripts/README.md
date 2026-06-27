# Wirable — operations scripts

Three scripts that get Wirable from "checked out" to "running a live audit",
locally for the demo and on the Hetzner VPS for prod.

| Script | What it does |
| --- | --- |
| `demo.sh` | One-command local bring-up of the full stack (db + backend + web), plus a `test <url>` mode that fires a run and tails its SSE so you can rehearse. |
| `seed_targets.sh` | The two hardened, known-good demo targets (one API path, one no-API Playwright path) and the exact commands to run them. |
| `deploy_hetzner.sh` | Local sandbox build → rsync app to the box → `docker compose up -d --build` → migrate → health-check. Idempotent and safe to re-run. |

All scripts resolve the repo root from their own location, so you can run them
from anywhere.

---

## Prerequisites

**Local (demo):**
- Docker Desktop running (provides `docker` + `docker compose`).
- `curl` (for `test` mode). `jq` optional — falls back to `sed`.
- Env files present (all gitignored — they are **not** in the repo):
  - `wirable/.env` — copy from `wirable/.env.example` and fill in.
  - `wirable/backend/.env`, `wirable/web/.env.local` — already exist in this
    checkout; compose reads `wirable/.env` for the values it injects.
- In `.env`, set:
  - `ANTHROPIC_API_KEYS` — comma-separated **key pool** (rotated per run).
    A single `ANTHROPIC_API_KEY` works as a fallback.
  - `DAYTONA_API_KEY` (+ `DAYTONA_SERVER_URL`) — the agent sandboxes run in
    Daytona's cloud.
  - `AGENTREADY_SANDBOX_IMAGE` — the sandbox tag (default
    `agentready-sandbox:latest`). For a local demo you can leave the default;
    if the image isn't reachable, the backend falls back to Daytona's default
    `python` snapshot.

**Sandbox image** (built by `docker/sandbox/build.sh`, **not** by these scripts
except via the deploy script):
```sh
# local only:
docker/sandbox/build.sh
# build + push so Daytona's fleet can pull it:
docker/sandbox/build.sh ghcr.io/you/agentready-sandbox:latest
```
The sandbox runs in **Daytona cloud**, never on the Hetzner box, so it must live
in a registry Daytona can pull from.

**Hetzner deploy (additional):**
- `rsync` + `ssh` locally.
- SSH key at `~/.ssh/crossnode_hetzner` with access to `root@5.161.110.99`.
- Docker installed on the box (it is — it runs Coolify + Traefik).

---

## Local demo flow

```sh
# 1. Bring the whole stack up (builds images on first run).
scripts/demo.sh
#    → Web:  http://localhost:3000
#    → API:  http://localhost:8000/docs
#    → Health: http://localhost:8000/health

# 2. Rehearse a run — fires POST /api/v1/run {url}, prints the watch URL,
#    and tails the live SSE stream to your terminal.
scripts/demo.sh test https://demo.vercel.store
#    → Watch in the UI: http://localhost:3000/run/<run_id>

# 3. Stop (keeps the postgres volume):
scripts/demo.sh down
```

### The two demo targets
```sh
scripts/seed_targets.sh            # print the documented target list
scripts/seed_targets.sh run        # fire BOTH (API path, then no-API path)
scripts/seed_targets.sh run api    # API target only
scripts/seed_targets.sh run noapi  # no-API target only
```
- **API path** — `https://demo.vercel.store` (Shopify Storefront GraphQL behind
  it; agent finds + calls structured endpoints). Alt: `https://dummyjson.com`.
- **No-API path** — `https://www.saucedemo.com` (login + cart, no API; agent
  drives the DOM via Playwright). Alt: `http://books.toscrape.com`.

---

## Hetzner deploy flow

```sh
# Full deploy: build the sandbox locally + ship the app + run it on the box.
scripts/deploy_hetzner.sh

# Build + PUSH the sandbox so Daytona can pull it, then deploy:
REGISTRY=ghcr.io/you scripts/deploy_hetzner.sh

# App only (sandbox unchanged):
scripts/deploy_hetzner.sh --skip-sandbox
```

What it does, in order:
1. **Builds** the Daytona sandbox (`docker/sandbox/build.sh`). Reminds you to
   **push** it — set `REGISTRY=...` to build+push, then point
   `AGENTREADY_SANDBOX_IMAGE` at that ref in the remote `.env`.
2. **rsyncs** the wirable dir to `/opt/wirable` on `5.161.110.99`, excluding
   `node_modules/.next/.venv/.git/*.log` and **never** the remote `.env`
   (no `--delete`, nothing destructive).
3. **Ensures** a prod `.env` on the box (seeds from local `.env` if missing,
   then prints the secrets checklist).
4. SSHes in: `docker compose up -d --build` → `alembic upgrade head` →
   `curl localhost:8000/health`.

It never runs `rm -rf` on the remote and prints every step. Re-running is safe.

### Prod secrets checklist (edit `/opt/wirable/.env` on the box)
- `DATABASE_URL` → managed Postgres (e.g. **Neon**), not the throwaway compose
  `db` service, for anything that must persist.
- `NEXTAUTH_URL` → `https://<your-domain>` (must match the public host).
- `NEXT_PUBLIC_BACKEND_URL` / `REPORT_BASE_URL` → public `https` URLs.
- `JWT_SECRET` / `NEXTAUTH_SECRET` / `INTERNAL_SECRET` → strong, unique values.
- `ANTHROPIC_API_KEYS` → real key pool. `DAYTONA_API_KEY` → real key.
- `AGENTREADY_SANDBOX_IMAGE` → registry ref Daytona can pull.
- `GOOGLE_*` / `GITHUB_*` OAuth + `UNIPILE_*` → real prod credentials.

### Coolify alternative (recommended for TLS)
The box already runs Coolify + Traefik. Instead of raw compose you can let
Coolify manage Wirable:
1. Coolify → New Resource → Docker Compose, point it at the repo (or
   `/opt/wirable/docker-compose.yml`).
2. Add the prod env vars in Coolify's UI.
3. Assign a subdomain (e.g. `wirable.apps.crossnode.sh`) to the `web` service —
   Traefik provisions a Let's Encrypt cert automatically. Point another
   subdomain at `backend` and update `NEXT_PUBLIC_BACKEND_URL` / `NEXTAUTH_URL`.

Coolify then handles rebuilds, TLS renewal, and restarts. If an `*.apps`
subdomain shows `ERR_CERT_AUTHORITY_INVALID`, run `docker restart coolify-proxy`
on the box (re-attempts ACME).
