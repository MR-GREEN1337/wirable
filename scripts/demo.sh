#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Wirable — local one-command demo bring-up
# ──────────────────────────────────────────────────────────────────────────────
# Boots the full local stack (postgres + FastAPI backend + Next.js web) via
# docker compose, waits for the DB to be healthy, applies migrations, brings up
# the app, and prints the URLs you rehearse the demo on.
#
# Usage:
#   scripts/demo.sh                 # bring the whole stack up and print URLs
#   scripts/demo.sh test <url>      # start a run against <url> and tail its SSE
#   scripts/demo.sh down            # stop the stack (keeps the pg volume)
#
# Examples:
#   scripts/demo.sh
#   scripts/demo.sh test https://demo.vercel.store
#
# Notes:
#   - The `backend` service already runs `alembic upgrade head` on start (see
#     docker-compose.yml), but we also run it explicitly here so a failed
#     migration surfaces loudly instead of being buried in container logs.
#   - Requires the gitignored env files to exist: wirable/.env (+ optionally
#     backend/.env, web/.env.local). See scripts/README.md.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# Resolve repo root (this script lives in <root>/scripts) regardless of cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

WEB_URL="http://localhost:3000"
API_URL="http://localhost:8000"

# ── pretty printing ───────────────────────────────────────────────────────────
log()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── docker compose shim (supports both `docker compose` and `docker-compose`) ──
if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    die "docker compose not found. Install Docker Desktop / the compose plugin."
fi

# ── preflight ─────────────────────────────────────────────────────────────────
preflight() {
    command -v docker >/dev/null 2>&1 || die "docker is not installed / not on PATH."
    docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop and retry."
    [ -f "${ROOT_DIR}/docker-compose.yml" ] || die "docker-compose.yml not found in ${ROOT_DIR}."
    if [ ! -f "${ROOT_DIR}/.env" ]; then
        warn ".env not found in ${ROOT_DIR}. Copy .env.example → .env and fill in"
        warn "ANTHROPIC_API_KEYS + DAYTONA_API_KEY before running a real audit."
    fi
}

# Wait until the `db` service reports healthy (compose has a pg_isready healthcheck).
wait_for_db() {
    log "Waiting for postgres (db) to become healthy…"
    local cid status i
    for i in $(seq 1 60); do
        cid="$("${DC[@]}" ps -q db 2>/dev/null || true)"
        if [ -n "${cid}" ]; then
            status="$(docker inspect -f '{{.State.Health.Status}}' "${cid}" 2>/dev/null || echo "starting")"
            if [ "${status}" = "healthy" ]; then
                ok "postgres is healthy."
                return 0
            fi
        fi
        sleep 2
    done
    die "postgres did not become healthy in time. Check: ${DC[*]} logs db"
}

run_migrations() {
    log "Applying database migrations (alembic upgrade head)…"
    # Run inside a one-off backend container so we don't depend on the long-running
    # backend service being up yet. --rm keeps it clean.
    if "${DC[@]}" run --rm backend sh -c "cd /app && python -m alembic upgrade head"; then
        ok "migrations applied."
    else
        die "alembic upgrade head failed. Inspect the output above."
    fi
}

bring_up() {
    preflight
    log "Starting postgres…"
    "${DC[@]}" up -d db
    wait_for_db
    run_migrations
    log "Building + starting backend and web (first build may take a few minutes)…"
    "${DC[@]}" up -d --build backend web
    echo
    ok "Wirable is up."
    echo
    printf '  Web (demo UI):  \033[1;36m%s\033[0m\n' "${WEB_URL}"
    printf '  API docs:       \033[1;36m%s/docs\033[0m\n' "${API_URL}"
    printf '  Health:         \033[1;36m%s/health\033[0m\n' "${API_URL}"
    echo
    echo "  Rehearse a run:   scripts/demo.sh test <target-url>"
    echo "  Stop everything:  scripts/demo.sh down"
}

# ── `test <url>`: kick off a run and tail its SSE stream to the terminal ───────
do_test() {
    local target="${1:-}"
    [ -n "${target}" ] || die "Usage: scripts/demo.sh test <url>"
    command -v curl >/dev/null 2>&1 || die "curl is required for 'test'."

    log "Checking backend is reachable at ${API_URL}/health …"
    curl -fsS "${API_URL}/health" >/dev/null 2>&1 \
        || die "Backend not reachable. Run 'scripts/demo.sh' first to bring the stack up."

    log "Starting a run against: ${target}"
    local resp run_id
    resp="$(curl -fsS -X POST "${API_URL}/api/v1/run" \
        -H 'Content-Type: application/json' \
        -d "{\"url\": \"${target}\"}")" \
        || die "POST /api/v1/run failed."

    # Extract run_id without requiring jq (fall back to grep/sed).
    if command -v jq >/dev/null 2>&1; then
        run_id="$(printf '%s' "${resp}" | jq -r '.run_id // empty')"
    else
        run_id="$(printf '%s' "${resp}" | sed -n 's/.*"run_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    fi
    [ -n "${run_id}" ] || die "No run_id in response: ${resp}"

    ok "run_id = ${run_id}"
    echo
    printf '  Watch in the UI:  \033[1;36m%s/run/%s\033[0m\n' "${WEB_URL}" "${run_id}"
    echo
    log "Tailing live SSE (Ctrl-C to stop)…"
    echo "──────────────────────────────────────────────────────────────────────"
    # -N disables buffering so events print as they arrive. The stream closes
    # itself when the run emits type=done / type=error.
    curl -N -sS "${API_URL}/api/v1/run/${run_id}/stream" || true
    echo
    echo "──────────────────────────────────────────────────────────────────────"
    ok "Stream closed. Full report: ${WEB_URL}/run/${run_id}"
}

do_down() {
    preflight
    log "Stopping the stack (postgres volume is preserved)…"
    "${DC[@]}" down
    ok "Stopped. Data volume 'pgdata' kept. To wipe it: ${DC[*]} down -v"
}

# ── dispatch ──────────────────────────────────────────────────────────────────
case "${1:-up}" in
    up|"")   bring_up ;;
    test)    shift; do_test "$@" ;;
    down)    do_down ;;
    *)       die "Unknown command '${1}'. Use: up | test <url> | down" ;;
esac
