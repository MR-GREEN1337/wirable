#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Build (and optionally push) the AgentReady sandbox image.
# ──────────────────────────────────────────────────────────────────────────────
# This is the container the audit/fix OpenCode agents run inside, launched by the
# backend via Daytona. Point the backend at it with AGENTREADY_SANDBOX_IMAGE.
#
# Usage:
#   ./build.sh                                  # build + tag agentready-sandbox:latest (local)
#   ./build.sh registry/image:tag               # build, tag, AND push to that ref
#
# Examples:
#   ./build.sh                                  # local-only (no push)
#   ./build.sh ghcr.io/you/agentready-sandbox:latest
#   ./build.sh us-docker.pkg.dev/PROJECT/agentready/sandbox:latest
#
# Make it executable first:  chmod +x build.sh
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DEFAULT_IMAGE="agentready-sandbox:latest"
IMAGE="${1:-$DEFAULT_IMAGE}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Push only when an explicit registry/tag arg is given (the default local tag has
# nowhere to push to).
PUSH="false"
if [ "$#" -ge 1 ]; then
    PUSH="true"
fi

echo "Building ${IMAGE}…"
# linux/amd64 so the image runs on Daytona's x86_64 fleet regardless of build host
# (e.g. an Apple-silicon dev machine). Uses buildx; falls back to plain build.
if docker buildx version >/dev/null 2>&1; then
    docker buildx build \
        --platform linux/amd64 \
        --tag "${IMAGE}" \
        --file "${SCRIPT_DIR}/Dockerfile" \
        "$( [ "$PUSH" = "true" ] && echo "--push" || echo "--load" )" \
        "${SCRIPT_DIR}"
else
    echo "buildx unavailable — using plain docker build (host arch)."
    docker build \
        --tag "${IMAGE}" \
        --file "${SCRIPT_DIR}/Dockerfile" \
        "${SCRIPT_DIR}"
    if [ "$PUSH" = "true" ]; then
        echo "Pushing ${IMAGE}…"
        docker push "${IMAGE}"
    fi
fi

echo ""
echo "Done."
echo ""
echo "  → Set this in the backend environment so AgentReady uses the image:"
echo ""
echo "      AGENTREADY_SANDBOX_IMAGE=${IMAGE}"
echo ""
if [ "$PUSH" != "true" ]; then
    echo "  Note: built locally only (no push). To make it available to the Daytona"
    echo "  fleet, re-run with a registry ref, e.g.:"
    echo "      ./build.sh ghcr.io/you/agentready-sandbox:latest"
    echo ""
    echo "  If AGENTREADY_SANDBOX_IMAGE is unset or the image is unavailable, the"
    echo "  backend falls back to Daytona's default 'python' snapshot."
fi
