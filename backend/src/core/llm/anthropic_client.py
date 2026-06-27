"""
Thin async Anthropic Messages API wrapper over httpx.

We deliberately do NOT depend on the `anthropic` SDK — httpx is already a
dependency and the surface we need (one POST to /v1/messages) is tiny. Each
call pulls a fresh key from the process-wide key_pool, so load spreads across
the configured pool and a rate-limited key gets rotated off on retry.

Design contract:
  - Defensive: these functions NEVER raise. claude_text returns "" and
    claude_json returns {} on any failure (no keys, network error, bad JSON).
  - If the key pool is empty, return immediately so callers degrade gracefully.
  - One retry on 429/5xx with a DIFFERENT pooled key — the whole reason a pool
    exists is to rotate off a rate-limited / overloaded key.
"""
from __future__ import annotations

import json

import httpx
from loguru import logger

from ..config import settings
from . import key_pool

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_TIMEOUT = 60.0  # seconds


def _headers(api_key: str) -> dict[str, str]:
    return {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }


def _extract_text(data: dict) -> str:
    """Concatenate the text of all content blocks in a Messages response."""
    parts: list[str] = []
    for block in data.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", "") or "")
    return "".join(parts)


async def _post(body: dict) -> dict | None:
    """POST to the Messages API with one key-rotating retry on 429/5xx.

    Returns the parsed response dict, or None on any unrecoverable failure.
    Never raises.
    """
    if not key_pool.has_keys():
        return None

    last_exc: Exception | None = None
    for attempt in range(2):  # initial try + one retry on a different key
        api_key = key_pool.next_key()
        if not api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(_API_URL, headers=_headers(api_key), json=body)
            if resp.status_code == 200:
                return resp.json()
            # Retry once on rate-limit / overload / server error with a fresh key.
            if resp.status_code in (429, 500, 502, 503, 529) and attempt == 0:
                logger.debug(
                    "claude_call: status %s on attempt %d — rotating key",
                    resp.status_code,
                    attempt,
                )
                continue
            logger.debug("claude_call: non-retryable status %s: %s", resp.status_code, resp.text[:200])
            return None
        except Exception as exc:  # network / timeout / json — try once more
            last_exc = exc
            logger.debug("claude_call: request failed on attempt %d: %s", attempt, exc)
            if attempt == 0:
                continue
            return None

    if last_exc is not None:
        logger.debug("claude_call: exhausted retries: %s", last_exc)
    return None


async def claude_text(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.4,
) -> str:
    """Send a single user prompt to Claude; return concatenated text. "" on failure."""
    body: dict = {
        "model": model or settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    data = await _post(body)
    if not data:
        return ""
    try:
        return _extract_text(data)
    except Exception as exc:
        logger.debug("claude_text: failed to extract text: %s", exc)
        return ""


def _extract_json_blob(text: str) -> dict:
    """Best-effort: parse `text` as JSON, else extract the last {...} blob."""
    text = text.strip()
    if not text:
        return {}
    # Fast path: whole response is JSON (possibly fenced).
    candidate = text
    if candidate.startswith("```"):
        # strip a ```json ... ``` fence
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    # Defensive: scan for the last {...} object, tolerating trailing junk by
    # using raw_decode (parses one JSON value from the start, ignores the rest).
    decoder = json.JSONDecoder()
    start = text.rfind("{")
    while start >= 0:
        try:
            parsed, _ = decoder.raw_decode(text[start:])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        start = text.rfind("{", 0, start)
    return {}


async def claude_json(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 2000,
) -> dict:
    """Ask Claude for JSON and parse it defensively. {} on failure.

    Instructs JSON-only output and extracts the last {...} blob from the reply,
    so a stray preamble doesn't break parsing.
    """
    json_system = (system + "\n\n" if system else "") + (
        "Respond with ONLY a single valid JSON object. No prose, no markdown "
        "fences, no explanation before or after."
    )
    text = await claude_text(
        prompt,
        system=json_system,
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
    )
    if not text:
        return {}
    return _extract_json_blob(text)
