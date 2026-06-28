#!/usr/bin/env python3
"""
Build the Wirable agent sandbox snapshot in Daytona (declarative — no external
registry). Bakes agent-browser (Vercel Labs) + Chrome for Testing + Python so
every audit sandbox boots ready to drive a real browser.

Run inside the backend container:
    docker exec wirable-backend-1 python /app/scripts/build_snapshot.py
"""
import asyncio
import os
import sys

from daytona import (
    AsyncDaytona,
    CreateSnapshotParams,
    DaytonaConfig,
    Image,
    Resources,
)

SNAPSHOT_NAME = os.environ.get("AGENTREADY_SANDBOX_IMAGE", "wirable-agent")


def _client() -> AsyncDaytona:
    key = os.environ.get("DAYTONA_API_KEY", "")
    url = (os.environ.get("DAYTONA_API_URL") or "").strip()
    if url and "app.daytona.io" not in url.replace("/api", ""):
        return AsyncDaytona(DaytonaConfig(api_key=key, api_url=url))
    return AsyncDaytona(DaytonaConfig(api_key=key))


def _image() -> Image:
    # Base on Daytona's own sandbox image so the Daytona daemon (sessions /
    # process exec) is present — a bare node/debian base lacks it, which makes
    # create_session fail ("generator didn't yield"). Layer our tools on top.
    return (
        Image.base("daytonaio/sandbox:0.8.0")
        .run_commands(
            "sudo apt-get update && sudo apt-get install -y --no-install-recommends python3 python3-pip ca-certificates curl || apt-get update && apt-get install -y --no-install-recommends python3 python3-pip ca-certificates curl || true",
            "sudo ln -sf /usr/bin/python3 /usr/bin/python || ln -sf /usr/bin/python3 /usr/bin/python || true",
            # Vercel Labs agent-browser (Rust CLI via npm) + Chrome for Testing (+deps)
            "sudo npm install -g agent-browser || npm install -g agent-browser",
            "agent-browser install --with-deps || agent-browser install",
            "agent-browser --version || true",
        )
    )


async def main() -> int:
    client = _client()
    params = CreateSnapshotParams(
        name=SNAPSHOT_NAME,
        image=_image(),
        resources=Resources(cpu=2, memory=4, disk=10),
    )
    print(f"building snapshot '{SNAPSHOT_NAME}' …", flush=True)

    # snapshot namespace differs slightly across SDK builds — resolve defensively
    ns = getattr(client, "snapshot", None) or getattr(client, "snapshots", None)
    create = getattr(ns, "create", None) if ns else getattr(client, "create_snapshot", None)
    if create is None:
        print("ERROR: no snapshot.create on this SDK:", dir(client), file=sys.stderr)
        return 2

    try:
        res = create(params, on_logs=lambda m: print(m, flush=True))
        if asyncio.iscoroutine(res):
            res = await res
        print("snapshot created:", getattr(res, "name", res))
        return 0
    except Exception as e:  # noqa: BLE001
        print("snapshot build failed:", repr(e), file=sys.stderr)
        return 1
    finally:
        try:
            c = getattr(client, "close", None)
            if c:
                r = c()
                if asyncio.iscoroutine(r):
                    await r
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
