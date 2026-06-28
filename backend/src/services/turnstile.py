"""
Cloudflare Turnstile verification — bot check for email/password signup.

Dev bypass: when TURNSTILE_SECRET_KEY is empty, verify() returns True and logs
a warning. With a secret configured, it fails CLOSED — any error returns False.
"""
from __future__ import annotations

import logging

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


async def verify(token: str, remoteip: str | None = None) -> bool:
    """Verify a Turnstile token with Cloudflare.

    Returns True/False. When no secret is configured, bypasses the check
    (dev) and logs a warning. With a secret set, any failure -> False.
    """
    secret = (settings.TURNSTILE_SECRET_KEY or "").strip()
    if not secret:
        logger.warning("[turnstile] TURNSTILE_SECRET_KEY unset — bypassing bot check (dev only)")
        return True

    if not token:
        return False

    data = {"secret": secret, "response": token}
    if remoteip:
        data["remoteip"] = remoteip

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_SITEVERIFY_URL, data=data)
            resp.raise_for_status()
            payload = resp.json()
            return bool(payload.get("success", False))
    except Exception:
        logger.exception("[turnstile] verification failed — failing closed")
        return False
