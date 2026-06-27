# AgentReady harness sandbox image.
#
# Base = Microsoft's Playwright image: Chromium + all OS deps + the Node runtime
# OpenCode needs are already present. We layer Python 3.12 (for the Playwright
# Python driver the audit/enrichment/discovery skills use) and OpenCode on top.
#
# The audit skill drives a REAL headless Chromium and writes COMPRESSED JPEG
# frames to /screenshots/NNNN.jpg (+ NNNN.json sidecars) so the backend can
# stream the agent's run live. /screenshots is created world-writable here.
FROM mcr.microsoft.com/playwright:v1.49.0-jammy

# Node is already in the Playwright image.
RUN node --version

# Python 3.12 + tooling (git for the fix-skill clone, curl for raw endpoint probes).
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3-pip python3.12-venv git curl ca-certificates \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# OpenCode CLI (the agent runtime the backend invokes via `opencode run`).
RUN npm install -g opencode-ai@latest 2>/dev/null \
    || npm install -g @opencodecli/opencode@latest 2>/dev/null \
    || true

# Python deps:
#  - playwright: the Python browser driver the skills script against
#  - httpx/requests: raw machine-endpoint probing (status codes, headers, bodies)
RUN pip3 install --break-system-packages playwright httpx requests \
    || pip3 install playwright httpx requests

# The Chromium browser binary is already installed in the base image under
# /ms-playwright; point the Python driver at it so we don't re-download, and
# install the driver bindings. `playwright install-deps` is a no-op here (deps
# present) but kept for portability.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python3 -m playwright install chromium 2>/dev/null || true

# Screenshot sink for the audit skill: world-writable so the agent (whatever uid
# the sandbox runs as) can write frames.
RUN mkdir -p /screenshots && chmod 0777 /screenshots

# Smoke-test: prove the Python Playwright driver can launch Chromium AND write a
# valid compressed JPEG screenshot to /screenshots — fail the BUILD if it can't,
# so a broken image never ships.
RUN python3 - <<'PY'
from playwright.sync_api import sync_playwright
import os
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    pg.set_content("<h1>agentready screenshot smoke-test</h1>")
    pg.screenshot(path="/screenshots/0000.jpg", type="jpeg", quality=60)
    b.close()
size = os.path.getsize("/screenshots/0000.jpg")
hdr = open("/screenshots/0000.jpg", "rb").read(3)
assert hdr == b"\xff\xd8\xff", f"not a JPEG: {hdr!r}"
assert 0 < size < 150_000, f"unexpected JPEG size: {size}"
os.remove("/screenshots/0000.jpg")
print(f"JPEG screenshot OK ({size} bytes)")
PY

WORKDIR /workspace

CMD ["/bin/bash"]
