# AgentReady — one-shot dev setup (Windows PowerShell)
# Usage: .\setup.ps1
#
# Requires PowerShell 5.1+ or PowerShell 7+.
# Run in an Administrator shell if you hit permission errors.

$ErrorActionPreference = "Stop"

function Write-Step  { Write-Host "▶ $args" -ForegroundColor Cyan }
function Write-OK    { Write-Host "✓ $args" -ForegroundColor Green }
function Write-Fail  { Write-Host "✗ $args" -ForegroundColor Red; exit 1 }
function Write-Title { Write-Host "`n$args" -ForegroundColor White }

Set-Location $PSScriptRoot

Write-Title "AgentReady — dev environment setup"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
Write-Title "Checking prerequisites..."

function Check-Command {
    param($cmd, $label, $install)
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        Write-OK "$label found"
    } else {
        Write-Fail "$label not found. $install"
    }
}

Check-Command "python"  "Python 3"  "Install from https://python.org/downloads"
Check-Command "node"    "Node.js"   "Install from https://nodejs.org"
Check-Command "git"     "Git"       "Install from https://git-scm.com"
Check-Command "docker"  "Docker"    "Install from https://docs.docker.com/desktop/windows/"

# Python version check
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ([System.Version]$pyVer -ge [System.Version]"3.12") {
    Write-OK "Python $pyVer"
} else {
    Write-Fail "Python 3.12+ required (found $pyVer). Install from https://python.org/downloads"
}

# Node version check
$nodeMaj = (node -e "console.log(process.version.slice(1))").Split(".")[0]
if ([int]$nodeMaj -ge 20) {
    Write-OK "Node.js $((node -e 'console.log(process.version)'))"
} else {
    Write-Fail "Node.js 20+ required. Install from https://nodejs.org"
}

# uv
if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Step "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
}
Write-OK "uv found"

# ── 2. .env setup ─────────────────────────────────────────────────────────────
Write-Title "Environment config..."

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-OK "Created .env from .env.example"
    Write-Host ""
    Write-Host "  Fill in these required keys in .env before running:" -ForegroundColor Yellow
    Write-Host "    DAYTONA_API_KEY         — from app.daytona.io"
    Write-Host "    ANTHROPIC_API_KEY       — from console.anthropic.com"
    Write-Host "    GOOGLE_CLIENT_ID/SECRET — from console.cloud.google.com"
    Write-Host "    GITHUB_CLIENT_ID/SECRET — from github.com/settings/apps"
    Write-Host ""
} else {
    Write-OK ".env already exists (not overwritten)"
}

# ── 3. Backend ────────────────────────────────────────────────────────────────
Write-Title "Backend (Python)..."

Set-Location backend

if (-not (Test-Path ".venv")) {
    Write-Step "Creating virtualenv..."
    uv venv .venv --python python3.12
}

Write-Step "Installing Python deps..."
try {
    uv pip install --python .venv/Scripts/python.exe -e "."
} catch {
    .venv/Scripts/pip install -e "."
}
Write-OK "Backend deps installed"

# Migrations
$envContent = Get-Content "../.env" -Raw -ErrorAction SilentlyContinue
if ($envContent -match "DATABASE_URL=postgresql") {
    Write-Step "Running Alembic migrations (will skip if DB not reachable)..."
    $env:PYTHONPATH = "."
    try {
        .venv/Scripts/alembic upgrade head 2>&1 | Out-Null
        Write-OK "Migrations applied"
    } catch {
        Write-Host "  (skipped — DB not running yet; run 'docker compose up db' first)"
    }
}

Set-Location ..

# ── 4. Frontend ───────────────────────────────────────────────────────────────
Write-Title "Frontend (Next.js)..."

Set-Location web
Write-Step "Installing npm deps..."
npm install --legacy-peer-deps
Write-OK "Frontend deps installed"
Set-Location ..

# ── 5. Harness Docker image ───────────────────────────────────────────────────
Write-Title "Harness Docker image..."

$dockerOk = docker info 2>&1 | Select-String "Server Version" -Quiet
if ($dockerOk) {
    Write-Step "Building agentready-harness:latest..."
    try {
        docker build -t agentready-harness:latest harness/
        Write-OK "Harness image built"
    } catch {
        Write-Host "  (build failed — sandbox will fall back to Daytona default snapshot)"
    }
} else {
    Write-Host "  Docker not running — skipping harness image build"
}

# ── 6. Done ───────────────────────────────────────────────────────────────────
Write-Title "Setup complete!"
Write-Host ""
Write-Host "  Start everything:" -ForegroundColor White
Write-Host "    docker compose up"
Write-Host ""
Write-Host "  Or run services individually:" -ForegroundColor White
Write-Host "    docker compose up db"
Write-Host "    cd backend; `$env:PYTHONPATH='.'; .venv/Scripts/uvicorn src.main:app --reload --port 8000"
Write-Host "    cd web; npm run dev"
Write-Host ""
Write-Host "  Docs:  http://localhost:3000" -ForegroundColor Cyan
Write-Host "  API:   http://localhost:8000/docs" -ForegroundColor Cyan
Write-Host ""
