# backend/src/api/v1/endpoints/llm_proxy.py
"""
OpenAI-compatible LiteLLM proxy for OpenCode sandboxes.

OpenCode sends standard chat/completions requests here.
A short-lived HMAC-signed token in the Authorization header carries the
real provider API key + LiteLLM model string so the proxy can route them
correctly — regardless of which @ai-sdk/* version OpenCode ships.

Endpoint registered at:  /api/v1/llm-proxy/chat/completions
OpenCode config sets:    baseURL = "<backend_url>/api/v1/llm-proxy"

Note: @ai-sdk/openai-compatible appends /chat/completions to the baseURL
directly (no automatic /v1 prefix), so the route must be /chat/completions.

Streaming design:
  The proxy calls LiteLLM with stream=True and forwards each token chunk as
  an SSE frame.  To maintain clean HTTP-level error surfacing (so @ai-sdk/
  openai-compatible sees a 502 rather than a silent empty stream), we peek
  at the first chunk before starting the StreamingResponse.  If LiteLLM
  raises before yielding anything, we return 502 directly.  Errors mid-stream
  are logged and the stream is closed cleanly with [DONE].
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, AsyncGenerator, Optional

import litellm
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from src.db.redis import get_redis_pool

router = APIRouter()

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# Per-org limits (keyed by sha256 of decoded api_key — stable across token rotations)
_RPM_LIMIT = 120  # max requests per minute per org key
_CONCURRENT_LIMIT = 40  # max simultaneous streaming connections per org key
_DAILY_LIMIT = 50_000  # hard daily cap — one run ≈ 500 calls; allows ~100 runs/day
_AUTH_FAILURE_LOG_TTL_S = 120
_KEY_FINGERPRINT_LEN = 8
# A single in-flight LLM call almost never runs longer than this. Concurrent slots
# older than the window are treated as leaked (a worker that died mid-call without
# releasing its slot) and pruned on the next request — so the counter self-heals
# instead of climbing to _CONCURRENT_LIMIT and wedging every run with 429s.
_CONC_STALE_S = 300


def _rate_key(api_key: str) -> str:
    """Stable, non-reversible key derived from the decoded api_key (not the raw token).

    Previously keyed by token[:32], which mapped all tokens for the same org to
    the same hash (since the api_key dominates the first 32 base64 chars) BUT also
    accumulated counts across deployments until the daily cap was hit.  Now we key
    explicitly on the api_key so the semantic is clear: one counter per LLM key.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:24]


async def _enforce_rate_limits(api_key: str, redis) -> None:
    """
    Sliding-window RPM + daily cap check.
    Raises HTTP 429 if any limit is exceeded.
    Concurrent connection tracking is done in the streaming path (see _proxy_chat_completions_handler).
    """
    key = _rate_key(api_key)
    window = int(time.time()) // 60  # 1-minute bucket
    day = int(time.time()) // 86400  # 1-day bucket

    rpm_key = f"llm_proxy:rpm:{key}:{window}"
    daily_key = f"llm_proxy:daily:{key}:{day}"

    # Increment both counters atomically (pipeline)
    pipe = redis.pipeline()
    pipe.incr(rpm_key)
    pipe.expire(rpm_key, 120)  # 2-minute TTL covers current + next window
    pipe.incr(daily_key)
    pipe.expire(daily_key, 90_000)  # 25h TTL
    rpm_count, _, daily_count, _ = await pipe.execute()

    if rpm_count > _RPM_LIMIT:
        retry_after = 60 - (int(time.time()) % 60)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {_RPM_LIMIT} requests/minute. Retry in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )
    if daily_count > _DAILY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Daily limit exceeded: {_DAILY_LIMIT} requests/day for this API key.",
            headers={"Retry-After": str(86400 - int(time.time()) % 86400)},
        )


def _conc_key(api_key: str) -> str:
    # Distinct key + ":z" suffix from the legacy ``llm_proxy:concurrent:*`` STRING
    # counter. The new gate is a ZSET; reusing the old name would hit WRONGTYPE on
    # the leftover string value. The legacy keys simply age out via their TTL.
    return f"llm_proxy:concurrent:z:{_rate_key(api_key)}"


async def _acquire_conc_slot(api_key: str, redis) -> tuple[str, str]:
    """Reserve one concurrent slot using a self-healing sorted set.

    Each in-flight call is a ZSET member (unique id) scored by its start time.
    Before reserving, members older than ``_CONC_STALE_S`` are pruned — these are
    leaked slots from workers that died mid-call without releasing.

    Returns ``(member, key)`` on success. Raises HTTP 429 if the live count (after
    pruning) is already at the cap. Always pass the returned ``member``/``key`` to
    ``_release_conc_slot`` in a finally so the slot is freed.

    This replaces a plain INCR/DECR counter, which leaked permanently whenever the
    DECR was skipped (upstream error path, killed worker) and never recovered
    because its TTL was refreshed on every request.
    """
    import uuid as _uuid

    key = _conc_key(api_key)
    now = time.time()
    member = _uuid.uuid4().hex

    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, "-inf", now - _CONC_STALE_S)  # drop leaked slots
    pipe.zcard(key)
    results = await pipe.execute()
    live_count = results[1] if len(results) > 1 and isinstance(results[1], int) else 0

    if live_count >= _CONCURRENT_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent connections ({_CONCURRENT_LIMIT} max per token).",
            headers={"Retry-After": "10"},
        )

    pipe = redis.pipeline()
    pipe.zadd(key, {member: now})
    pipe.expire(key, _CONC_STALE_S * 2)  # idle keys disappear; no permanent buildup
    await pipe.execute()
    return member, key


async def _release_conc_slot(redis, key: str, member: str) -> None:
    try:
        await redis.zrem(key, member)
    except Exception:  # nosec
        pass


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def make_proxy_token(api_key: str, model: str, secret: str, ttl: int = 7200) -> str:
    """
    Create a compact signed token:  base64url(json_payload).hmac_sig
    Payload JSON: {"k": api_key, "m": model, "e": expiry_unix}
    Signature: HMAC-SHA256(base64url_payload, secret)[:32 hex chars]
    """
    import base64

    exp = int(time.time()) + ttl
    payload_bytes = json.dumps({"k": api_key, "m": model, "e": exp}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")
    sig = hmac.new(
        secret.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"{payload_b64}.{sig}"


def decode_proxy_token(token: str, secret: str) -> dict[str, str]:
    """Decode and verify a token produced by make_proxy_token."""
    import base64

    try:
        payload_b64, sig = token.rsplit(".", 1)
    except ValueError:
        raise ValueError("malformed proxy token")

    expected_sig = hmac.new(
        secret.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]

    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("invalid proxy token signature")

    padding = "=" * (4 - len(payload_b64) % 4)
    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes)
    except Exception:
        raise ValueError("malformed proxy token payload")

    if payload.get("e", 0) < int(time.time()):
        raise ValueError("proxy token expired")

    return {"api_key": payload["k"], "model": payload["m"]}


# ---------------------------------------------------------------------------
# Streaming generator
# ---------------------------------------------------------------------------


async def _stream_chunks(
    first_chunk: Any,
    response_iter: Any,
    model: str,
) -> AsyncGenerator[bytes]:
    """
    Forward LiteLLM streaming chunks as SSE frames.
    `first_chunk` has already been peeked; forward it then drain the rest.
    """

    def _serialise(chunk: Any) -> Optional[bytes]:
        try:
            if hasattr(chunk, "model_dump"):
                data = chunk.model_dump(exclude_unset=True)
            elif isinstance(chunk, dict):
                data = chunk
            else:
                return None
            return f"data: {json.dumps(data)}\n\n".encode()
        except Exception:
            return None

    # Emit the peeked first chunk
    frame = _serialise(first_chunk)
    if frame:
        yield frame

    # Drain the rest
    try:
        async for chunk in response_iter:
            frame = _serialise(chunk)
            if frame:
                yield frame
    except Exception as exc:
        logger.warning(f"llm_proxy: stream error mid-flight model={model} err={exc}")

    yield b"data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/health")
async def proxy_health():
    """Reachability probe used by sandbox curl check."""
    return {"ok": True}


async def _log_auth_failure(reason: str, request: Request, redis=None) -> None:
    reason_slug = reason.replace(" ", "_")[:64]
    log_key = f"llm_proxy:auth_fail:{reason_slug}:{int(time.time()) // _AUTH_FAILURE_LOG_TTL_S}"

    should_log = True
    if redis is not None:
        try:
            should_log = await redis.set(
                log_key, "1", ex=_AUTH_FAILURE_LOG_TTL_S, nx=True
            )
        except Exception:
            should_log = True

    if should_log:
        logger.warning(
            f"llm_proxy: auth rejected reason={reason!r} path={request.url.path} "
            f"ua={request.headers.get('user-agent', '-')[:80]}"
        )


def _upstream_status_code(exc: Exception) -> int:
    return int(getattr(exc, "status_code", 0) or getattr(exc, "status", 0) or 0)


def _upstream_retry_after(exc: Exception) -> str | None:
    headers = getattr(exc, "headers", None)
    if isinstance(headers, dict):
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after:
            return str(retry_after)
    return None


async def _proxy_chat_completions_handler(request: Request, redis=None):
    from src.core.settings import get_settings

    settings = get_settings()
    secret = settings.AUTH_SECRET_KEY

    # ── Auth ──────────────────────────────────────────────────────────────────
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth_header.removeprefix("Bearer ").strip()

    try:
        payload = decode_proxy_token(token, secret)
    except ValueError as exc:
        await _log_auth_failure(str(exc), request, redis=redis)
        raise HTTPException(status_code=401, detail=str(exc))

    api_key = payload["api_key"] or ""
    model = payload["model"]

    # Fall back to the system-level Crossnode API key when the token carries none.
    # This covers public/audit runs and orgs that rely on the platform default key.
    if not api_key:
        api_key = settings.CROSSNODE_ASSISTANT_API_KEY or ""

    if not api_key:
        raise HTTPException(
            status_code=502,
            detail="No LLM API key available — configure CROSSNODE_ASSISTANT_API_KEY.",
        )

    key_fp = _rate_key(api_key)[:_KEY_FINGERPRINT_LEN]

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Concurrent slot is a self-healing ZSET: leaked slots (worker died mid-call)
    # age out after _CONC_STALE_S instead of wedging the key at the cap forever.
    conc_key = None
    conc_member = None
    if redis is not None:
        await _enforce_rate_limits(api_key, redis)
        conc_member, conc_key = await _acquire_conc_slot(api_key, redis)

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    wants_stream: bool = body.get("stream", False)

    extra: dict[str, Any] = {}
    for key in ("max_tokens", "temperature", "top_p", "stop", "tools", "tool_choice"):
        if key in body:
            extra[key] = body[key]

    logger.info(
        f"llm_proxy: request model={model} stream={wants_stream} msgs={len(messages)} "
        f"path={request.url.path} key_fp={key_fp}"
    )

    # ── Start streaming from LiteLLM ──────────────────────────────────────────
    # We peek at the first chunk before returning any HTTP response.
    # If LiteLLM raises before yielding (auth error, invalid model, etc.)
    # we surface it as HTTP 502, which @ai-sdk/openai-compatible handles
    # correctly (unlike a silent empty SSE stream that causes a 45s stall).
    try:
        response_iter = await litellm.acompletion(
            model=model,
            messages=messages,
            api_key=api_key,
            stream=True,
            **{k: v for k, v in extra.items() if k != "stream"},
        )
        # Peek: consume first chunk to trigger immediate auth/model validation
        first_chunk = None
        async for chunk in response_iter:
            first_chunk = chunk
            break
    except Exception as exc:
        # Release the concurrent slot — this error path previously leaked it,
        # which is how the counter climbed to the cap and 429'd every run.
        if redis is not None and conc_key and conc_member:
            await _release_conc_slot(redis, conc_key, conc_member)

        status_code = _upstream_status_code(exc)
        retry_after = _upstream_retry_after(exc)

        if 400 <= status_code < 500:
            logger.warning(
                f"llm_proxy: upstream client error model={model} status={status_code} "
                f"path={request.url.path} key_fp={key_fp}"
            )
            response_headers = {"Retry-After": retry_after} if retry_after else None
            raise HTTPException(
                status_code=status_code, detail=str(exc), headers=response_headers
            )

        logger.error(
            f"llm_proxy: upstream failure model={model} status={status_code or 502} "
            f"path={request.url.path} key_fp={key_fp} err={exc}"
        )
        raise HTTPException(status_code=502, detail=str(exc))

    if wants_stream:

        async def _guarded_stream() -> AsyncGenerator[bytes]:
            try:
                async for chunk in _stream_chunks(first_chunk, response_iter, model):
                    yield chunk
            finally:
                if redis is not None and conc_key and conc_member:
                    await _release_conc_slot(redis, conc_key, conc_member)

        return StreamingResponse(
            _guarded_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming caller: collect all chunks into a single response object
    import uuid as _uuid

    content_parts = []
    finish_reason = "stop"
    resp_id = None
    resp_model = model

    def _extract_delta(c: Any) -> str:
        try:
            return c.choices[0].delta.content or ""
        except Exception:
            return ""

    if first_chunk is not None:
        content_parts.append(_extract_delta(first_chunk))
        resp_id = getattr(first_chunk, "id", None)
        resp_model = getattr(first_chunk, "model", model) or model

    async for chunk in response_iter:
        content_parts.append(_extract_delta(chunk))
        try:
            fr = chunk.choices[0].finish_reason
            if fr:
                finish_reason = fr
        except Exception:  # nosec
            pass

    content = "".join(content_parts)
    # Release concurrent slot for non-streaming path
    if redis is not None and conc_key and conc_member:
        await _release_conc_slot(redis, conc_key, conc_member)
    return {
        "id": resp_id or f"chatcmpl-{_uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "model": resp_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# Register both URL patterns — different versions of @ai-sdk/openai-compatible
# append either /chat/completions or /v1/chat/completions to the baseURL.
@router.post("/chat/completions")
async def proxy_chat_completions(
    request: Request,
    redis=Depends(get_redis_pool),
):
    """OpenAI-compatible chat completions proxy (no-version path)."""
    return await _proxy_chat_completions_handler(request, redis)


@router.post("/v1/chat/completions")
async def proxy_chat_completions_v1(
    request: Request,
    redis=Depends(get_redis_pool),
):
    """OpenAI-compatible chat completions proxy (versioned path)."""
    return await _proxy_chat_completions_handler(request, redis)
