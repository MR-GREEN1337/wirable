"""
Transactional email via Resend — currently the signup welcome email.

Best-effort: send_welcome NEVER raises. A failed (or unconfigured) email must
not break signup. No-op when RESEND_API_KEY is empty.
"""
from __future__ import annotations

import logging

import httpx

from ..core.config import settings

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


def _welcome_html(name: str | None) -> str:
    greeting = f"Hi {name}," if name else "Hi there,"
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#ffffff;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#111827;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;margin:0 auto;padding:32px 24px;">
    <tr><td>
      <p style="font-size:16px;line-height:1.5;margin:0 0 16px;">{greeting}</p>
      <p style="font-size:16px;line-height:1.5;margin:0 0 16px;">
        Welcome to Wirable. We black-box test whether an AI agent can actually
        use your platform, score it 0 to 100, and host a proxy that fixes the
        rough edges without you changing any code.
      </p>
      <p style="font-size:16px;line-height:1.5;margin:0 0 24px;">
        Your account is on the free tier with 3 runs to start. Point it at a
        platform and watch the agent try.
      </p>
      <p style="margin:0 0 24px;">
        <a href="https://wirable.dev"
           style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;padding:12px 20px;border-radius:8px;">
          Open Wirable
        </a>
      </p>
      <p style="font-size:13px;line-height:1.5;color:#6b7280;margin:0;">
        wirable.dev
      </p>
    </td></tr>
  </table>
</body>
</html>"""


async def send_welcome(to_email: str, name: str | None = None) -> None:
    """Send the welcome email. Best-effort: never raises, no-op without a key."""
    api_key = (settings.RESEND_API_KEY or "").strip()
    if not api_key:
        logger.info("[email] RESEND_API_KEY unset — skipping welcome email to %s", to_email)
        return

    body = {
        "from": settings.WIRABLE_EMAIL_FROM,
        "to": [to_email],
        "subject": "Welcome to Wirable",
        "html": _welcome_html(name),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _RESEND_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
    except Exception:
        logger.exception("[email] welcome email to %s failed (non-fatal)", to_email)
