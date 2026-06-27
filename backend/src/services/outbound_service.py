"""
Outbound email service — sends cold audit emails via Unipile (if configured)
or logs them locally for development.
"""
import uuid

import httpx

from ..core.config import settings


def pick_worst_failing_dim(agg: dict) -> tuple[str, str]:
    """Pick the highest-weighted FAILING dimension from an aggregated audit.

    Returns ``(dimension, evidence)``. The worst (highest rubric weight) failing
    dimension makes the strongest cold-email hook — it's the most impactful gap.
    Falls back to ("multiple dimensions", "—") when nothing is failing or the
    shape is unexpected.
    """
    dims = (agg or {}).get("dimensions", {}) or {}
    failing = [(d, v) for d, v in dims.items() if not v.get("passed", False)]
    failing.sort(key=lambda kv: kv[1].get("weight", 0) or 0, reverse=True)
    if failing:
        top_dim, top_v = failing[0]
        return top_dim, (top_v.get("evidence", "—") or "—")
    return "multiple dimensions", "—"


def render_audit_email(
    to_name: str,
    domain: str,
    score: int,
    report_url: str,
    failing_dim: str,
    evidence: str,
    token: str | None = None,
) -> tuple[str, str]:
    """Render the (subject, body) for a cold audit email.

    When ``token`` is provided, an invisible tracking-pixel reference is appended
    so opens are recorded via GET /api/v1/track/open/{token}. The exact body
    returned here is what callers should persist on the OutboundEmail row.
    """
    display_name = to_name or "there"
    subject = f"Your product scored {score}/100 on agent-readiness"

    body = f"""Hi {display_name},

We ran {domain} through an automated agent-readiness audit and found something worth sharing.

When we tested {failing_dim}, we found:
{evidence}

See the full trace (and what a fix would look like): {report_url}

If you're curious, we can generate an MCP server + agent integration layer for {domain}
in about 10 minutes — no engineering work on your end.

-- AgentReady
"""

    if token:
        pixel = f"{settings.REPORT_BASE_URL.rstrip('/')}/api/v1/track/open/{token}"
        # Invisible 1x1 open-tracking pixel (HTML clients render it; plain-text
        # clients ignore the trailing reference harmlessly).
        body += f'\n<img src="{pixel}" width="1" height="1" alt="" style="display:none" />\n'

    return subject, body


async def send_audit_email(
    to_email: str,
    to_name: str,
    domain: str,
    score: int,
    report_url: str,
    failing_dim: str,
    evidence: str,
    token: str | None = None,
) -> tuple[bool, str, str]:
    """
    Send a cold audit-results email to a prospect.

    Returns ``(sent, subject, body)`` — ``sent`` is True on success / graceful
    no-op (dev mode), False on delivery error. The returned subject/body are the
    exact rendered content (incl. the tracking pixel) so the caller can persist
    them on the OutboundEmail row.
    """
    subject, body = render_audit_email(
        to_name, domain, score, report_url, failing_dim, evidence, token
    )

    if not settings.UNIPILE_DSN or not settings.UNIPILE_API_KEY:
        # Dev / staging: log instead of sending
        from loguru import logger
        logger.info(f"[outbound] would send to {to_email}: {subject}")
        return True, subject, body

    dsn = settings.UNIPILE_DSN  # e.g. api49.unipile.com:13049
    # Send via Unipile email API
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"https://{dsn}/api/v1/emails",
                headers={
                    "X-API-KEY": settings.UNIPILE_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "account_id": settings.UNIPILE_ACCOUNT_ID,
                    "to": [{"display_name": to_name or "", "identifier": to_email}],
                    "subject": subject,
                    "body": body,
                },
            )
            success = r.status_code in (200, 201)
            if not success:
                print(f"[outbound] Unipile error {r.status_code}: {r.text[:200]}")
            return success, subject, body
        except httpx.HTTPError as exc:
            print(f"[outbound] HTTP error sending to {to_email}: {exc}")
            return False, subject, body


async def send_audit_email_for_company(db, company, agg: dict) -> bool:
    """Send a cold audit email for ``company`` and persist the OutboundEmail row.

    Shared by the /audit endpoint auto-trigger and the autonomous scout so the
    send + persist logic never diverges. Picks the worst failing dimension as the
    hook, mints a tracking token (so the embedded open-pixel matches the persisted
    row), sends via :func:`send_audit_email`, and on success writes the
    OutboundEmail row + flips ``company.outbound_status`` to "contacted".

    Returns True if an email was sent (incl. dev-mode log no-op), False otherwise.
    The caller owns the session; this function commits on success.
    """
    from ..models.outbound import OutboundEmail

    if not getattr(company, "founder_email", None):
        return False

    top_dim, evidence = pick_worst_failing_dim(agg)
    report_url = f"{settings.REPORT_BASE_URL.rstrip('/')}/report/{company.id}"
    token = uuid.uuid4().hex

    sent, subject, body = await send_audit_email(
        to_email=company.founder_email,
        to_name=company.founder_name or "",
        domain=company.domain,
        score=agg.get("score", 0) or 0,
        report_url=report_url,
        failing_dim=top_dim,
        evidence=evidence,
        token=token,
    )
    if sent:
        db.add(
            OutboundEmail(
                id=uuid.uuid4(),
                company_id=company.id,
                subject=subject,
                body=body,
                report_url=report_url,
                token=token,
            )
        )
        company.outbound_status = "contacted"
        await db.commit()
    return sent
