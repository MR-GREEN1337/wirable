# AgentReady sandbox image

This is the container the AgentReady **audit / fix OpenCode agents run inside**.
The backend launches one isolated sandbox per job via Daytona
(`DaytonaClient.sandbox()` → `CreateSandboxFromImageParams`), uploads the task
prompt, runs `opencode run`, and reads back `/output.json`.

It mirrors Crossnode's `docker/sandbox/` image but is intentionally leaner:
AgentReady captures screenshots to **files**, so there is no agent-browser CDP
daemon, no WebSocket→MJPEG stream bridge, and no socat plumbing. A virtual
display + Chromium + Playwright + OpenCode, all runnable by the `daytona` user,
is everything the audit/fix skills need.

## What's inside

| Component | Why |
|-----------|-----|
| `ubuntu:22.04` base | matches Crossnode's sandbox base |
| `daytona` user, **uid 1000**, NOPASSWD sudo | **critical** — Daytona's SDK execs every command as this exact user/uid. OpenCode, Chromium, and `~/.config/opencode` must be usable by it |
| Node 20 (NodeSource) | the OpenCode runtime |
| OpenCode (`opencode-ai@1.17.6`, binary fallback) | the agent runtime the backend invokes via `opencode run` |
| Python 3 + `playwright httpx requests` | the audit/fix skills script Playwright in Python and probe machine endpoints with httpx/curl |
| Chromium (Playwright-managed, in `/opt/playwright`) | a real browser to drive against live sites; lives outside `/home/daytona` so it survives Daytona's workspace volume mount |
| Xvfb + openbox + xdotool/scrot | a virtual display so headed Chromium works; computer-use helpers |
| Chromium/Playwright shared libs | the full runtime dep list copied from Crossnode's sandbox |
| `/screenshots` (world-writable `0777`) | the audit agent's frame sink (see below) |

A **build-time smoke test** fails the build unless `node`, `opencode`, and a
Playwright-launched Chromium can all run AND emit a valid compressed JPEG to
`/screenshots`. A broken image never ships.

## Screenshots → live stream

The audit skill drives a real Chromium and writes, for each step:

- `/screenshots/NNNN.jpg` — compressed JPEG (~1280×800, quality ~60, <150 KB),
  zero-padded 4-digit index strictly increasing from `0001`.
- `/screenshots/NNNN.json` — sidecar: `{"caption", "dimension", "url"}`.

The backend tails `/screenshots/` and relays each new frame over its live SSE
stream, so the operator watches the agent literally try to use the audited
product in real time. `/screenshots` is `0777` so the agent can write it
regardless of the uid the sandbox runs as.

## Build & push

```sh
chmod +x build.sh entrypoint.sh

# Build locally, tag agentready-sandbox:latest (no push):
./build.sh

# Build AND push to a registry the Daytona fleet can pull from:
./build.sh ghcr.io/you/agentready-sandbox:latest
# or:  ./build.sh us-docker.pkg.dev/PROJECT/agentready/sandbox:latest
```

The build targets `linux/amd64` (Daytona's fleet arch) via `docker buildx`, so it
works from an Apple-silicon dev machine.

## Wire it into the backend

The backend resolves the sandbox image from the `AGENTREADY_SANDBOX_IMAGE` env
var (mirroring Crossnode's `CROSSNODE_SANDBOX_IMAGE`), falling back to the baked
`agentready-harness:latest` constant, then to Daytona's default `python` snapshot
if the image is unavailable in the registry.

Set, in the backend environment / `.env`:

```sh
AGENTREADY_SANDBOX_IMAGE=agentready-sandbox:latest
# …or the pushed registry ref you built, e.g.
# AGENTREADY_SANDBOX_IMAGE=ghcr.io/you/agentready-sandbox:latest
```

Resolution logic: `wirable/backend/src/core/sandbox/daytona_client.py::_resolve_image()`.

### Fallback behavior

If `AGENTREADY_SANDBOX_IMAGE` is unset, or the resolved image can't be created in
Daytona, `sandbox()` catches the error and falls back to the Daytona default
`python` snapshot. In that fallback the backend self-heals OpenCode
(`npm install -g opencode-ai@latest`), but there is **no preinstalled Chromium /
Playwright / Xvfb** — so screenshots and live browser auditing only work fully
when this image is built and `AGENTREADY_SANDBOX_IMAGE` points at it.

## Entrypoint

`entrypoint.sh` starts Xvfb on `:1` (so headed Chromium works), starts openbox,
ensures `/screenshots` is writable, then execs the passed command or
`sleep infinity` for Daytona session use. It is defensive: if Xvfb fails it still
continues — Playwright falls back to headless, so a missing display never blocks a
run.

## Tree

```
wirable/docker/sandbox/
├── Dockerfile     # ubuntu:22.04 + daytona(uid1000) + Node20 + OpenCode + Python/Playwright/Chromium + Xvfb; smoke-tested
├── build.sh       # build + tag agentready-sandbox:latest; optional registry arg to push
├── entrypoint.sh  # start Xvfb :1 then exec command / keep alive (headless-safe fallback)
└── README.md      # this file
```
