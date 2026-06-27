#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# AgentReady sandbox entrypoint
# ──────────────────────────────────────────────────────────────────────────────
# Brings up a virtual display (Xvfb :1) so headed Chromium/Playwright works, then
# execs the passed command (or keeps the container alive for Daytona session use).
#
# Adapted from Crossnode's entrypoint but deliberately simpler: AgentReady captures
# screenshots to FILES (/screenshots/NNNN.jpg), so we don't run the agent-browser
# CDP daemon, the WS→MJPEG stream bridge, or socat. Xvfb + a window manager is all
# the Playwright-driving audit/fix skills need.
#
# Defensive: if Xvfb fails to come up we STILL exec the command — Playwright can
# fall back to headless, so a missing display must never block a run.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

DISPLAY_NUM=":1"
export DISPLAY="${DISPLAY_NUM}"

log() { echo "[agentready-entrypoint] $*"; }

start_display() {
    # ── Xvfb (virtual display) ────────────────────────────────────────────────
    if [ -S "/tmp/.X11-unix/X1" ]; then
        log "Xvfb already running on ${DISPLAY_NUM}"
    else
        log "Starting Xvfb on ${DISPLAY_NUM}…"
        Xvfb "${DISPLAY_NUM}" -screen 0 1280x800x24 -ac +extension RANDR \
            > /tmp/xvfb.log 2>&1 &
        for _ in $(seq 1 20); do
            sleep 0.5
            [ -S "/tmp/.X11-unix/X1" ] && break
        done
        if [ -S "/tmp/.X11-unix/X1" ]; then
            log "Xvfb ready"
        else
            log "WARNING: Xvfb did not come up — Playwright will fall back to headless"
            return 0
        fi
    fi

    # ── Openbox window manager (best-effort; apps open windows properly) ──────
    if ! pgrep -x openbox >/dev/null 2>&1; then
        log "Starting openbox…"
        DISPLAY="${DISPLAY_NUM}" openbox --sm-disable > /tmp/openbox.log 2>&1 &
        sleep 1
    fi
}

# Never let display setup abort the container — Playwright headless is the fallback.
start_display || log "WARNING: start_display exited non-zero (continuing headless-capable)"

# Ensure the screenshot sink exists and is writable even if a volume mount shadowed it.
mkdir -p /screenshots 2>/dev/null || true
chmod 0777 /screenshots 2>/dev/null || true

log "Display ready. Sandbox ready."

# If invoked with a command, exec it; otherwise keep the container alive so Daytona
# can attach sessions and run `opencode run …` against it.
if [ "$#" -gt 0 ]; then
    log "exec: $*"
    exec "$@"
else
    log "No command given — sleeping for Daytona session use."
    exec sleep infinity
fi
