# File: backend/src/api/v1/endpoints/public.py
"""
Public endpoints that don't require authentication.
Used for landing page demos, public agent share pages (/a/{slug}), and shared run links.
"""
import asyncio
import json
import os
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi_limiter.depends import RateLimiter
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.auth.encryption import decrypt
from src.core.execution.log_redaction import (
    sanitize_event_for_client,
    sanitize_raw_for_client,
)
from src.core.llm_clients import get_llm_client
from src.core.security import create_access_token
from src.core.settings import get_settings
from src.db.models import Tool
from src.db.models.agent import Agent
from src.db.models.client import AgentClient
from src.db.models.client_member import ClientMember, ClientRole
from src.db.models.run import Run, RunStatus
from src.db.postgresql import get_session
from src.db.redis import get_raw_redis_pool, get_redis_pool
from src.tasks import run_agent_task

router = APIRouter()
settings = get_settings()


@router.post("/unsubscribe/{token}")
async def unsubscribe_one_click(
    token: str, session: AsyncSession = Depends(get_session)
) -> Response:
    """RFC 8058 one-click unsubscribe (mail clients POST here). Records the
    opt-out on the org's suppression list so the fleet never emails them again."""
    from src.core.email.suppression import verify_unsubscribe_token
    from src.services.suppression_service import add_suppression

    parsed = verify_unsubscribe_token(token)
    if parsed:
        org_id, email = parsed
        await add_suppression(session, org_id, email, reason="unsubscribe")
    # Always 200, even on a bad token — never leak whether an address exists.
    return Response(status_code=200)


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe_page(
    token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Human-facing unsubscribe (clicking the link in the email)."""
    from src.core.email.suppression import verify_unsubscribe_token
    from src.services.suppression_service import add_suppression

    parsed = verify_unsubscribe_token(token)
    if parsed:
        org_id, email = parsed
        await add_suppression(session, org_id, email, reason="unsubscribe")
        msg = "You've been unsubscribed. You won't receive further emails."
    else:
        msg = "This unsubscribe link is invalid or has expired."
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Unsubscribe</title></head>"
        f"<body style='font-family:system-ui,sans-serif;max-width:32rem;margin:18vh auto;"
        f"padding:0 1.5rem;color:#14181f'>"
        f"<p style='font-size:1.05rem;line-height:1.6'>{msg}</p></body></html>"
    )


_GUEST_RUN_TTL = 86400 * 30  # 30 days
_DEFAULT_FREE_RUN_LIMIT = 3
_DEFAULT_PRICE_MONTHLY = 29.0


# ============================================================
# ORG LOGO (stable public URL for emails + portal)
# ============================================================

# Deterministic per-org key so the URL is stable across re-uploads — a presigned
# URL would expire and break the logo in already-sent emails.
ORG_LOGO_KEY = "org-logos/{org_id}/logo"


@router.get("/org-logo/{org_id}")
async def get_org_logo(org_id: uuid.UUID):
    """Stream an org's uploaded logo from S3 at a stable, cacheable URL — safe to
    embed in client emails and the portal (unlike an expiring presigned URL).
    404 when no logo has been uploaded."""
    import boto3
    from botocore.exceptions import ClientError

    bucket = getattr(settings, "S3_UPLOADS_BUCKET_NAME", None)
    if not (bucket and settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY):
        raise HTTPException(status_code=404, detail="not found")
    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    key = ORG_LOGO_KEY.format(org_id=org_id)
    try:
        obj = await asyncio.to_thread(s3.get_object, Bucket=bucket, Key=key)
    except ClientError:
        raise HTTPException(status_code=404, detail="not found")
    content_type = obj.get("ContentType") or "image/png"
    return StreamingResponse(
        obj["Body"],
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ============================================================
# AGENCY SITE — PUBLIC LEAD INTAKE
# ============================================================
# The agent-built agency site posts its contact form here. The site lives on a
# different origin (the deployed app's domain), so we answer CORS preflight and
# tag the response with permissive headers — creating a NEW prospect is the only
# side effect and it's rate-limited, so `*` is acceptable here.

_INTAKE_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
}


class AgencyIntakeIn(BaseModel):
    name: str = Field(..., max_length=300)
    email: str = Field(..., max_length=320)
    company: Optional[str] = Field(default=None, max_length=300)
    message: Optional[str] = Field(default=None, max_length=4000)
    # Optional qualification score (0-100) a quiz/qualifier lead-magnet computes and
    # POSTs alongside name+email, so the lead arrives pre-scored. Ignored by the
    # agency-intake path (which never sends it). `qualification` is an accepted alias.
    score: Optional[int] = Field(default=None, ge=0, le=100)
    qualification: Optional[int] = Field(default=None, ge=0, le=100)


# Website-visitor de-anonymization (RB2B / Koala / Vector) → prospect. These
# vendors POST an identified visitor (person + company) when someone hits the
# operator's site; we land them as a prospect with a high-intent "visited your
# website" signal. Tolerant of each vendor's payload shape. Dormant until the
# operator wires a vendor at /visitor-intake/{agency_site_token}.
class VisitorIntakeIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    company_name: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    linkedin: Optional[str] = None
    page: Optional[str] = None
    url: Optional[str] = None

    model_config = {"extra": "ignore"}


@router.post(
    "/visitor-intake/{token}",
    dependencies=[Depends(RateLimiter(times=240, seconds=3600))],
)
async def visitor_intake(
    token: str,
    body: VisitorIntakeIn,
    session: AsyncSession = Depends(get_session),
):
    """An RB2B/Koala-style website-visitor identification → upsert a prospect in
    the operator's pipeline with a high-intent ``visited_website`` signal."""
    from datetime import datetime

    from sqlalchemy import func

    from src.db.models.organization import Organization
    from src.db.models.prospect import Prospect, ProspectSource, ProspectStage

    org = (
        await session.execute(
            select(Organization).where(Organization.agency_site_token == token)
        )
    ).scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Unknown site")

    name = (body.name or "").strip()[:300]
    email = (body.email or "").strip().lower()[:320] or None
    company = ((body.company or body.company_name or "") or "").strip()[:300] or None
    li = (body.linkedin_url or body.linkedin or "").strip()[:1024] or None
    page = (body.page or body.url or "").strip()[:1024] or None
    if not name and not email and not li:
        raise HTTPException(status_code=422, detail="Need a name, email, or LinkedIn")

    now = datetime.utcnow()
    sig = {
        "triggers": ["visited_website"],
        "strength": 65,
        "detected_at": now.isoformat(),
    }

    def _stamp(p: Prospect) -> None:
        enr = dict(p.enrichment or {})
        v = dict(enr.get("visit") or {})
        v["count"] = int(v.get("count") or 0) + 1
        v["last_at"] = now.isoformat()
        if page:
            v["page"] = page
        enr["visit"] = v
        existing = enr.get("signal") or {}
        if int(existing.get("strength") or 0) <= 65:
            enr["signal"] = sig
        p.enrichment = enr
        p.score = max(int(p.score or 0), 58)
        p.updated_at = now

    # Match an existing prospect by email or LinkedIn handle, else create.
    match = None
    if email:
        match = (
            (
                await session.execute(
                    select(Prospect).where(
                        Prospect.organization_id == org.id,
                        func.lower(Prospect.email) == email,
                    )
                )
            )
            .scalars()
            .first()
        )
    if not match and li:
        import re as _re

        m = _re.search(r"linkedin\.com/in/([^/?#]+)", li.lower())
        handle = m.group(1) if m else None
        if handle:
            match = (
                (
                    await session.execute(
                        select(Prospect).where(
                            Prospect.organization_id == org.id,
                            func.lower(Prospect.linkedin_url).contains(handle),
                        )
                    )
                )
                .scalars()
                .first()
            )

    if match:
        _stamp(match)
        session.add(match)
    else:
        p = Prospect(
            organization_id=org.id,
            name=name or (email or li or "Website visitor")[:300],
            email=email,
            company=company,
            title=((body.title or "").strip()[:300] or None),
            linkedin_url=li,
            stage=ProspectStage.NEW,
            source=ProspectSource.INBOUND,
            score=58,
            notes=f"Visited your website{f' ({page})' if page else ''}.",
            enrichment={
                "visit": {"count": 1, "last_at": now.isoformat(), "page": page},
                "signal": sig,
            },
        )
        session.add(p)
    await session.commit()
    return {"ok": True}


@router.options("/agency-intake/{token}")
async def agency_intake_preflight(token: str):
    from fastapi import Response

    return Response(status_code=204, headers=_INTAKE_CORS)


@router.post(
    "/agency-intake/{token}",
    dependencies=[Depends(RateLimiter(times=20, seconds=3600))],
)
async def agency_intake(
    token: str,
    body: AgencyIntakeIn,
    session: AsyncSession = Depends(get_session),
):
    """A lead submitted the contact form on an operator's public agency site →
    land it as a NEW inbound prospect in that operator's pipeline."""
    from fastapi import Response

    from src.db.models.organization import Organization
    from src.db.models.prospect import Prospect, ProspectSource, ProspectStage

    res = await session.execute(
        select(Organization).where(Organization.agency_site_token == token)
    )
    org = res.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Unknown agency site")

    name = (body.name or "").strip()[:300]
    email = (body.email or "").strip()[:320]
    if not name or not email:
        raise HTTPException(status_code=422, detail="Name and email are required")

    prospect = Prospect(
        organization_id=org.id,
        name=name,
        email=email,
        company=((body.company or "").strip()[:300] or None),
        stage=ProspectStage.NEW,
        source=ProspectSource.INBOUND,
        notes=((body.message or "").strip()[:4000] or None),
    )
    session.add(prospect)
    await session.commit()

    # Same inbound lead → fire async enrichment best-effort (must never fail the
    # visitor's capture POST; the prospect is already committed).
    try:
        from src.tasks._enrich import enrich_prospect_task

        enrich_prospect_task.send(str(prospect.id))
    except Exception:  # nosec B110 — enrichment is fire-and-forget
        pass

    return Response(
        content=json.dumps({"ok": True}),
        media_type="application/json",
        headers=_INTAKE_CORS,
    )


# Lead-magnet intake. Distinct from /agency-intake (keyed to the org's agency
# site): this token encodes (org_id, optional client_id) so a CLIENT-scoped
# magnet's leads land in THAT client's pipeline (Prospect.agent_client_id), while
# an agency-scoped magnet lands org-level. The deployed magnet app's capture form
# POSTs here. Same CORS + rate limit + name/email validation as agency-intake.


@router.options("/lead-intake/{token}")
async def lead_intake_preflight(token: str):
    from fastapi import Response

    return Response(status_code=204, headers=_INTAKE_CORS)


@router.post(
    "/lead-intake/{token}",
    dependencies=[Depends(RateLimiter(times=20, seconds=3600))],
)
async def lead_intake(
    token: str,
    body: AgencyIntakeIn,
    session: AsyncSession = Depends(get_session),
):
    """A visitor captured by a deployed lead-magnet app → land them as a NEW
    inbound prospect. Token decodes to (org_id, optional client_id): when a client
    is present the prospect is scoped to that client's pipeline."""
    from fastapi import Response

    from src.db.models.organization import Organization
    from src.db.models.prospect import Prospect, ProspectSource, ProspectStage
    from src.services.lead_magnet_service import decode_intake_token

    decoded = decode_intake_token(token)
    if not decoded:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")

    try:
        org_id = uuid.UUID(str(decoded["org_id"]))
        client_id = (
            uuid.UUID(str(decoded["client_id"])) if decoded.get("client_id") else None
        )
        lead_magnet_id = (
            uuid.UUID(str(decoded["lead_magnet_id"]))
            if decoded.get("lead_magnet_id")
            else None
        )
    except Exception:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")

    org = await session.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")

    # If the token names a client, only accept it when that client still belongs
    # to this org (defense in depth; the token is already signed).
    if client_id is not None:
        client = await session.get(AgentClient, client_id)
        if not client or client.organization_id != org.id:
            client_id = None

    # Resolve the magnet this capture belongs to (defense in depth: only count it
    # when the row still belongs to this org). Lets us increment leads_count and
    # inherit the magnet's client scope when the token didn't carry one.
    from src.db.models.lead_magnet import LeadMagnet as _LeadMagnet

    magnet_row = None
    if lead_magnet_id is not None:
        magnet_row = await session.get(_LeadMagnet, lead_magnet_id)
        if not magnet_row or magnet_row.organization_id != org.id:
            magnet_row = None
            lead_magnet_id = None
        elif client_id is None and magnet_row.agent_client_id is not None:
            client_id = magnet_row.agent_client_id

    name = (body.name or "").strip()[:300]
    email = (body.email or "").strip()[:320]
    if not name or not email:
        raise HTTPException(status_code=422, detail="Name and email are required")

    # Optional qualification score (0-100) a quiz/qualifier magnet POSTs → the lead
    # arrives pre-scored on the same field the pipeline already uses.
    score = body.score if body.score is not None else body.qualification

    prospect = Prospect(
        organization_id=org.id,
        agent_client_id=client_id,
        lead_magnet_id=lead_magnet_id,
        name=name,
        email=email,
        company=((body.company or "").strip()[:300] or None),
        stage=ProspectStage.NEW,
        source=ProspectSource.INBOUND,
        score=(int(score) if score is not None else None),
        notes=((body.message or "").strip()[:4000] or None),
    )
    session.add(prospect)

    # Attribute the capture to its magnet (the per-magnet lead metric).
    if magnet_row is not None:
        magnet_row.leads_count = (magnet_row.leads_count or 0) + 1
        from datetime import datetime as _dt

        magnet_row.updated_at = _dt.utcnow()
        session.add(magnet_row)

    await session.commit()

    # Async enrichment: the lead arrived bare (name/email/maybe company) — fan out
    # the metered firmographics/role/fit-score waterfall in the background so it's
    # immediately workable in outbound. Best-effort: a failed enqueue must NEVER
    # fail the visitor's capture POST (the prospect is already committed).
    try:
        from src.tasks._enrich import enrich_prospect_task

        enrich_prospect_task.send(str(prospect.id))
    except Exception:  # nosec B110 — enrichment is fire-and-forget
        pass

    return Response(
        content=json.dumps({"ok": True}),
        media_type="application/json",
        headers=_INTAKE_CORS,
    )


# Lead-magnet VIEW beacon. The deployed magnet app fires a one-time fetch() here
# on page load (fire-and-forget, no PII) so each magnet has a real top-of-funnel
# number to pair with leads_count → a true conversion rate. Same token shape +
# CORS as lead-intake; a generous rate limit since one visitor = one beacon but a
# magnet can be popular. Best-effort: NEVER raises (always returns ok) so a bad
# token or a DB hiccup never breaks the visitor's page load.


@router.options("/lead-magnet-view/{token}")
async def lead_magnet_view_preflight(token: str):
    from fastapi import Response

    return Response(status_code=204, headers=_INTAKE_CORS)


@router.post(
    "/lead-magnet-view/{token}",
    dependencies=[Depends(RateLimiter(times=240, seconds=3600))],
)
async def lead_magnet_view(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """A visitor loaded a deployed lead-magnet app → bump that magnet's
    views_count (best-effort). Token decodes to (org_id, client_id?, magnet_id);
    only the magnet id matters here. Never raises — a forged/old token, a missing
    magnet, or a DB error all return ok so the beacon never disturbs the page."""
    from fastapi import Response

    from src.services.lead_magnet_service import decode_intake_token

    try:
        decoded = decode_intake_token(token)
        lm_id = (decoded or {}).get("lead_magnet_id")
        if lm_id:
            from src.db.models.lead_magnet import LeadMagnet as _LeadMagnet

            magnet = await session.get(_LeadMagnet, uuid.UUID(str(lm_id)))
            if magnet is not None:
                magnet.views_count = (magnet.views_count or 0) + 1
                magnet.updated_at = datetime.utcnow()
                session.add(magnet)
                # ALSO bump today's per-day bucket (the time-series row the UI
                # charts). Upsert on (lead_magnet_id, day) so the beacon is
                # idempotent per day. Best-effort — never raise.
                from sqlalchemy.dialects.postgresql import insert as _pg_insert

                from src.db.models.lead_magnet import (
                    LeadMagnetViewDaily as _ViewDaily,
                )

                _today = datetime.utcnow().date()
                _stmt = (
                    _pg_insert(_ViewDaily)
                    .values(
                        id=uuid.uuid4(),
                        lead_magnet_id=magnet.id,
                        day=_today,
                        count=1,
                    )
                    .on_conflict_do_update(
                        index_elements=["lead_magnet_id", "day"],
                        set_={"count": _ViewDaily.count + 1},
                    )
                )
                await session.execute(_stmt)
                await session.commit()
    except Exception as exc:  # nosec B110 — a view beacon must never disturb the page
        logger.warning(f"[magnet] view beacon skipped: {exc}")

    return Response(status_code=204, headers=_INTAKE_CORS)


# ============================================================
# MAGNET GATEWAY — metered toolbox for INTERACTIVE lead magnets
# ============================================================
# A deployed magnet can be genuinely interactive (paste-a-URL → live AI audit) by
# calling these from its OWN Next.js API routes. The builder agent composes the
# primitives; we never ship a template. Auth = the magnet's signed intake token
# (same `{token}` shape as lead-intake). Abuse is bounded economically: the per-IP
# RateLimiter on each route + a per-magnet daily cap (in magnet_gateway) + usage
# metering. Capture reuses /lead-intake/{token}. Flip MAGNET_GATEWAY_ENABLED off to
# kill the surface. Same permissive CORS as the rest of the magnet endpoints (the
# deployed app is a different origin; the calls are scoped + capped).


class MagnetLLMIn(BaseModel):
    system: str = Field(default="", max_length=8000)
    prompt: str = Field(..., max_length=24000)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=8000)


class MagnetSearchIn(BaseModel):
    query: str = Field(..., max_length=400)
    num_results: Optional[int] = Field(default=6, ge=1, le=10)


class MagnetScrapeIn(BaseModel):
    url: str = Field(..., max_length=2048)


def _gateway_enabled() -> None:
    if not getattr(settings, "MAGNET_GATEWAY_ENABLED", True):
        raise HTTPException(status_code=404, detail="Not found")


@router.options("/magnet/{tool}/{token}")
async def magnet_gateway_preflight(tool: str, token: str):
    return Response(status_code=204, headers=_INTAKE_CORS)


@router.post(
    "/magnet/llm/{token}",
    dependencies=[Depends(RateLimiter(times=60, seconds=3600))],
)
async def magnet_llm(
    token: str,
    body: MagnetLLMIn,
    session: AsyncSession = Depends(get_session),
):
    """Stream an LLM completion (SSE) for an interactive magnet. The app supplies
    the system + user prompt; we cap output, meter the call, and enforce a per-magnet
    daily ceiling. SSE frames: `data: {"delta": "..."}` per chunk, then `data: [DONE]`."""
    _gateway_enabled()
    from src.services import magnet_gateway as gw

    ctx = await gw.resolve_context(session, token)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")
    if not await gw.within_daily_cap(
        ctx.magnet_id, "llm", settings.MAGNET_GATEWAY_LLM_DAILY_CAP
    ):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    async def _events():
        try:
            async for piece in gw.stream_llm(
                session,
                ctx,
                system=body.system,
                prompt=body.prompt,
                max_tokens=body.max_tokens,
            ):
                yield f"data: {json.dumps({'delta': piece})}\n\n"
        except Exception as exc:  # nosec B110 — surface a clean stream end on error
            logger.warning(f"[magnet-gw] llm event error: {exc}")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={
            **_INTAKE_CORS,
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/magnet/search/{token}",
    dependencies=[Depends(RateLimiter(times=60, seconds=3600))],
)
async def magnet_search(
    token: str,
    body: MagnetSearchIn,
    session: AsyncSession = Depends(get_session),
):
    """Web search (Exa) for a magnet → {results: [{title, url, snippet}]}."""
    _gateway_enabled()
    from src.services import magnet_gateway as gw

    ctx = await gw.resolve_context(session, token)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")
    if not await gw.within_daily_cap(
        ctx.magnet_id, "tool", settings.MAGNET_GATEWAY_TOOL_DAILY_CAP
    ):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    results = await gw.run_search(
        session, ctx, body.query, num_results=body.num_results or 6
    )
    return Response(
        content=json.dumps({"results": results}),
        media_type="application/json",
        headers=_INTAKE_CORS,
    )


@router.post(
    "/magnet/scrape/{token}",
    dependencies=[Depends(RateLimiter(times=60, seconds=3600))],
)
async def magnet_scrape(
    token: str,
    body: MagnetScrapeIn,
    session: AsyncSession = Depends(get_session),
):
    """Scrape a URL (Firecrawl) for a magnet → {markdown: str|null}. Unsafe/internal
    URLs are rejected (SSRF guard)."""
    _gateway_enabled()
    from src.services import magnet_gateway as gw

    ctx = await gw.resolve_context(session, token)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Unknown lead magnet")
    if not await gw.within_daily_cap(
        ctx.magnet_id, "tool", settings.MAGNET_GATEWAY_TOOL_DAILY_CAP
    ):
        raise HTTPException(status_code=429, detail="Daily limit reached")

    md = await gw.run_scrape(session, ctx, body.url)
    return Response(
        content=json.dumps({"markdown": md}),
        media_type="application/json",
        headers=_INTAKE_CORS,
    )


# ============================================================
# CROSSNODE'S OWN SALES INBOX (contact-sales page)
# ============================================================
# Distinct from /agency-intake/{token} above: that is keyed to an OPERATOR's
# agency site. This is Crossnode's OWN sales lead capture — the marketing
# /contact-sales form posts here. It lands the lead on Crossnode's own org
# pipeline (CROSSNODE_OWN_ORG_ID) when configured, and always sends a heads-up
# email to the sales inbox so a lead is never silently dropped.


class ContactSalesIn(BaseModel):
    name: str = Field(..., max_length=300)
    email: str = Field(..., max_length=320)
    company: Optional[str] = Field(default=None, max_length=300)
    team_size: Optional[str] = Field(default=None, max_length=32)
    message: Optional[str] = Field(default=None, max_length=4000)


@router.options("/contact-sales")
async def contact_sales_preflight():
    from fastapi import Response

    return Response(status_code=204, headers=_INTAKE_CORS)


@router.post(
    "/contact-sales",
    dependencies=[Depends(RateLimiter(times=20, seconds=3600))],
)
async def contact_sales(
    body: ContactSalesIn,
    session: AsyncSession = Depends(get_session),
):
    """Crossnode's own sales-lead intake (the marketing /contact-sales form).

    Lands the lead on Crossnode's own org pipeline when CROSSNODE_OWN_ORG_ID is
    set, and always emails a heads-up to SALES_NOTIFICATION_EMAIL so a lead is
    never silently dropped. Rate-limited 20/hour/IP like /agency-intake."""
    from fastapi import Response

    name = (body.name or "").strip()[:300]
    email = (body.email or "").strip()[:320]
    if not name or "@" not in email:
        raise HTTPException(
            status_code=422, detail="A name and valid email are required"
        )

    company = (body.company or "").strip()[:300] or None
    team_size = (body.team_size or "").strip()[:32] or None
    message = (body.message or "").strip()[:4000] or None

    # Team size is not a Prospect column, so fold it into the notes.
    note_parts = []
    if team_size:
        note_parts.append(f"Team size: {team_size}")
    if message:
        note_parts.append(message)
    notes = "\n\n".join(note_parts) or None

    # 1) Land on Crossnode's own pipeline when an own-org is configured.
    own_org_id = getattr(settings, "CROSSNODE_OWN_ORG_ID", None)
    if own_org_id:
        try:
            from src.db.models.prospect import (
                Prospect,
                ProspectSource,
                ProspectStage,
            )

            prospect = Prospect(
                organization_id=uuid.UUID(str(own_org_id)),
                name=name,
                email=email,
                company=company,
                stage=ProspectStage.NEW,
                source=ProspectSource.INBOUND,
                notes=notes,
            )
            session.add(prospect)
            await session.commit()
        except Exception as e:  # never fail the form on a pipeline hiccup
            logger.error(f"contact-sales: failed to record prospect: {e}")

    # 2) Always send a heads-up to the sales inbox (best-effort).
    try:
        from src.core.email import _get_base_email_html, _send_email

        sales_to = (
            getattr(settings, "SALES_NOTIFICATION_EMAIL", None)
            or settings.EMAILS_FROM_EMAIL
        )

        def _safe(s: Optional[str]) -> str:
            return (s or "").replace("<", "&lt;").replace(">", "&gt;")

        content = (
            f"<p><strong>New sales lead from the contact-sales form.</strong></p>"
            f"<p><strong>Name:</strong> {_safe(name)}<br>"
            f"<strong>Email:</strong> {_safe(email)}<br>"
            f"<strong>Company:</strong> {_safe(company) or '—'}<br>"
            f"<strong>Team size:</strong> {_safe(team_size) or '—'}</p>"
            f"<p><strong>Message:</strong><br>{_safe(message) or '—'}</p>"
        )
        html = _get_base_email_html(title="New sales lead", content=content)
        _send_email(
            sales_to,
            f"New sales lead — {name}" + (f" ({company})" if company else ""),
            html,
            reply_to=email,
        )
    except Exception as e:
        logger.error(f"contact-sales: failed to send notification email: {e}")

    return Response(
        content=json.dumps({"ok": True}),
        media_type="application/json",
        headers=_INTAKE_CORS,
    )


# ============================================================
# LANDING PAGE DEMOS
# ============================================================


class DemoAgentRequest(BaseModel):
    prompt: str = Field(..., max_length=500, description="Agent description prompt")
    cf_turnstile_token: Optional[str] = None


class DemoAgentSuggestion(BaseModel):
    name: str
    description: str
    capabilities: list[str]
    suggested_tools: list[str]
    complexity: str


class DemoAgentResponse(BaseModel):
    suggestion: DemoAgentSuggestion
    prompt: str


@router.post(
    "/demo/suggest-agent",
    response_model=DemoAgentResponse,
    dependencies=[Depends(RateLimiter(times=10, seconds=3600))],
)
async def public_suggest_agent(
    request: Request,
    body: DemoAgentRequest,
    session: AsyncSession = Depends(get_session),
):
    """Public endpoint for landing page demo."""
    from src.core.turnstile import verify_turnstile

    await verify_turnstile(body.cf_turnstile_token, request)

    prompt = body.prompt.strip()

    if len(prompt) < 10:
        raise HTTPException(
            status_code=400,
            detail="Please provide a more detailed description (at least 10 characters).",
        )

    if not settings.DEFAULT_PLANNER_LLM_TOOL_ID:
        logger.warning("No planner LLM configured for public demo endpoint")
        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(
                name="Custom AI Agent",
                description=f"An intelligent agent designed to: {prompt[:100]}",
                capabilities=["Task Automation", "Data Processing", "Smart Decisions"],
                suggested_tools=["Web Search", "Email", "Calendar"],
                complexity="Intermediate",
            ),
            prompt=prompt,
        )

    try:
        planner_llm = await session.get(
            Tool, uuid.UUID(settings.DEFAULT_PLANNER_LLM_TOOL_ID)
        )

        if not planner_llm:
            logger.warning("Planner LLM tool not found in database")
            return _get_fallback_response(prompt)

        api_key = decrypt(planner_llm.config["encrypted_api_key"].encode("latin-1"))
        llm_client = get_llm_client(planner_llm, api_key)

        system_prompt = """You are an AI Solutions Architect helping a potential user explore what they can build.

Given the user's description, create ONE extremely impressive agent suggestion that showcases the power of the platform.

Rules:
1. FAVOR COMPLEXITY: Suggest multi-step workflows with logic and branching. Avoid "Start -> LLM -> End" patterns.
2. DETERMINISTIC LOGIC: Instead of just an "agent", describe a "workflow" with specific tools for specific tasks.
3. TOOLS: Include 3-4 real, high-value tool integrations (e.g., Slack, Gmail, HubSpot, Stripe, GitHub).
4. COMPLEXITY: Always aim for "Advanced" or "Intermediate" complexity.
5. DESCRIPTION: Focus on the business value and the sophisticated logic/multi-tool chain.

Return a JSON object with:
- "name": Powerful agent name (e.g., "Enterprise Lead Qualification & CRM Sync Engine")
- "description": 2-3 sentence description highlighting the complex logic and tool chain
- "capabilities": Array of 4-5 specific, advanced capabilities
- "suggested_tools": Array of 3-4 professional tool/app names
- "complexity": "Intermediate" or "Advanced"

Output ONLY valid JSON, no markdown."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"I want to build: {prompt}"},
        ]

        full_response = ""
        async for chunk in llm_client.stream_chat_response(
            planner_llm.config["model_id"], messages, enforce_json=True
        ):
            if chunk.get("type") == "text_chunk":
                full_response += chunk.get("content", "")

        clean_json = (
            full_response.strip().replace("```json", "").replace("```", "").strip()
        )
        suggestion_data = json.loads(clean_json)

        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(**suggestion_data),
            prompt=prompt,
        )

    except Exception as e:
        logger.error(f"Public demo agent suggestion failed: {e}")
        return _get_fallback_response(prompt)


def _get_fallback_response(prompt: str) -> DemoAgentResponse:
    keywords = prompt.lower()

    if any(word in keywords for word in ["email", "gmail", "mail"]):
        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(
                name="Email Automation Agent",
                description="Automatically processes incoming emails, extracts key information, and takes actions based on content and sender.",
                capabilities=[
                    "Email Monitoring",
                    "Smart Filtering",
                    "Auto-Response",
                    "Task Creation",
                ],
                suggested_tools=["Gmail", "Slack", "Notion"],
                complexity="Intermediate",
            ),
            prompt=prompt,
        )
    elif any(word in keywords for word in ["sales", "lead", "crm"]):
        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(
                name="Lead Qualification Agent",
                description="Automatically qualifies inbound leads, enriches contact data, and routes hot prospects to your sales team.",
                capabilities=[
                    "Lead Scoring",
                    "Data Enrichment",
                    "CRM Sync",
                    "Slack Notifications",
                ],
                suggested_tools=["HubSpot", "LinkedIn", "Slack"],
                complexity="Advanced",
            ),
            prompt=prompt,
        )
    elif any(word in keywords for word in ["slack", "notification", "alert"]):
        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(
                name="Smart Notification Agent",
                description="Monitors multiple data sources and sends intelligent, contextual alerts to the right channels.",
                capabilities=[
                    "Multi-Source Monitoring",
                    "Smart Routing",
                    "Priority Detection",
                ],
                suggested_tools=["Slack", "Discord", "Email"],
                complexity="Intermediate",
            ),
            prompt=prompt,
        )
    else:
        return DemoAgentResponse(
            suggestion=DemoAgentSuggestion(
                name="Custom Automation Agent",
                description=f"A powerful agent tailored to your needs: {prompt[:80]}...",
                capabilities=[
                    "Workflow Automation",
                    "Data Processing",
                    "API Integrations",
                    "Smart Decisions",
                ],
                suggested_tools=["Slack", "Gmail", "Notion", "Web Search"],
                complexity="Intermediate",
            ),
            prompt=prompt,
        )


# ============================================================
# PUBLIC FAST BUILD DEMO
# ============================================================


class DemoFastBuildRequest(BaseModel):
    prompt: str = Field(..., max_length=500)
    cf_turnstile_token: Optional[str] = None


@router.post(
    "/demo/fast-build/stream",
    dependencies=[Depends(RateLimiter(times=300, seconds=60))],
)
async def public_fast_build_demo_stream(
    request: Request,
    body: DemoFastBuildRequest,
    session: AsyncSession = Depends(get_session),
):
    """Removed: the public node-graph builder demo. Crossnode is now an agent
    delivery platform — there is no canvas to demo. See the Composer instead."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="The builder demo has been retired. Describe an outcome in the Composer instead.",
    )

    # (unreachable — legacy body retained for reference; the builder is gone)
    from src.core.turnstile import verify_turnstile

    await verify_turnstile(body.cf_turnstile_token, request)

    from fastapi.responses import StreamingResponse

    from src.db.models.tool import ToolType

    prompt = body.prompt.strip()
    if len(prompt) < 5:
        raise HTTPException(status_code=400, detail="Prompt too short")

    if not settings.APP_BUILDER_LLM_TOOL_ID:
        raise HTTPException(status_code=503, detail="Demo builder not configured.")

    llm_tool = await session.get(Tool, uuid.UUID(settings.APP_BUILDER_LLM_TOOL_ID))
    if not llm_tool:
        raise HTTPException(status_code=503, detail="Demo builder LLM not available.")

    demo_tools = [
        Tool(
            id=uuid.uuid4(),
            name="Gmail",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Gmail"},
            description="Send and read emails",
        ),
        Tool(
            id=uuid.uuid4(),
            name="Slack",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Slack"},
            description="Send messages to Slack channels",
        ),
        Tool(
            id=uuid.uuid4(),
            name="Google Calendar",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Google Calendar"},
            description="Manage calendar events",
        ),
        Tool(
            id=uuid.uuid4(),
            name="Notion",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Notion"},
            description="Read/Write Notion pages",
        ),
        Tool(
            id=uuid.uuid4(),
            name="Stripe",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Stripe"},
            description="Process payments and subscriptions",
        ),
        Tool(
            id=uuid.uuid4(),
            name="HubSpot",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "HubSpot"},
            description="CRM management",
        ),
        Tool(
            id=uuid.uuid4(),
            name="Web Search",
            tool_type=ToolType.COMPOSIO,
            config={"app_name": "Tavily"},
            description="Search the internet",
        ),
    ]

    async def event_generator():
        nodes = []
        try:
            yield f"data: {json.dumps({'type': 'status', 'message': 'Analyzing request...'})}\n\n"

            async for event in stream_build_agent(
                prompt=prompt,
                available_tools=demo_tools,
                available_agents=[],
                llm_tool=llm_tool,
                organization=None,
            ):
                action = event.get("action")
                payload = event.get("payload", {})
                if action == "add_node":
                    nodes.append(payload)
                yield f"data: {json.dumps({'type': 'graph_action', 'action': action, 'payload': payload})}\n\n"

            base_value = 299
            node_value = len(nodes) * 50
            tool_names = set()
            for t in demo_tools:
                for n in nodes:
                    if t.name.lower() in str(n).lower():
                        tool_names.add(t.name)
            integration_value = len(tool_names) * 150
            total_value = base_value + node_value + integration_value
            total_value = (total_value // 10) * 10 + 9

            yield f"data: {json.dumps({'type': 'value_estimation', 'value': total_value, 'currency': 'USD'})}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'nodes_count': len(nodes)})}\n\n"

        except Exception as e:
            logger.error(f"Demo build failed: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Demo generation failed. Please try again.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================
# PUBLIC AGENT PAGE ENDPOINTS (/a/{slug} on main domain)
# ============================================================


# ============================================================
# OAUTH TRIAL EXCHANGE
# ============================================================


class PortalTrialExchangeRequest(BaseModel):
    code: str


@router.post(
    "/oauth-trial-exchange",
    dependencies=[Depends(RateLimiter(times=60, seconds=60))],
)
async def exchange_portal_trial_code(
    body: PortalTrialExchangeRequest,
    redis=Depends(get_redis_pool),
):
    """
    Exchange a one-time code (from disposable OAuth) for portal trial credentials.
    Called by the /api/auth-proxy/portal/oauth-callback Next.js route.
    """
    raw = await redis.get(f"portal_trial_exchange:{body.code}")
    if not raw:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    await redis.delete(f"portal_trial_exchange:{body.code}")
    import json as _json

    data = _json.loads(raw)
    return data


# Also expose under the auth namespace for the Next.js route
# (registered at /api/v1/public/oauth-trial-exchange but the route.ts calls
# /api/v1/auth/portal-trial/exchange — alias registered in the main router)


# ============================================================
# MODELS
# ============================================================


class PublicAgentRunRequest(BaseModel):
    input_data: dict[str, Any]
    guest_id: Optional[str] = None


class ConvertGuestRequest(BaseModel):
    agent_id: str
    email: str
    cf_turnstile_token: Optional[str] = None


class ConvertGuestResponse(BaseModel):
    access_token: str
    client: dict[str, Any]


# ============================================================
# GUEST → TRIAL CONVERSION
# ============================================================


@router.post(
    "/convert",
    response_model=ConvertGuestResponse,
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
)
async def convert_guest_to_trial(
    request: Request,
    body: ConvertGuestRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Convert a guest visitor into a trial portal client.
    Called from the conversion wall on public agent pages and shared run pages.
    Creates an AgentClient with 7-day trial access and returns a JWT + portal path.
    """
    from src.core.turnstile import verify_turnstile

    await verify_turnstile(body.cf_turnstile_token, request)

    # Resolve agent to get organization_id
    agent: Optional[Agent] = None
    if body.agent_id:
        try:
            agent = await session.get(Agent, uuid.UUID(body.agent_id))
        except (ValueError, Exception):
            pass

    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    email = body.email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(status_code=400, detail="Invalid email address")

    # Check if client already exists for this email + org
    from sqlmodel import func as sqlfunc

    existing_stmt = select(AgentClient).where(
        AgentClient.organization_id == agent.organization_id,
        sqlfunc.lower(AgentClient.contact_email) == email,
        AgentClient.is_active == True,
    )
    existing_client = (await session.execute(existing_stmt)).scalar_one_or_none()

    if existing_client:
        # Refresh trial if expired
        if existing_client.payment_mode == "free":
            existing_client.payment_mode = "trial"
            existing_client.trial_ends_at = datetime.utcnow() + timedelta(days=7)
            existing_client.trial_credits = 500
            session.add(existing_client)
            await session.flush()
        client = existing_client
    else:
        # Generate a unique slug from email
        slug_base = re.sub(r"[^a-z0-9]", "-", email.split("@")[0].lower()).strip("-")
        slug_base = slug_base[:20] or "user"
        client_slug = f"{slug_base}-{secrets.token_hex(3)}"

        client = AgentClient(
            name=email.split("@")[0].replace(".", " ").title(),
            slug=client_slug,
            contact_email=email,
            organization_id=agent.organization_id,
            payment_mode="trial",
            trial_ends_at=datetime.utcnow() + timedelta(days=7),
            trial_credits=500,
            monthly_allowance_credits=500,
            portal_enabled=True,
            is_active=True,
        )
        session.add(client)
        await session.flush()

    # Ensure ClientMember exists
    member_stmt = select(ClientMember).where(
        ClientMember.client_id == client.id,
        ClientMember.email == email,
    )
    member = (await session.execute(member_stmt)).scalar_one_or_none()
    if not member:
        member = ClientMember(
            client_id=client.id,
            email=email,
            role=ClientRole.ADMIN,
            is_active=True,
            last_login_at=datetime.utcnow(),
        )
        session.add(member)
        await session.flush()
    else:
        member.last_login_at = datetime.utcnow()
        session.add(member)

    await session.commit()
    await session.refresh(client)
    await session.refresh(member)

    token = create_access_token(
        data={
            "sub": email,
            "client_id": str(client.id),
            "member_id": str(member.id),
            "type": "client_portal",
        },
        expires_delta=timedelta(days=7),
    )

    trial_ends_at = client.trial_ends_at.isoformat() if client.trial_ends_at else ""

    return ConvertGuestResponse(
        access_token=token,
        client={
            "id": str(client.id),
            "name": client.name,
            "slug": client.slug,
            "contact_email": client.contact_email,
            "logo_url": client.logo_url,
            "brand_color": client.brand_color or "#6366f1",
            "trial_ends_at": trial_ends_at,
        },
        portal_path=f"/portal/{client.slug}",
        trial_ends_at=trial_ends_at,
    )


# ============================================================
# PLATFORM STATS (for landing page social proof)
# ============================================================


class PlatformStats(BaseModel):
    active_builders: int
    runs_this_month: int


@router.get(
    "/platform-stats",
    response_model=PlatformStats,
    dependencies=[Depends(RateLimiter(times=200, seconds=60))],
)
async def get_platform_stats(
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis_pool),
):
    """Public platform-wide stats for social proof. Cached 1 hour."""

    CACHE_KEY = "public:platform_stats"
    CACHE_TTL = 3600

    cached = await redis.get(CACHE_KEY)
    if cached:
        data = json.loads(cached)
        return PlatformStats(**data)

    from src.db.models.organization import Organization

    builders_result = await session.execute(
        select(func.count(Organization.id)).where(
            Organization.stripe_subscription_status == "active"
        )
    )
    active_builders: int = builders_result.scalar() or 0

    now = datetime.now(UTC).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    runs_result = await session.execute(
        select(func.count(Run.id)).where(
            Run.status == RunStatus.COMPLETED,
            Run.created_at >= month_start,
        )
    )
    runs_this_month: int = runs_result.scalar() or 0

    payload = {"active_builders": active_builders, "runs_this_month": runs_this_month}
    await redis.setex(CACHE_KEY, CACHE_TTL, json.dumps(payload))

    return PlatformStats(**payload)


# ── Shared run endpoints (is_public=True runs, no auth required) ─────────────


# Bonus runs granted when a guest provides an email — applies to shared-run
# re-runs and storefront anon demos. Keyed on a hash of email + agent_id so
# the same email gets the same bonus across cookie/IP resets.
_EMAIL_UNLOCK_BONUS = 5
_EMAIL_UNLOCK_TTL = 86400 * 90  # 90 days


_SENSITIVE_NODE_KEYS = frozenset(
    {
        "encrypted_api_key",
        "api_key",
        "apiKey",
        "password",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "webhook_secret",
        "database_url",
        "connection_string",
    }
)


def _sanitize_graph_snapshot(graph: Any) -> Any:
    """Strip credential fields from node configs before serving to unauthenticated callers."""
    if not isinstance(graph, dict):
        return graph
    nodes = graph.get("nodes", [])
    safe_nodes = []
    for node in nodes:
        if not isinstance(node, dict):
            safe_nodes.append(node)
            continue
        data = dict(node.get("data") or {})
        safe_data = {
            k: ("[redacted]" if k in _SENSITIVE_NODE_KEYS else v)
            for k, v in data.items()
        }
        # Also strip keys whose name contains credential-like substrings
        safe_data = {
            k: (
                "[redacted]"
                if any(
                    s in k.lower()
                    for s in (
                        "api_key",
                        "apikey",
                        "secret",
                        "password",
                        "token",
                        "credential",
                    )
                )
                else v
            )
            for k, v in safe_data.items()
        }
        safe_nodes.append({**node, "data": safe_data})
    return {**graph, "nodes": safe_nodes}


def _shared_run_guest_key(agent_id: str, guest_id: str) -> str:
    return f"guest_runs:agent:{agent_id}:{guest_id}"


def _email_unlock_key(agent_id: str, email_hash: str) -> str:
    return f"email_unlock:{agent_id}:{email_hash}"


def _hash_email(email: str) -> str:
    import hashlib

    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


class EmailUnlockBody(BaseModel):
    email: str
    agent_id: uuid.UUID
    source: Optional[str] = None  # "shared_run" | "storefront" | "audit"
    consent: bool = True  # explicit consent for marketing


class EmailUnlockResponse(BaseModel):
    granted: bool
    bonus_runs: int
    total_unlocked: int
    message: str


@router.post(
    "/email-unlock",
    response_model=EmailUnlockResponse,
    dependencies=[Depends(RateLimiter(times=20, seconds=60))],
)
async def grant_email_unlock(
    body: EmailUnlockBody,
    request: Request,
    redis=Depends(get_redis_pool),
    session: AsyncSession = Depends(get_session),
) -> EmailUnlockResponse:
    """Soft paywall: capture an email and grant N bonus runs on this agent.

    The unlock is keyed by hash(email) + agent_id so the same email can't
    multiply bonuses by hitting the modal repeatedly. The X-Guest-Id cookie
    is also reset to a deterministic value derived from the email hash so
    subsequent /run-again calls naturally find the new larger budget.

    Mailing list opt-in is implied by consent=True; downstream worker can
    forward to Resend/SendGrid/etc."""
    import re

    email = (body.email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    agent = await session.get(Agent, body.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    email_hash = _hash_email(email)
    unlock_key = _email_unlock_key(str(agent.id), email_hash)

    already = await redis.get(unlock_key)
    if already:
        # Idempotent: same email + agent → same bonus, no double-grant
        try:
            existing = int(already)
        except (TypeError, ValueError):
            existing = 0
        return EmailUnlockResponse(
            granted=False,
            bonus_runs=0,
            total_unlocked=existing,
            message="Email already on file for this agent.",
        )

    await redis.setex(unlock_key, _EMAIL_UNLOCK_TTL, _EMAIL_UNLOCK_BONUS)

    # Reset the guest-runs counter so the visitor immediately gets the bonus.
    # We key on email hash to make the unlock cookie-proof.
    guest_key = _shared_run_guest_key(str(agent.id), f"email:{email_hash}")
    await redis.setex(guest_key, _GUEST_RUN_TTL, 0)

    # Lightweight audit / list capture — opt-in marketing emails are stored
    # in a separate Redis set the org owner can export.
    if body.consent:
        await redis.sadd(
            f"public_demo_emails:{agent.organization_id}",
            f"{email}|{agent.id}|{body.source or 'unknown'}|{int(datetime.utcnow().timestamp())}",
        )

    logger.info(
        f"email-unlock: granted {_EMAIL_UNLOCK_BONUS} runs for "
        f"email_hash={email_hash} agent={agent.id} source={body.source}"
    )

    return EmailUnlockResponse(
        granted=True,
        bonus_runs=_EMAIL_UNLOCK_BONUS,
        total_unlocked=_EMAIL_UNLOCK_BONUS,
        message=f"Unlocked {_EMAIL_UNLOCK_BONUS} more runs. We sent a copy to {email}.",
    )


@router.get(
    "/runs/{run_id}",
    dependencies=[Depends(RateLimiter(times=200, seconds=60))],
)
async def get_shared_run(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis_pool),
    guest_id: Optional[str] = None,
):
    """Return run details for a publicly shared run."""
    run = await session.get(Run, run_id)
    if not run or not run.is_public:
        raise HTTPException(status_code=404, detail="Run not found")

    agent = await session.get(Agent, run.agent_id) if run.agent_id else None
    config = (agent.public_access_config or {}) if agent else {}
    price_monthly = float(
        config.get(
            "price_monthly", (agent.price if agent else None) or _DEFAULT_PRICE_MONTHLY
        )
    )
    free_run_limit = int(config.get("free_run_limit", _DEFAULT_FREE_RUN_LIMIT))

    # Resolve runs_used for this guest
    effective_guest_id = guest_id or request.headers.get("X-Guest-Id", "")
    runs_used = 0
    if effective_guest_id and agent:
        val = await redis.get(_shared_run_guest_key(str(agent.id), effective_guest_id))
        runs_used = int(val) if val else 0

    return {
        "id": str(run.id),
        "status": run.status.value if run.status else "pending",
        "is_public": run.is_public,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "input_data": run.input_data,
        "output_data": run.output_data,
        "graph_snapshot": _sanitize_graph_snapshot(run.graph_snapshot),
        "agent": (
            {
                "id": str(agent.id),
                "name": agent.name,
                "description": agent.description,
                "price_monthly": price_monthly,
                "free_run_limit": free_run_limit,
            }
            if agent
            else None
        ),
        "runs_used": runs_used,
        "free_run_limit": free_run_limit,
    }


@router.post(
    "/runs/{run_id}/run-again",
    dependencies=[Depends(RateLimiter(times=30, seconds=60))],
)
async def run_shared_agent_again(
    run_id: uuid.UUID,
    payload: PublicAgentRunRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis=Depends(get_redis_pool),
):
    """
    Let a visitor run the same agent as a shared run with their own input.
    Tracks guest run counts and enforces the free run limit.
    The agent does NOT need to be visibility=PUBLIC — sharing the run is enough.
    """
    source_run = await session.get(Run, run_id)
    if not source_run or not source_run.is_public:
        raise HTTPException(status_code=404, detail="Run not found")

    if not source_run.agent_id:
        raise HTTPException(status_code=400, detail="No agent associated with this run")

    agent = await session.get(Agent, source_run.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    config = agent.public_access_config or {}
    free_run_limit = int(config.get("free_run_limit", _DEFAULT_FREE_RUN_LIMIT))
    price_monthly = float(
        config.get("price_monthly", agent.price or _DEFAULT_PRICE_MONTHLY)
    )

    # Resolve guest_id — accept caller's identifier OR derive from the
    # email-unlock token if present (X-Email-Unlock: <hash>). The email-derived
    # bucket is what gets credited bonus runs by /email-unlock.
    guest_id = payload.guest_id or request.headers.get("X-Guest-Id", "")
    email_hash = (request.headers.get("X-Email-Unlock") or "").strip()
    if email_hash:
        # email-unlock keyed identity takes priority — survives cookie clears
        guest_id = f"email:{email_hash}"
    elif not guest_id:
        guest_id = secrets.token_hex(16)

    redis_key = _shared_run_guest_key(str(agent.id), guest_id)
    current_raw = await redis.get(redis_key)
    current_count = int(current_raw) if current_raw else 0

    # Apply any unlock bonus tied to this email
    effective_limit = free_run_limit
    if email_hash:
        bonus_raw = await redis.get(_email_unlock_key(str(agent.id), email_hash))
        if bonus_raw:
            try:
                effective_limit += int(bonus_raw)
            except (TypeError, ValueError):
                pass

    if current_count >= effective_limit:
        raise HTTPException(
            status_code=402,
            detail={
                "limit_reached": True,
                "runs_used": current_count,
                "free_run_limit": effective_limit,
                "email_unlock_available": not email_hash,
                "price_monthly": price_monthly,
                "agent_id": str(agent.id),
                "agent_name": agent.name,
                "message": (
                    f"You've used all {effective_limit} free runs. "
                    + (
                        "Start a trial to keep going."
                        if email_hash
                        else "Drop an email to unlock 5 more, or start a trial."
                    )
                ),
            },
        )

    new_count = await redis.incr(redis_key)
    await redis.expire(redis_key, _GUEST_RUN_TTL)

    # Use published version graph, fall back to draft
    graph_data = None
    if agent.published_version_id:
        from src.db.models.agent import AgentVersion

        version = await session.get(AgentVersion, agent.published_version_id)
        if version:
            graph_data = version.graph_data

    if not graph_data:
        graph_data = agent.draft_graph_data or {}

    if not graph_data.get("nodes"):
        raise HTTPException(status_code=400, detail="Agent has no graph configured")

    new_run = Run(
        agent_id=agent.id,
        organization_id=agent.organization_id,
        input_data=payload.input_data,
        status=RunStatus.PENDING,
    )
    session.add(new_run)
    await session.commit()
    await session.refresh(new_run)

    run_agent_task.send(str(new_run.id))

    return {
        "run_id": str(new_run.id),
        "status": "started",
        "runs_used": new_count,
        "free_run_limit": free_run_limit,
        "guest_id": guest_id,
    }


@router.get(
    "/runs/{run_id}/logs",
    dependencies=[Depends(RateLimiter(times=200, seconds=60))],
)
async def get_shared_run_logs(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    redis_pool=Depends(get_redis_pool),
):
    """Return stored logs for a publicly shared run."""
    run = await session.get(Run, run_id)
    if not run or not run.is_public:
        raise HTTPException(status_code=404, detail="Run not found")

    key = f"run-log-history:{run_id}"
    logs = await redis_pool.lrange(key, 0, -1)
    # Public shared runs are unauthenticated — scrub to a client-safe feed so we
    # never leak raw tool I/O, terminal commands, reasoning, or credentials.
    safe: list[dict] = []
    for entry in logs:
        event = sanitize_event_for_client(json.loads(entry))
        if event is not None:
            safe.append(event)
    return safe


@router.get(
    "/runs/{run_id}/stream",
    dependencies=[Depends(RateLimiter(times=100, seconds=60))],
)
async def stream_shared_run_logs(
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis_pool=Depends(get_redis_pool),
):
    """Live SSE stream for a publicly shared run."""
    run = await session.get(Run, run_id)
    if not run or not run.is_public:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_gen():
        yield f"data: {json.dumps({'type': 'status', 'status': run.status.value if run.status else 'pending'})}\n\n"
        pubsub = redis_pool.pubsub()
        channel = f"run-log-stream:{run_id}"
        await pubsub.subscribe(channel)
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg:
                    safe = sanitize_raw_for_client(msg["data"])
                    if safe is not None:
                        yield f"data: {safe.decode()}\n\n"
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ── Public ROI Report ────────────────────────────────────────────────────────


@router.get("/report/{token}")
async def get_public_roi_report(
    token: str,
    redis=Depends(get_redis_pool),
):
    """Fetch a shared ROI report by token — no auth required. Proxies to the report endpoint."""
    from sqlalchemy import desc as sa_desc
    from sqlalchemy import func as sa_func
    from sqlmodel import select as sm_select

    from src.db.models import Agent, Run
    from src.db.models.client import AgentClient
    from src.db.models.organization import Organization
    from src.db.models.run import RunStatus
    from src.db.postgresql import postgres_db

    raw = await redis.get(f"report_share:{token}")
    if not raw:
        raise HTTPException(status_code=404, detail="Report not found or expired")

    meta = json.loads(raw)
    client_id = uuid.UUID(meta["client_id"])

    async with postgres_db.async_session_maker() as session:
        client = await session.get(AgentClient, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="Client not found")

        total_runs = (
            await session.execute(
                sm_select(sa_func.count(Run.id)).where(Run.agent_client_id == client_id)
            )
        ).scalar() or 0

        success_runs = (
            await session.execute(
                sm_select(sa_func.count(Run.id)).where(
                    Run.agent_client_id == client_id, Run.status == RunStatus.COMPLETED
                )
            )
        ).scalar() or 0

        top_agents = (
            await session.execute(
                sm_select(Agent.name, sa_func.count(Run.id).label("cnt"))
                .join(Run, Run.agent_id == Agent.id)
                .where(Run.agent_client_id == client_id)
                .group_by(Agent.name)
                .order_by(sa_desc("cnt"))
                .limit(5)
            )
        ).all()

        org = await session.get(Organization, uuid.UUID(meta["organization_id"]))
        agency_name = org.name if org else "Agency"

    success_rate = (success_runs / total_runs * 100) if total_runs > 0 else 0
    hours_saved = round(success_runs * 0.75, 1)

    return {
        "client_name": client.name,
        "agency_name": agency_name,
        "hourly_rate_usd": org.hourly_rate_usd if org else None,
        "stats": {
            "total_runs": total_runs,
            "success_runs": success_runs,
            "success_rate": round(success_rate, 1),
            "hours_saved": hours_saved,
        },
        "top_agents": [{"name": a.name, "runs": a.cnt} for a in top_agents],
        "generated_at": datetime.utcnow().isoformat(),
    }


# ============================================================
# PUBLIC RELIABILITY SCORECARD
# ============================================================


class ReliabilityScorecardResponse(BaseModel):
    platform_name: str = "Crossnode"
    score: int  # 0-100 composite
    status: str  # "operational" | "degraded" | "down"
    last_updated: str

    # Components
    uptime_pct_30d: float
    api_success_rate_pct: float
    avg_response_time_ms: float
    incident_count_30d: int

    # Breakdown
    daily_scores: list[dict[str, Any]]


@router.get(
    "/reliability",
    response_model=ReliabilityScorecardResponse,
    dependencies=[Depends(RateLimiter(times=200, seconds=60))],
)
async def get_reliability_scorecard(
    session: AsyncSession = Depends(get_session),
):
    """Public reliability scorecard. Aggregated from platform run data."""
    now = datetime.utcnow()
    thirty_days_ago = now - timedelta(days=30)

    # Overall runs in last 30d
    total_runs_30d = (
        await session.execute(
            select(func.count(Run.id)).where(
                Run.created_at >= thirty_days_ago,
                Run.parent_run_id.is_(None),
            )
        )
    ).scalar() or 0

    success_runs_30d = (
        await session.execute(
            select(func.count(Run.id)).where(
                Run.created_at >= thirty_days_ago,
                Run.status == RunStatus.COMPLETED,
                Run.parent_run_id.is_(None),
            )
        )
    ).scalar() or 0

    # Avg response time for completed runs
    avg_resp = (
        await session.execute(
            select(
                func.avg(
                    func.extract("epoch", Run.completed_at - Run.created_at) * 1000
                )
            ).where(
                Run.created_at >= thirty_days_ago,
                Run.status == RunStatus.COMPLETED,
                Run.completed_at.is_not(None),
            )
        )
    ).scalar() or 0.0

    api_success_rate = (success_runs_30d / max(total_runs_30d, 1)) * 100

    # Estimate uptime: treat days with >95% success rate as "up"
    daily_stmt = (
        select(
            func.date_trunc("day", Run.created_at).label("day"),
            func.count(Run.id).label("total"),
            func.sum(func.case((Run.status == RunStatus.COMPLETED, 1), else_=0)).label(
                "success"
            ),
        )
        .where(
            Run.created_at >= thirty_days_ago,
            Run.parent_run_id.is_(None),
        )
        .group_by(func.date_trunc("day", Run.created_at))
        .order_by(func.date_trunc("day", Run.created_at))
    )
    daily_rows = (await session.execute(daily_stmt)).all()

    daily_scores = []
    up_days = 0
    for row in daily_rows:
        day_total = row.total or 0
        day_success = row.success or 0
        day_rate = (day_success / max(day_total, 1)) * 100
        is_up = day_rate >= 95.0
        if is_up:
            up_days += 1
        daily_scores.append(
            {
                "date": row.day.strftime("%Y-%m-%d"),
                "runs": day_total,
                "success_rate": round(day_rate, 1),
                "status": "up" if is_up else "degraded",
            }
        )

    uptime_pct = (up_days / max(len(daily_rows), 1)) * 100

    # Composite score (weighted)
    score = int(
        (uptime_pct * 0.4)
        + (api_success_rate * 0.35)
        + (max(0, 100 - (avg_resp / 100)) * 0.25)
    )
    score = min(100, max(0, score))

    status = "operational" if score >= 95 else "degraded" if score >= 80 else "down"
    incident_count = len(daily_rows) - up_days

    return ReliabilityScorecardResponse(
        score=score,
        status=status,
        last_updated=now.isoformat(),
        uptime_pct_30d=round(uptime_pct, 1),
        api_success_rate_pct=round(api_success_rate, 1),
        avg_response_time_ms=round(float(avg_resp), 1),
        incident_count_30d=incident_count,
        daily_scores=daily_scores,
    )


# ── Agency Audit (public conversion funnel) ──────────────────────────────────
# S-tier: Turnstile, SSRF, domain cache, concurrency limit, domain rate limit.

_AUDIT_CONCURRENT_KEY = "audit:active_jobs"
# Global cap on simultaneous live audits (each spends a Daytona sandbox + LLM
# budget). Env-overridable so launch-day capacity can be raised without a deploy.
_AUDIT_MAX_CONCURRENT = int(os.getenv("AUDIT_MAX_CONCURRENT", "10"))
_AUDIT_DOMAIN_CACHE_PREFIX = "audit:domain:"
_AUDIT_DOMAIN_RATE_PREFIX = "audit:domain_rate:"

# Domain cache + per-domain rate limit. Enabled for launch: a repeat audit of the
# same domain+type within 24h returns the cached report instantly (saves a full
# sandbox+LLM run) and the per-domain hourly limit blocks abuse. Was temporarily
# False during live-stream verification; the stream is now verified stable.
# Env-overridable kill-switch in case a bad cached report needs to be bypassed.
_AUDIT_CACHE_ENABLED = os.getenv("AUDIT_CACHE_ENABLED", "true").lower() == "true"


class AuditFile(BaseModel):
    name: str = Field(..., max_length=200)
    # base64-encoded file content (no data: URL prefix)
    content_base64: str = Field(..., max_length=4_000_000)  # ~3MB decoded
    mime: Optional[str] = Field(default=None, max_length=120)


class AuditStartRequest(BaseModel):
    url: str = Field(..., max_length=500)
    cf_turnstile_token: Optional[str] = None
    audit_type: str = Field(default="agency", max_length=32)
    custom_prompt: str = Field(default="", max_length=12000)
    # Optional context files (campaign report, brief, etc.) the agent reads.
    files: list[AuditFile] = Field(default_factory=list, max_length=5)
    # How deeply the agent explores the site: quick | balanced | thorough.
    exploration_depth: str = Field(default="balanced", max_length=16)


@router.post(
    "/audit",
    dependencies=[Depends(RateLimiter(times=10, seconds=3600))],
)
async def start_agency_audit(
    payload: AuditStartRequest,
    request: Request,
    redis_pool=Depends(get_redis_pool),
):
    """
    Start a public agency website audit.
    Rate-limits: 5/IP/hour (fastapi-limiter) + 1/domain/hour + 10 global concurrent.
    Domain cache: returns instantly if the same domain was audited in the last 24h.
    """
    import time as _time

    from src.core.agency_audit import validate_audit_url
    from src.core.turnstile import verify_turnstile

    # ── Turnstile ─────────────────────────────────────────────────────────────
    # Fail-closed on the audit: it spends a sandbox + LLM budget per call and has
    # a confirmed Turnstile widget. Other public endpoints (build/suggest/convert)
    # stay permissive until they render a widget.
    await verify_turnstile(payload.cf_turnstile_token, request, enforce=True)

    # ── URL validation + SSRF ─────────────────────────────────────────────────
    try:
        url, domain = validate_audit_url(payload.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── Domain cache: instant return if already audited ───────────────────────
    # Key includes audit_type so different types on the same domain are cached
    # independently (security audit ≠ SEO audit for the same site).
    _valid_audit_types = {
        "agency",
        "security",
        "competitor",
        "seo",
        "content",
        "technical",
        "email",
    }
    audit_type_safe = (
        payload.audit_type if payload.audit_type in _valid_audit_types else "agency"
    )
    domain_cache_key = f"{_AUDIT_DOMAIN_CACHE_PREFIX}{audit_type_safe}:{domain}"
    cached_raw = (
        await redis_pool.get(domain_cache_key) if _AUDIT_CACHE_ENABLED else None
    )
    if cached_raw:
        # Create a synthetic job_id with the cached report pre-loaded in the log
        job_id = str(uuid.uuid4())
        cached_report = json.loads(cached_raw)
        cached_report.setdefault("url", url)
        for ev in [
            json.dumps({"type": "report_ready", "report": cached_report}),
            json.dumps({"type": "done"}),
        ]:
            await redis_pool.rpush(f"audit:log:{job_id}", ev)
        await redis_pool.expire(f"audit:log:{job_id}", 7200)
        await redis_pool.setex(
            f"audit:report:{job_id}", 7200, json.dumps(cached_report)
        )
        await redis_pool.setex(
            f"audit:job:{job_id}",
            7200,
            json.dumps({"url": url, "domain": domain, "status": "cached"}),
        )
        return {"job_id": job_id, "cached": True}

    # ── Per-domain rate limit (1 fresh audit per domain+type per hour) ──────────
    # Per-type so security audit doesn't block a separate SEO audit on same domain.
    # Gated off while _AUDIT_CACHE_ENABLED is False so we can re-test the same
    # domain repeatedly during live-stream verification.
    if _AUDIT_CACHE_ENABLED:
        domain_rate_key = f"{_AUDIT_DOMAIN_RATE_PREFIX}{audit_type_safe}:{domain}"
        if await redis_pool.exists(domain_rate_key):
            raise HTTPException(
                status_code=429,
                detail="This domain was recently audited. Try again in an hour.",
            )
        await redis_pool.setex(domain_rate_key, 3600, "1")

    # ── Global concurrency limit ──────────────────────────────────────────────
    cutoff = _time.time() - 3600
    await redis_pool.zremrangebyscore(_AUDIT_CONCURRENT_KEY, 0, cutoff)
    active = await redis_pool.zcard(_AUDIT_CONCURRENT_KEY)
    if active >= _AUDIT_MAX_CONCURRENT:
        raise HTTPException(
            status_code=503,
            detail="Too many audits in progress. Try again in a few minutes.",
        )

    # ── Start audit ───────────────────────────────────────────────────────────
    job_id = str(uuid.uuid4())

    # Register in active-jobs set (self-healing: entries expire after 1h)
    await redis_pool.zadd(_AUDIT_CONCURRENT_KEY, {job_id: _time.time()})

    # Store job metadata
    await redis_pool.setex(
        f"audit:job:{job_id}",
        7200,
        json.dumps({"url": url, "domain": domain, "status": "pending"}),
    )

    # Stash any uploaded context files in Redis (keyed by job_id) so the runner
    # can write them into the sandbox for the agent to read. Bounded: max 5 files,
    # ~3MB decoded each, ~8MB total — enough for a campaign report + a brief.
    if payload.files:
        import base64 as _b64

        _safe_files = []
        _total = 0
        for f in payload.files[:5]:
            try:
                _decoded = _b64.b64decode(f.content_base64, validate=False)
            except Exception:  # nosec B112 - skip a malformed/undecodable upload
                continue
            if not _decoded or len(_decoded) > 3_000_000:
                continue
            _total += len(_decoded)
            if _total > 8_000_000:
                break
            # Sanitize filename → basename only, no path traversal
            _name = (f.name or "file").replace("\\", "/").split("/")[-1][:120] or "file"
            _safe_files.append(
                {"name": _name, "content_base64": f.content_base64, "mime": f.mime}
            )
        if _safe_files:
            await redis_pool.setex(
                f"audit:files:{job_id}", 7200, json.dumps(_safe_files)
            )

    from src.tasks._audit import run_agency_audit_task

    audit_type = (payload.audit_type or "agency").strip()[:32]
    _cp_limit = 12000 if audit_type == "email" else 500
    custom_prompt = (payload.custom_prompt or "").strip()[:_cp_limit]
    depth = (payload.exploration_depth or "balanced").strip().lower()
    if depth not in ("quick", "balanced", "thorough"):
        depth = "balanced"
    run_agency_audit_task.send(job_id, url, domain, audit_type, custom_prompt, depth)

    return {"job_id": job_id, "cached": False}


@router.post("/audit/{job_id}/build-agent")
async def build_agent_from_audit(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    redis_pool=Depends(get_redis_pool),
):
    """
    Post-signup: build the first recommended agent from the audit report.
    Called by the onboarding flow after account creation.
    Requires valid JWT (user must be authenticated after signup).
    """
    from src.core.security import get_current_user
    from src.db.models.agent import Agent

    user = await get_current_user(request, session, request.query_params.get("token"))
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    raw = await redis_pool.get(f"audit:report:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Audit report not found or expired")

    report = json.loads(raw)
    opportunities = report.get("opportunities", [])
    if not opportunities:
        raise HTTPException(status_code=422, detail="Audit report has no opportunities")

    opp = opportunities[0]
    niche = report.get("niche", "")

    agent = Agent(
        name=opp["service_name"],
        description=(
            f"{opp['tagline']}\n\n{opp['description']}\n\n"
            f"Built for: {niche}\n{opp.get('why_fits', '')}"
        ),
        user_id=user.id,
        organization_id=user.active_organization_id,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    logger.info(
        f"Audit {job_id}: agent {agent.id} created for user {user.id} "
        f"— {opp['service_name']!r}"
    )
    return {
        "agent_id": str(agent.id),
        "agent_name": agent.name,
        "niche": niche,
        "redirect": f"/agents/{agent.id}",
    }


@router.get("/audit/{job_id}/stream")
async def stream_agency_audit(
    job_id: str,
    request: Request,
    redis_pool=Depends(get_redis_pool),
):
    """SSE stream delivering progress events and final report for an audit job."""
    job = await redis_pool.get(f"audit:job:{job_id}")
    if not job:
        raise HTTPException(status_code=404, detail="Audit job not found")

    async def event_gen():
        # Replay any buffered events for reconnect support
        buffered = await redis_pool.lrange(f"audit:log:{job_id}", 0, -1)
        already_done = False
        for item in buffered:
            yield f"data: {item}\n\n"
            try:
                if json.loads(item).get("type") == "done":
                    already_done = True
            except Exception:  # nosec
                pass

        # If the log already had a "done" event, the job is fully replayed — stop here.
        # Otherwise check the report cache and short-circuit for completed jobs.
        if already_done:
            return

        # If report already exists, short-circuit
        existing = await redis_pool.get(f"audit:report:{job_id}")
        if existing:
            yield f"data: {json.dumps({'type': 'report_ready', 'report': json.loads(existing)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return

        pubsub = redis_pool.pubsub()
        channel = f"audit:events:{job_id}"
        await pubsub.subscribe(channel)
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg:
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    yield f"data: {data}\n\n"
                    try:
                        if json.loads(data).get("type") == "done":
                            break
                    except Exception:  # nosec
                        pass
                else:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete(
    "/audit/{job_id}",
    dependencies=[Depends(RateLimiter(times=20, seconds=60))],
)
async def cancel_agency_audit(
    job_id: str,
    redis_pool=Depends(get_redis_pool),
):
    """
    Cancel an in-progress audit. Removes it from the active-jobs counter,
    pushes a 'cancelled' event so SSE listeners disconnect cleanly, and
    deletes the ephemeral Redis keys. Idempotent — safe to call even after
    the job has already finished.
    """
    # Publish cancellation event so any open SSE connection closes
    cancel_event = json.dumps({"type": "cancelled"})
    done_event = json.dumps({"type": "done"})
    channel = f"audit:events:{job_id}"
    await redis_pool.publish(channel, cancel_event)
    await redis_pool.publish(channel, done_event)

    # Remove from concurrency counter
    await redis_pool.zrem(_AUDIT_CONCURRENT_KEY, job_id)

    # Clean up ephemeral keys — leave domain cache intact (report may be valid)
    await redis_pool.delete(
        f"audit:job:{job_id}",
        f"audit:log:{job_id}",
        f"audit:report:{job_id}",
    )

    return {"ok": True}


@router.get("/audit/{job_id}/browser-stream/view")
async def public_audit_browser_stream_viewer(job_id: str):
    """Canvas-based 30fps live viewer for a public audit (no auth required)."""
    from fastapi.responses import HTMLResponse

    # Points at the MJPEG relay so <img> works natively.
    stream_path = f"/api/v1/public/audit/{job_id}/browser-stream"
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0a;overflow:hidden;width:100vw;height:100vh}}
img{{display:block;width:100%;height:100%;object-fit:contain}}
#st{{position:fixed;top:6px;right:8px;font:11px/1 monospace;color:rgba(255,255,255,.35);pointer-events:none}}
</style></head>
<body>
<img id="stream" src="{stream_path}" alt="live stream">
<div id="st">connecting…</div>
<script>
const el=document.getElementById('stream'),st=document.getElementById('st');
el.onload=()=>st.textContent='live ●';
el.onerror=()=>{{st.textContent='reconnecting…';setTimeout(()=>{{el.src='{stream_path}?t='+Date.now()}},2000);}};
</script>
</body></html>"""
    return HTMLResponse(content=html)


@router.get("/audit/{job_id}/browser-stream")
async def public_audit_browser_stream_relay(
    job_id: str,
    redis_pool=Depends(get_redis_pool),
):
    """SSE relay of base64 JPEG frames from cn-stream-bridge for a public audit (no auth).

    cn-stream-bridge (port 9224) connects to agent-browser's WebSocket stream server
    (port 9223) and re-emits frames as SSE — same pattern as Suna's agent-browser-viewer.
    This endpoint is a simple SSE passthrough; no MJPEG parsing needed.
    """
    import asyncio

    import aiohttp

    # Wait up to 20s for the harness to write the stream metadata.
    raw = None
    for _attempt in range(20):
        raw = await redis_pool.get(f"harness_stream:{job_id}")
        if raw:
            break
        await asyncio.sleep(1)

    if not raw:
        logger.warning(
            f"public audit stream relay: harness_stream:{job_id} not found after 20s"
        )
        raise HTTPException(status_code=503, detail="Browser stream not yet available")

    info = json.loads(raw)
    daytona_base = info["daytona_url"].rstrip("/")
    daytona_token = info.get("token", "")
    headers: dict = {}
    if daytona_token:
        headers["x-daytona-preview-token"] = daytona_token

    # cn-stream-bridge on port 9224 already serves text/event-stream.
    # Just pass the SSE lines through — no MJPEG parsing needed.
    stream_url = daytona_base + "/stream"
    if daytona_token:
        stream_url += f"?dtPreviewToken={daytona_token}"

    logger.info(
        f"public audit stream relay: job={job_id} stream_url={stream_url} "
        f"has_token={bool(daytona_token)}"
    )

    async def relay_sse():
        try:
            async with aiohttp.ClientSession() as client:
                connected = False
                consec_errors = 0
                while consec_errors < 30:
                    try:
                        async with client.get(
                            stream_url,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=None, connect=10),
                        ) as resp:
                            if resp.status != 200:
                                if resp.status in (502, 503, 504, 404):
                                    if consec_errors == 0 or consec_errors % 5 == 0:
                                        logger.info(
                                            f"public audit stream relay: attempt {consec_errors+1} "
                                            f"got {resp.status} job={job_id}"
                                        )
                                    consec_errors += 1
                                    await asyncio.sleep(1.5)
                                    continue
                                logger.warning(
                                    f"public audit stream relay upstream error: "
                                    f"{resp.status} (non-retryable) job={job_id}"
                                )
                                return
                            if not connected:
                                connected = True
                                logger.info(
                                    f"public audit stream relay: connected job={job_id} "
                                    f"(after {consec_errors + 1} attempts)"
                                )
                            consec_errors = 0
                            # Pass SSE lines through as-is; upstream already sends
                            # `data: <base64>\n\n` so no transformation needed.
                            async for line in resp.content:
                                if line:
                                    yield line.decode("utf-8", errors="replace")
                            await asyncio.sleep(0.3)
                    except (TimeoutError, aiohttp.ClientConnectorError) as exc:
                        if consec_errors == 0 or consec_errors % 5 == 0:
                            logger.info(
                                f"public audit stream relay: attempt {consec_errors+1} "
                                f"connect error job={job_id}: {type(exc).__name__}: {exc}"
                            )
                        consec_errors += 1
                        await asyncio.sleep(1.5)
                    except aiohttp.ClientError as exc:
                        consec_errors += 1
                        await asyncio.sleep(1.5)
                if not connected:
                    logger.warning(
                        f"public audit stream relay: upstream never online after 30 attempts job={job_id}"
                    )
        except Exception as exc:
            logger.warning(f"public audit stream relay error for {job_id}: {exc}")

    return StreamingResponse(
        relay_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/audit/report/{job_id}")
async def get_public_audit_report(
    job_id: str,
    redis_pool=Depends(get_redis_pool),
):
    """
    Fetch a completed audit report by job_id — no auth required.
    Used by the public shareable report page and OG image generation.
    Returns 404 if the report has expired (2h TTL in Redis).
    """
    raw = await redis_pool.get(f"audit:report:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Report not found or expired")
    return json.loads(raw)


@router.get("/audit/{job_id}/screenshot/{token}")
async def get_audit_screenshot(
    job_id: str,
    token: str,
    redis_pool=Depends(get_raw_redis_pool),
):
    """Serve a screenshot captured during a public audit (stored in Redis, 1h TTL)."""
    import base64 as _b64

    from fastapi.responses import Response

    # Raw (non-decoding) client: the screenshot is binary PNG bytes; the default
    # decode_responses=True pool raises UnicodeDecodeError (byte 0x89) on it.
    data = await redis_pool.get(f"browser_screenshot:{token}:bytes")
    if not data:
        raise HTTPException(status_code=404, detail="Screenshot not found or expired")
    png = _b64.b64decode(data) if isinstance(data, str) else data
    return Response(content=png, media_type="image/png")


@router.get("/showcase", dependencies=[Depends(RateLimiter(times=60, seconds=60))])
async def public_showcase(session: AsyncSession = Depends(get_session)):
    """Public, SCRUBBED view of the configured showcase org running live.

    Outcomes only — no client names, no titles, no content, no file paths. Gated
    on SHOWCASE_ORG_ID (unset → disabled, nothing public). The §9.1 distribution
    surface: shipped deliverables, trust-gate catches, deliveries as a live feed.
    """
    org_id = settings.SHOWCASE_ORG_ID
    if not org_id:
        return {"enabled": False}
    try:
        org_uuid = uuid.UUID(org_id)
    except ValueError:
        return {"enabled": False}

    from src.db.models.artifact import Artifact, ArtifactKind, ArtifactStatus

    now = datetime.utcnow()
    since = now - timedelta(days=7)

    async def _count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one() or 0)

    shipped = await _count(
        select(func.count()).where(
            Artifact.organization_id == org_uuid, Artifact.created_at >= since
        )
    )
    delivered = await _count(
        select(func.count()).where(
            Artifact.organization_id == org_uuid,
            Artifact.status == ArtifactStatus.DELIVERED,
            Artifact.delivered_at >= since,
        )
    )
    caught = await _count(
        select(func.count()).where(
            Artifact.organization_id == org_uuid,
            Artifact.status == ArtifactStatus.REJECTED,
            Artifact.updated_at >= since,
        )
    )
    working = await _count(
        select(func.count()).where(
            Run.organization_id == org_uuid, Run.status == RunStatus.RUNNING
        )
    )

    rows = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.organization_id == org_uuid)
                .order_by(Artifact.created_at.desc())
                .limit(24)
            )
        )
        .scalars()
        .all()
    )
    beats: list[dict] = []
    for a in rows:
        v = (a.artifact_metadata or {}).get("verification") or {}
        is_catch = a.status == ArtifactStatus.REJECTED or v.get("decision") in (
            "revise",
            "reject",
        )
        if is_catch:
            kind = "catch"
        elif a.status == ArtifactStatus.DELIVERED:
            kind = "delivered"
        elif a.status == ArtifactStatus.APPROVED:
            kind = "approved"
        else:
            kind = "shipped"
        atype = (
            "app"
            if a.kind == ArtifactKind.LINK
            else (
                "report"
                if a.kind in (ArtifactKind.TEXT, ArtifactKind.FILE)
                else "deliverable"
            )
        )
        ts = a.reviewed_at if is_catch else a.delivered_at
        beats.append(
            {"kind": kind, "atype": atype, "ts": (ts or a.created_at).isoformat()}
        )

    return {
        "enabled": True,
        "stats": {
            "shipped": shipped,
            "delivered": delivered,
            "caught": caught,
            "working": working,
        },
        "beats": beats,
    }


# ─────────────────────────────────────────────────────────────────────────────
# The one-page close — public, token-scoped. The client views the proposal,
# e-signs, and (next) pays the deposit, all on one page. No auth: the token IS
# the credential (long-lived, like the portal magic link). See agreement_service.
# ─────────────────────────────────────────────────────────────────────────────
def _client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP for the signature audit trail (honors the proxy)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


async def _agreement_by_token(token: str, session: AsyncSession):
    from src.db.models.agreement import Agreement

    ag = (
        (
            await session.execute(
                select(Agreement).where(Agreement.public_token == token)
            )
        )
        .scalars()
        .first()
    )
    if not ag:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return ag


async def _agreement_public_payload(ag, session: AsyncSession) -> dict[str, Any]:
    from src.db.models.agreement import AgreementPublicRead
    from src.db.models.organization import Organization

    org = await session.get(Organization, ag.organization_id)
    data = AgreementPublicRead(
        title=ag.title,
        scope=ag.scope or [],
        terms=ag.terms,
        pricing_model=ag.pricing_model,
        currency=ag.currency,
        total_cents=ag.total_cents,
        deposit_cents=ag.deposit_cents,
        milestones=ag.milestones or [],
        performance_model=getattr(ag, "performance_model", "none") or "none",
        performance_bonus_cents=getattr(ag, "performance_bonus_cents", 0) or 0,
        performance_cap_meetings=getattr(ag, "performance_cap_meetings", None),
        status=ag.status,
        valid_until=ag.valid_until,
        signer_name=ag.signer_name,
        signed_at=ag.signed_at,
        agency_name=getattr(org, "name", None) if org else None,
        agency_logo_url=getattr(org, "logo_url", None) if org else None,
        agency_brand_color=getattr(org, "brand_color", None) if org else None,
        agency_payout_link=getattr(org, "payout_link", None) if org else None,
        agency_payment_instructions=(
            getattr(org, "payment_instructions", None) if org else None
        ),
    )
    return data.model_dump()


@router.get("/agreements/{token}")
async def view_agreement(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimiter(times=60, seconds=60)),
):
    """The client opens the close page. Marks it viewed (a conversion signal)."""
    from src.db.models.agreement import AgreementStatus
    from src.services import agreement_service

    ag = await _agreement_by_token(token, session)

    # Lazily expire if past its window.
    if agreement_service.is_expired(ag) and ag.status in (
        AgreementStatus.SENT.value,
        AgreementStatus.VIEWED.value,
    ):
        ag.status = AgreementStatus.EXPIRED.value
        ag.updated_at = datetime.utcnow()
        session.add(ag)
        await session.commit()

    # First open: sent → viewed.
    if ag.status == AgreementStatus.SENT.value:
        try:
            agreement_service.transition(ag, AgreementStatus.VIEWED.value)
            ag.viewed_at = datetime.utcnow()
            session.add(ag)
            await session.commit()
            await session.refresh(ag)
        except Exception:  # nosec B110 - view tracking is best-effort
            await session.rollback()

    return await _agreement_public_payload(ag, session)


class AgreementSignRequest(BaseModel):
    signer_name: str
    signer_email: Optional[str] = None
    accepted: bool = True


@router.post("/agreements/{token}/sign")
async def sign_agreement_public(
    token: str,
    body: AgreementSignRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimiter(times=10, seconds=60)),
):
    """The client e-signs. Captures the audit trail, provisions the client, and
    raises the deposit invoice (all in agreement_service.sign_agreement)."""
    from src.services import agreement_service
    from src.services.agreement_service import AgreementError

    if not body.accepted:
        raise HTTPException(status_code=400, detail="You must accept to sign.")

    ag = await _agreement_by_token(token, session)
    try:
        await agreement_service.sign_agreement(
            session,
            ag,
            signer_name=body.signer_name,
            signer_email=body.signer_email,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AgreementError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    await session.commit()
    await session.refresh(ag)

    payload = await _agreement_public_payload(ag, session)
    payload["deposit_invoice_id"] = (
        str(ag.deposit_invoice_id) if ag.deposit_invoice_id else None
    )
    return payload


@router.post("/agreements/{token}/deposit-checkout")
async def agreement_deposit_checkout(
    token: str,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimiter(times=20, seconds=60)),
):
    """One-session close: after signing, the client pays the deposit here. Mirrors
    the portal invoice-pay branching (Crossnode-own → direct charge; every other
    agency → its OWN payout link, no Connect). Returns the URL to redirect to; the
    existing client_invoice webhook marks it paid for the Crossnode-own direct
    charge (see stripe_webhook_service._handle_client_invoice_paid)."""
    from src.db.models.agreement import AgreementStatus
    from src.db.models.invoice import ClientInvoice, InvoiceStatus

    ag = await _agreement_by_token(token, session)
    if ag.status == AgreementStatus.PAID.value or (
        ag.status == AgreementStatus.ACTIVE.value
    ):
        return {"status": "paid"}
    if ag.status != AgreementStatus.SIGNED.value or not ag.deposit_invoice_id:
        raise HTTPException(status_code=409, detail="Nothing to pay yet — sign first.")

    inv = await session.get(ClientInvoice, ag.deposit_invoice_id)
    if not inv or inv.status == InvoiceStatus.PAID.value:
        return {"status": "paid"}

    org = await session.get(Organization, ag.organization_id)
    if not org:
        raise HTTPException(status_code=404, detail="Agency not found")

    base = str(get_settings().ALLOWED_ORIGINS[0]).rstrip("/")
    success_url = f"{base}/sign/{token}?deposit=paid"
    cancel_url = f"{base}/sign/{token}?deposit=canceled"

    from src.services.billing_service import billing_service

    own = get_settings().CROSSNODE_OWN_ORG_ID
    if own and str(org.id) == str(own):
        res = await billing_service.create_direct_checkout(
            inv, success_url=success_url, cancel_url=cancel_url
        )
        return {"url": res["url"]}

    # Every other agency uses its OWN payout link (no Connect): the invoice's
    # link, else the org's standing payout link.
    link = inv.payment_url or org.payout_link
    if link:
        return {"url": link}
    return {
        "status": "manual",
        "detail": "The agency will send you a payment link for the deposit.",
    }


@router.post("/agreements/{token}/decline")
async def decline_agreement_public(
    token: str,
    session: AsyncSession = Depends(get_session),
    _rl: None = Depends(RateLimiter(times=10, seconds=60)),
):
    from src.db.models.agreement import AgreementStatus
    from src.services import agreement_service
    from src.services.agreement_service import AgreementError

    ag = await _agreement_by_token(token, session)
    try:
        agreement_service.transition(ag, AgreementStatus.DECLINED.value)
    except AgreementError as e:
        raise HTTPException(status_code=409, detail=str(e))
    session.add(ag)
    await session.commit()
    return {"status": ag.status}
