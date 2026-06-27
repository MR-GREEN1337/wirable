#!/usr/bin/env bash
# AgentReady — one-shot dev setup
# Usage: bash setup.sh
set -euo pipefail

BOLD='\033[1m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'

info()    { echo -e "${CYAN}▶${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
fail()    { echo -e "${RED}✗${RESET} $*"; exit 1; }
header()  { echo -e "\n${BOLD}$*${RESET}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

header "AgentReady — dev environment setup"

# ── 1. Prerequisite check ─────────────────────────────────────────────────────
header "Checking prerequisites..."

check() {
  local cmd="$1" label="${2:-$1}" install="$3"
  if command -v "$cmd" &>/dev/null; then
    success "$label found: $(command -v "$cmd")"
  else
    fail "$label not found. $install"
  fi
}

check python3    "Python 3"  "Install from https://python.org/downloads"
check node       "Node.js"   "Install from https://nodejs.org"
check git        "Git"       "Install from https://git-scm.com"
check docker     "Docker"    "Install from https://docs.docker.com/get-docker/"

# Python version gate
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"; then
  success "Python $PY_VER"
else
  fail "Python 3.12+ required (found $PY_VER). Install from https://python.org/downloads"
fi

# Node version gate (>=20)
NODE_VER=$(node -e "console.log(process.version.slice(1))")
NODE_MAJ=$(echo "$NODE_VER" | cut -d. -f1)
if [ "$NODE_MAJ" -ge 20 ]; then
  success "Node.js $NODE_VER"
else
  fail "Node.js 20+ required (found $NODE_VER). Install from https://nodejs.org"
fi

# uv (fast Python installer)
if ! command -v uv &>/dev/null; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$PATH"
fi
success "uv found"

# ── 2. .env setup ─────────────────────────────────────────────────────────────
header "Environment config..."

if [ ! -f .env ]; then
  cp .env.example .env
  success "Created .env from .env.example"
  echo ""
  echo -e "  ${BOLD}Fill in these required keys in .env before running:${RESET}"
  echo "    DAYTONA_API_KEY         — from app.daytona.io"
  echo "    ANTHROPIC_API_KEY       — from console.anthropic.com"
  echo "    GOOGLE_CLIENT_ID/SECRET — from console.cloud.google.com → OAuth2"
  echo "    GITHUB_CLIENT_ID/SECRET — from github.com/settings/apps → OAuth App"
  echo ""
else
  success ".env already exists (not overwritten)"
fi

# ── 3. Backend ────────────────────────────────────────────────────────────────
header "Backend (Python)..."

cd backend

if [ ! -d .venv ]; then
  info "Creating virtualenv..."
  uv venv .venv --python python3.12
fi

info "Installing Python deps..."
uv pip install --python .venv/bin/python -e ".[dev]" 2>/dev/null || \
  uv pip install --python .venv/bin/python -r <(uv pip compile pyproject.toml -q) || \
  .venv/bin/pip install -e "."

success "Backend deps installed"

# Run migrations if DATABASE_URL is set and Postgres is reachable
if grep -q "DATABASE_URL=postgresql" ../.env 2>/dev/null; then
  info "Running Alembic migrations (will skip if DB not reachable)..."
  PYTHONPATH=. .venv/bin/alembic upgrade head 2>/dev/null && success "Migrations applied" || \
    echo "  (skipped — DB not running yet; run 'docker compose up db' first)"
fi

cd ..

# ── 4. Frontend ───────────────────────────────────────────────────────────────
header "Frontend (Next.js)..."

cd web
info "Installing npm deps..."
npm install --legacy-peer-deps
success "Frontend deps installed"
cd ..

# ── 5. Harness Docker image ───────────────────────────────────────────────────
header "Harness Docker image..."

if docker info &>/dev/null 2>&1; then
  info "Building agentready-harness:latest..."
  docker build -t agentready-harness:latest harness/ && \
    success "Harness image built" || \
    echo "  (build failed — see above; sandbox will fall back to Daytona default snapshot)"
else
  echo "  Docker daemon not running — skipping harness image build"
fi

# ── 6. Done ───────────────────────────────────────────────────────────────────
header "Setup complete!"
echo ""
echo -e "  ${BOLD}Start everything:${RESET}"
echo "    docker compose up              # Postgres + backend + frontend"
echo ""
echo -e "  ${BOLD}Or run services individually:${RESET}"
echo "    docker compose up db           # Postgres only"
echo "    cd backend && PYTHONPATH=. .venv/bin/uvicorn src.main:app --reload --port 8000"
echo "    cd web && npm run dev"
echo ""
echo -e "  ${BOLD}Docs:${RESET}  http://localhost:3000"
echo -e "  ${BOLD}API:${RESET}   http://localhost:8000/docs"
echo ""
