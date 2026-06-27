# ──────────────────────────────────────────────────────────────────────────────
# AgentReady sandbox image
# ──────────────────────────────────────────────────────────────────────────────
# This is the container the audit/fix OpenCode agents run inside, launched by the
# backend via Daytona (CreateSandboxFromImageParams, image = AGENTREADY_SANDBOX_IMAGE).
#
# It mirrors Crossnode's docker/sandbox/ image but is intentionally simpler: the
# AgentReady audit agent captures COMPRESSED JPEG screenshots to FILES
# (/screenshots/NNNN.jpg + NNNN.json sidecars) which the backend tails into a live
# SSE stream — so we do NOT need Crossnode's agent-browser WebSocket→MJPEG bridge,
# CDP daemon, or socat plumbing. A virtual display (Xvfb) + Chromium + Playwright +
# OpenCode, runnable by the `daytona` user, is all the audit/fix skills need.
#
# CRITICAL: Daytona's SDK execs every command as the `daytona` user (uid 1000).
# OpenCode, Chromium, the Playwright driver, and /screenshots must all be
# installed for / writable by that user — get the uid wrong and runs silently no-op.
# ──────────────────────────────────────────────────────────────────────────────
FROM ubuntu:22.04

ARG NODE_MAJOR=20
# Pin OpenCode for reproducibility; the backend also self-heals via npm if missing.
ARG OPENCODE_VERSION=1.17.6

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    DISPLAY=:1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# ── System packages ───────────────────────────────────────────────────────────
# Base tooling + a virtual display + every shared library Chromium/Playwright
# needs. The lib list is copied verbatim from Crossnode's sandbox Dockerfile
# (the canonical, battle-tested set for headed Chromium on Ubuntu 22.04).
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Base tools
    ca-certificates curl wget gnupg lsb-release git \
    build-essential python3 python3-pip python3-dev unzip sudo \
    # Virtual display (lets Chromium run headed under Xvfb)
    xvfb x11-utils xauth \
    # Computer-use helpers — desktop automation & raw screenshots
    xdotool scrot \
    # Lightweight window manager (apps open windows properly under Xvfb)
    openbox \
    # Playwright / Chromium runtime shared libraries
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 \
    libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
    libx11-6 libx11-xcb1 libxcb1 libxext6 libxss1 \
    libxtst6 libxrender1 libxi6 \
    fonts-liberation fontconfig libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 20 (NodeSource) ───────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest \
    && rm -rf /var/lib/apt/lists/*

# ── daytona user (Daytona SDK default os_user, uid 1000) ──────────────────────
# Daytona execs as this exact user — everything below must be usable by it.
RUN useradd -m -u 1000 -s /bin/bash daytona \
    && echo "daytona ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && mkdir -p /home/daytona/.config /home/daytona/.cache /home/daytona/.local/bin \
    && chown -R daytona:daytona /home/daytona

# ── OpenCode (the agent runtime the backend invokes via `opencode run`) ───────
# Try the npm wrapper first (matches AgentReady's prior harness + the backend's
# self-heal `npm install -g opencode-ai@latest`). The wrapper's postinstall can
# fail in Docker buildx, so fall back to installing the linux-x64 binary package
# directly (Crossnode's approach) and symlinking it onto PATH.
RUN set -ex \
    && ( npm install -g opencode-ai@${OPENCODE_VERSION} 2>/dev/null \
         || npm install -g opencode-ai@latest 2>/dev/null \
         || true ) \
    && if ! command -v opencode >/dev/null 2>&1; then \
         echo "npm wrapper unavailable — installing linux-x64 binary package…" ; \
         TMP="$(mktemp -d)" ; \
         curl -sSL "https://registry.npmjs.org/opencode-linux-x64/-/opencode-linux-x64-${OPENCODE_VERSION}.tgz" \
              -o "$TMP/opencode.tgz" ; \
         tar -xzf "$TMP/opencode.tgz" -C "$TMP" ; \
         cp "$TMP/package/bin/opencode" /usr/local/bin/opencode ; \
         chmod +x /usr/local/bin/opencode ; \
         rm -rf "$TMP" ; \
       fi \
    && command -v opencode >/dev/null 2>&1 \
    && (opencode --version 2>/dev/null || true)

# ── Playwright + Chromium (Python driver, used by the audit/fix skills) ───────
# Install the Python bindings the skills script against, plus the HTTP probing
# libs (httpx/requests for raw machine-endpoint probes). Chromium is installed to
# /opt/playwright (world-readable) so it survives Daytona's workspace volume mount
# over /home/daytona at runtime — PLAYWRIGHT_BROWSERS_PATH points the driver there.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN pip3 install --no-cache-dir playwright httpx requests \
    && playwright install --with-deps chromium 2>&1 | tail -8 \
    && chmod -R a+rX /opt/playwright

# ── Screenshot sink ───────────────────────────────────────────────────────────
# The audit agent writes /screenshots/NNNN.jpg (+ NNNN.json sidecars); the backend
# tails this directory into the live SSE stream. World-writable so the agent can
# write regardless of the uid Daytona runs as.
RUN mkdir -p /screenshots && chmod 0777 /screenshots

# ── Build-time smoke test ─────────────────────────────────────────────────────
# Fail the BUILD (not a runtime job) if the toolchain is broken: prove node,
# opencode, and chromium are present, AND that the Python Playwright driver can
# launch Chromium and emit a valid compressed JPEG to /screenshots. A broken image
# never ships.
RUN set -ex \
    && node --version \
    && (opencode --version 2>/dev/null || echo "opencode present (version probe noisy)") \
    && python3 - <<'PY'
import os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"])
    print("chromium version:", b.version)
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.set_content("<h1>agentready sandbox screenshot smoke-test</h1>")
    pg.screenshot(path="/screenshots/0000.jpg", type="jpeg", quality=60)
    b.close()

size = os.path.getsize("/screenshots/0000.jpg")
hdr = open("/screenshots/0000.jpg", "rb").read(3)
assert hdr == b"\xff\xd8\xff", f"not a JPEG: {hdr!r}"
assert 0 < size < 150_000, f"unexpected JPEG size: {size}"
os.remove("/screenshots/0000.jpg")
print(f"JPEG screenshot OK ({size} bytes)")
PY

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Marker env read by the backend (mirrors Crossnode's CROSSNODE_PREBUILT) — lets
# the harness skip redundant runtime installs when it sees a prebuilt image.
ENV AGENTREADY_PREBUILT=1 \
    DISPLAY=:1

# Daytona execs as the daytona user; default to it so interactive/CMD shells and
# `opencode run` land with the right HOME (~/.config/opencode is where the backend
# writes the OpenCode config).
USER daytona
WORKDIR /home/daytona
ENV HOME=/home/daytona PATH="/home/daytona/.local/bin:$PATH"

CMD ["/entrypoint.sh"]
