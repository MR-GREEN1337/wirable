#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Wirable — local build → ship → run on the Hetzner VPS
# ──────────────────────────────────────────────────────────────────────────────
# Idempotent, safe-to-re-run deploy. It:
#   1. Builds the Daytona sandbox image locally (and reminds you to PUSH it to a
#      registry — the sandbox runs in Daytona's cloud, NOT on this box).
#   2. rsyncs the wirable app to the box (excluding build/dep junk).
#   3. Ensures a prod .env exists on the box (copies wirable/.env if missing,
#      then warns to harden it).
#   4. SSHes in and runs `docker compose up -d --build`, applies migrations, and
#      health-checks the backend.
#
# It never runs `rm -rf` on the remote and prints every step.
#
# Usage:
#   scripts/deploy_hetzner.sh                 # full deploy (build sandbox + ship + run)
#   scripts/deploy_hetzner.sh --skip-sandbox  # ship + run only (sandbox unchanged)
#   scripts/deploy_hetzner.sh --app-only      # alias for --skip-sandbox
#
# Override the sandbox registry so Daytona can pull it:
#   REGISTRY=ghcr.io/you scripts/deploy_hetzner.sh
#   (→ builds + pushes ghcr.io/you/agentready-sandbox:latest)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config (override via env) ─────────────────────────────────────────────────
HOST="${HOST:-5.161.110.99}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/crossnode_hetzner}"
SSH_USER="${SSH_USER:-root}"
REMOTE_DIR="${REMOTE_DIR:-/opt/wirable}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-agentready-sandbox:latest}"

# REGISTRY hook: when set, the sandbox is built AND pushed as
# "${REGISTRY}/agentready-sandbox:latest" so the Daytona fleet can pull it.
# When empty, the sandbox is built LOCALLY ONLY (you must push it yourself, or
# the backend falls back to Daytona's default 'python' snapshot).
REGISTRY="${REGISTRY:-}"

# Resolve repo root (this script lives in <root>/scripts).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SSH_OPTS=(-i "${SSH_KEY}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
REMOTE="${SSH_USER}@${HOST}"

# ── pretty printing ───────────────────────────────────────────────────────────
step() { printf '\n\033[1;36m━━ %s\033[0m\n' "$*"; }
log()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── args ──────────────────────────────────────────────────────────────────────
SKIP_SANDBOX="false"
case "${1:-}" in
    --skip-sandbox|--app-only) SKIP_SANDBOX="true" ;;
    "" ) ;;
    * ) die "Unknown arg '${1}'. Use: --skip-sandbox | --app-only" ;;
esac

# ── preflight ─────────────────────────────────────────────────────────────────
preflight() {
    command -v docker >/dev/null 2>&1 || die "docker not found on PATH."
    command -v rsync  >/dev/null 2>&1 || die "rsync not found on PATH."
    command -v ssh    >/dev/null 2>&1 || die "ssh not found on PATH."
    [ -f "${SSH_KEY}" ] || die "SSH key not found at ${SSH_KEY}."
    [ -f "${ROOT_DIR}/docker-compose.yml" ] || die "docker-compose.yml not found in ${ROOT_DIR}."
    docker info >/dev/null 2>&1 || die "Docker daemon not running locally (needed to build the sandbox)."
    log "Checking SSH connectivity to ${REMOTE} …"
    ssh "${SSH_OPTS[@]}" "${REMOTE}" 'echo ok' >/dev/null 2>&1 \
        || die "Cannot SSH to ${REMOTE} with ${SSH_KEY}."
    ssh "${SSH_OPTS[@]}" "${REMOTE}" 'command -v docker >/dev/null 2>&1' \
        || die "Docker is not installed on the remote box."
    ok "preflight passed."
}

# ── 1. build (+ optionally push) the Daytona sandbox image ────────────────────
build_sandbox() {
    if [ "${SKIP_SANDBOX}" = "true" ]; then
        log "Skipping sandbox build (--skip-sandbox)."
        return 0
    fi
    step "1. Build Daytona sandbox image"
    local build_sh="${ROOT_DIR}/docker/sandbox/build.sh"
    [ -f "${build_sh}" ] || die "Sandbox builder not found at ${build_sh}."

    if [ -n "${REGISTRY}" ]; then
        local ref="${REGISTRY%/}/agentready-sandbox:latest"
        log "Building + pushing sandbox → ${ref}"
        log "(passing the registry ref to build.sh triggers a push)"
        bash "${build_sh}" "${ref}"
        ok "Sandbox pushed: ${ref}"
        warn "Set AGENTREADY_SANDBOX_IMAGE=${ref} in the REMOTE .env so Daytona pulls it."
    else
        log "Building sandbox locally as ${SANDBOX_IMAGE} (no REGISTRY set)."
        bash "${build_sh}" "${SANDBOX_IMAGE}"
        warn "IMPORTANT: the sandbox runs in Daytona's CLOUD, not on the Hetzner box."
        warn "A local-only image is NOT reachable by Daytona. To make it available:"
        warn "    REGISTRY=ghcr.io/you scripts/deploy_hetzner.sh"
        warn "  or push manually:  docker push <registry>/agentready-sandbox:latest"
        warn "  then set AGENTREADY_SANDBOX_IMAGE to that ref in the remote .env."
        warn "Without it, the backend falls back to Daytona's default 'python' snapshot."
    fi
}

# ── 2. ship the app to the box ────────────────────────────────────────────────
ship_app() {
    step "2. Ship app to ${REMOTE}:${REMOTE_DIR}"
    log "Ensuring ${REMOTE_DIR} exists on the box…"
    ssh "${SSH_OPTS[@]}" "${REMOTE}" "mkdir -p '${REMOTE_DIR}'"

    log "rsyncing app (excluding node_modules/.next/.venv/.git/logs)…"
    # NOTE: no --delete — we never destructively wipe the remote tree. Re-runs
    # overwrite changed files but leave the remote .env and pg volume untouched.
    rsync -az --human-readable \
        -e "ssh ${SSH_OPTS[*]}" \
        --exclude '.git/' \
        --exclude 'node_modules/' \
        --exclude 'web/.next/' \
        --exclude 'web/out/' \
        --exclude '.venv/' \
        --exclude 'venv/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '*.log' \
        --exclude '.DS_Store' \
        --exclude '.env' \
        "${ROOT_DIR}/" "${REMOTE}:${REMOTE_DIR}/"
    ok "app synced."
}

# ── 3. ensure a prod .env exists on the box ───────────────────────────────────
ensure_remote_env() {
    step "3. Ensure prod .env on the box"
    if ssh "${SSH_OPTS[@]}" "${REMOTE}" "test -f '${REMOTE_DIR}/.env'"; then
        ok "Remote .env already present — left untouched."
    else
        if [ -f "${ROOT_DIR}/.env" ]; then
            warn "No remote .env. Seeding it from your LOCAL .env (dev secrets!)."
            # scp the local .env up as a starting point. It MUST be hardened.
            scp "${SSH_OPTS[@]}" "${ROOT_DIR}/.env" "${REMOTE}:${REMOTE_DIR}/.env"
            ok "Seeded remote .env from local .env."
        else
            warn "No local .env to seed from. Create ${REMOTE_DIR}/.env on the box by hand."
        fi
    fi
    cat >&2 <<EOF

  ╭─ PROD SECRETS CHECKLIST (edit ${REMOTE_DIR}/.env on the box) ────────────────╮
  │  DATABASE_URL      → point at managed Postgres (e.g. Neon), NOT the throwaway │
  │                      compose 'db' service, for anything persistent.           │
  │  NEXTAUTH_URL      → https://<your-domain>   (must match the public host)     │
  │  NEXT_PUBLIC_BACKEND_URL / REPORT_BASE_URL → public https URLs                │
  │  JWT_SECRET / NEXTAUTH_SECRET / INTERNAL_SECRET → strong unique values        │
  │  ANTHROPIC_API_KEYS → real key pool (comma-separated)                         │
  │  DAYTONA_API_KEY   → real key                                                 │
  │  AGENTREADY_SANDBOX_IMAGE → registry ref Daytona can pull                     │
  │  GOOGLE/GITHUB OAuth + UNIPILE_* → real prod credentials                      │
  ╰──────────────────────────────────────────────────────────────────────────────╯

EOF
}

# ── 4. bring the stack up on the box + migrate + health-check ─────────────────
remote_up() {
    step "4. Build + run on the box, migrate, health-check"
    # Heredoc runs ON the remote. Quoted 'EOF' so local vars don't expand. We
    # pass REMOTE_DIR as a positional arg ($1) — robust regardless of the remote
    # sshd AcceptEnv config (env-passing over SSH is often disabled).
    ssh "${SSH_OPTS[@]}" "${REMOTE}" "bash -s -- $(printf '%q' "${REMOTE_DIR}")" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
cd "${REMOTE_DIR}"

# docker compose shim (plugin or legacy binary)
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    echo "✗ docker compose not available on the remote." >&2; exit 1
fi

echo "▸ docker compose up -d --build …"
$DC up -d --build

echo "▸ Applying migrations (alembic upgrade head)…"
# Run against the running backend service so it shares its env + network.
$DC exec -T backend sh -c "cd /app && python -m alembic upgrade head"

echo "▸ Health-checking backend (localhost:8000/health)…"
ok=false
for i in $(seq 1 30); do
    if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
        ok=true; break
    fi
    sleep 3
done
if [ "$ok" = "true" ]; then
    echo "✓ backend healthy."
else
    echo "✗ backend did NOT pass health check. Logs:" >&2
    $DC logs --tail=60 backend >&2 || true
    exit 1
fi
EOF
    ok "Remote stack is up and healthy."
}

# ── go ────────────────────────────────────────────────────────────────────────
main() {
    preflight
    build_sandbox
    ship_app
    ensure_remote_env
    remote_up

    echo
    ok "Deploy complete."
    echo
    printf '  Backend health (on box):  curl http://%s:8000/health\n' "${HOST}"
    printf '  App is published on the box per its compose port mappings (8000/api, 3000/web).\n'
    echo
    cat <<EOF
  ── Coolify alternative (this box already runs Coolify + Traefik) ──────────────
  Instead of raw docker compose, you can let Coolify manage Wirable and get TLS
  for free:
    1. In Coolify → New Resource → Docker Compose, point it at this repo
       (or paste ${REMOTE_DIR}/docker-compose.yml).
    2. Add the same env vars from the prod checklist above in Coolify's UI.
    3. Assign a domain/subdomain (e.g. wirable.apps.crossnode.sh) to the `web`
       service; Coolify's Traefik provisions a Let's Encrypt cert automatically.
    4. Point another subdomain at `backend` (or proxy /api through the web host)
       and set NEXT_PUBLIC_BACKEND_URL / NEXTAUTH_URL to those https URLs.
  Coolify then handles rebuilds, TLS renewal, and restarts. If a *.apps domain
  shows ERR_CERT_AUTHORITY_INVALID, run: docker restart coolify-proxy on the box.
EOF
}

main "$@"
