"""
Tiny in-process IP rate limiter — a launch safety-net against scripted abuse of
the cost-sensitive endpoints (guest signup → free runs → Daytona+Claude spend).

Sliding-window, in-memory (per backend process; resets on restart). Generous
limits so legitimate Product Hunt traffic (even behind a shared corporate NAT)
is never blocked, but a single IP hammering thousands of requests is stopped.

Usage (FastAPI dependency):
    @router.post("/run", dependencies=[Depends(rate_limit("run", 15, 3600))])
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

# bucket -> ip -> deque[timestamps]
_HITS: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))


def _client_ip(request: Request) -> str:
    # Behind Traefik/Coolify: trust the first X-Forwarded-For hop, else peer.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str, limit: int, window_s: int):
    """Return a dependency that allows `limit` requests per `window_s` per IP."""

    async def _dep(request: Request) -> None:
        ip = _client_ip(request)
        now = time.monotonic()
        dq = _HITS[bucket][ip]
        cutoff = now - window_s
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry = int(dq[0] + window_s - now) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests — slow down and try again shortly.",
                headers={"Retry-After": str(max(retry, 1))},
            )
        dq.append(now)
        # Opportunistic cleanup so the dict doesn't grow unbounded.
        if len(_HITS[bucket]) > 10000:
            for k in [k for k, v in _HITS[bucket].items() if not v or v[-1] < cutoff][:5000]:
                _HITS[bucket].pop(k, None)

    return Depends(_dep)
