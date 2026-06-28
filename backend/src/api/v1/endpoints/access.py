"""
Access + billing endpoints (Wirable) — the launch-gate control surface.

Runs are gated (POST /run requires an account + free quota; see endpoints/run.py).
This router lets the frontend read a user's entitlement status, redeem a judge /
internal access code for unlimited free access, and start a paid upgrade via
Stripe Checkout. Stripe stays fully behind env: the `stripe` lib is imported
lazily, and routes degrade to 503 when keys aren't configured — so the app
imports + runs fine with no billing set up.

  GET  /api/v1/access/status                 (authed) -> entitlement status
  POST /api/v1/access/redeem  {code}         (authed) -> redeem an access code
  POST /api/v1/billing/checkout              (authed) -> {url} (Stripe session)
  POST /api/v1/billing/webhook               (PUBLIC) -> Stripe webhook sink
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.config import settings
from ....core.ratelimit import rate_limit
from ....core.database import get_session, AsyncSessionLocal
from ....services import entitlements

# One module, two prefixes (access + billing) — exported as a single router by
# mounting a child router for /billing onto the /access one would mix prefixes,
# so we expose two routers and the v1 router includes both.
router = APIRouter(prefix="/access", tags=["access"])
billing_router = APIRouter(prefix="/billing", tags=["billing"])


def _user_id(user: dict) -> str:
    sub = user.get("sub")
    try:
        return str(_uuid.UUID(str(sub)))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid user id in token")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RedeemRequest(BaseModel):
    code: str


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


@router.get("/status")
async def access_status(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Current entitlement status: {tier, runs_used, runs_limit, remaining, unlimited}."""
    return await entitlements.status(db, _user_id(user))


@router.post("/redeem", dependencies=[rate_limit("redeem", 15, 600)])
async def access_redeem(
    body: RedeemRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Redeem a judge/internal access code -> unlimited access. 400 if invalid."""
    return await entitlements.redeem_code(db, _user_id(user), body.code)


# ---------------------------------------------------------------------------
# Billing (Stripe) — fully behind env; lazy import so a missing lib never breaks
# ---------------------------------------------------------------------------


def _load_stripe():
    """Import + configure the stripe lib, or return None if unavailable.

    Returns None (caller -> 503) when the lib isn't installed OR the secret key
    isn't set. Never raises on import.
    """
    if not settings.STRIPE_SECRET_KEY:
        return None
    try:
        import stripe  # type: ignore
    except Exception:
        logger.warning("[billing] stripe lib not importable; billing disabled")
        return None
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


@billing_router.post("/checkout")
async def billing_checkout(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Create a Stripe Checkout session for the paid upgrade -> {url}.

    503 when Stripe isn't configured (no secret key / price id, or lib missing).
    The user id is stored in the session metadata so the webhook can grant_paid.
    """
    if not settings.STRIPE_PRICE_ID:
        raise HTTPException(status_code=503, detail="billing not configured")
    stripe = _load_stripe()
    if stripe is None:
        raise HTTPException(status_code=503, detail="billing not configured")

    uid = _user_id(user)
    base = settings.APP_BASE_URL.rstrip("/")
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": settings.STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{base}/dashboard?paid=1",
            cancel_url=f"{base}/dashboard",
            metadata={"user_id": uid},
            client_reference_id=uid,
            subscription_data={"metadata": {"user_id": uid}},
        )
    except Exception as exc:
        logger.exception("[billing] checkout session create failed")
        raise HTTPException(status_code=502, detail=f"stripe error: {exc}")

    return {"url": session.get("url") if isinstance(session, dict) else session.url}


@billing_router.post("/webhook")
async def billing_webhook(request: Request):
    """Stripe webhook sink (UNAUTHENTICATED — Stripe calls this).

    Verifies the signature when STRIPE_WEBHOOK_SECRET is set. On a successful
    checkout / payment, resolves the user from session metadata and marks them
    paid. Returns {ok:true} fast; fully defensive (never 500s the webhook).
    """
    body = await request.body()
    stripe = _load_stripe()

    event: dict = {}
    secret = settings.STRIPE_WEBHOOK_SECRET
    sig = request.headers.get("stripe-signature")

    if stripe is not None and secret:
        # Enforce signature verification when both the lib and secret are present.
        try:
            event = stripe.Webhook.construct_event(body, sig, secret)
            if not isinstance(event, dict):
                event = dict(event)  # stripe object -> dict-like
        except Exception:
            logger.warning("[billing] webhook signature verification failed")
            raise HTTPException(status_code=400, detail="invalid signature")
    else:
        # No secret configured (or lib missing): parse best-effort, don't verify.
        try:
            import json as _json

            event = _json.loads(body or b"{}")
        except Exception:
            event = {}

    etype = (event.get("type") or "") if isinstance(event, dict) else ""
    paid_types = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "payment_intent.succeeded",
    }
    if etype not in paid_types:
        return {"ok": True, "ignored": etype or "unknown"}

    # Resolve the user id from the object's metadata / client_reference_id.
    obj = ((event.get("data") or {}).get("object") or {}) if isinstance(event, dict) else {}
    user_id = None
    if isinstance(obj, dict):
        meta = obj.get("metadata") or {}
        user_id = meta.get("user_id") or obj.get("client_reference_id")

    if not user_id:
        return {"ok": True, "no_user": True}

    try:
        async with AsyncSessionLocal() as db:
            await entitlements.grant_paid(db, user_id)
    except Exception:
        logger.exception("[billing] grant_paid from webhook failed")
        # Still 200 so Stripe doesn't hammer-retry on our internal error.
        return {"ok": True, "deferred": True}

    return {"ok": True, "granted": str(user_id)}
