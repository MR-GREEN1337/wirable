"""Operator-mailbox sending — send cold outreach from the operator's OWN inbox
(Gmail / Outlook via their connected Composio account), not a transactional ESP.

This is the correct cold-send architecture (what Smartlead/lemlist/gojiberry do):
mail originates from a real, warmed human mailbox the prospect can reply to, it's
ToS-safe (cold mail never touches our transactional Resend rails), and reputation
ties to the operator's domain — not ours. When no mailbox is connected, the caller
falls back to Resend.

All Composio specifics are isolated here and fully defensive: ANY failure returns
a falsy/None result so the send path degrades to Resend rather than dropping mail.
Not yet E2E-verified against a live connected mailbox — the action slug + param
names are best-effort per Composio's Gmail/Outlook toolkits and may need a one-line
tweak once a real mailbox is connected.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterator, Optional

from loguru import logger

# toolkit slug (lowercased) → (provider, send-action slug, new-message trigger slug)
_MAILBOX_PROVIDERS: dict[str, tuple[str, str, str]] = {
    "gmail": ("gmail", "GMAIL_SEND_EMAIL", "GMAIL_NEW_GMAIL_MESSAGE"),
    "googlemail": ("gmail", "GMAIL_SEND_EMAIL", "GMAIL_NEW_GMAIL_MESSAGE"),
    "outlook": ("outlook", "OUTLOOK_SEND_EMAIL", "OUTLOOK_NEW_MESSAGE"),
    "microsoft": ("outlook", "OUTLOOK_SEND_EMAIL", "OUTLOOK_NEW_MESSAGE"),
    "office365": ("outlook", "OUTLOOK_SEND_EMAIL", "OUTLOOK_NEW_MESSAGE"),
}

# provider → read-back action slug. Used only by the verification self-test to
# re-fetch a just-sent message as PROOF the send landed. Best-effort: an unknown
# or wrong slug degrades the self-test to "send fired" (message-id proof) without
# breaking anything on the real send path.
_FETCH_ACTIONS: dict[str, str] = {
    "gmail": "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
    "outlook": "OUTLOOK_GET_MESSAGE",
}


@dataclasses.dataclass
class MailboxSendResult:
    """Result of a real mailbox send. Truthy iff the send fired, so every existing
    ``if not sent:`` / ``if await send_via_mailbox(...):`` caller keeps working —
    but now the send also carries PROOF (``message_id``) that a message was created
    in the account. That id is what turns "best-effort, unverified" into a
    verifiable receipt (and enables read-back + idempotency)."""

    ok: bool
    provider: str = ""
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None

    def __bool__(self) -> bool:  # backward-compat with bool callers
        return self.ok


def _walk_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict node in an arbitrarily nested dict/list (the Composio send
    response shape varies by SDK version, so we search rather than assume a path)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_dicts(v)


def extract_message_ids(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Defensively pull (message_id, thread_id) out of a provider send response.
    Gmail returns ``id`` + ``threadId``; Outlook returns ``id``. Prefers an id that
    sits next to ``threadId``/``labelIds`` (the real message node) over any stray id."""
    mid: Optional[str] = None
    tid: Optional[str] = None
    if not isinstance(raw, (dict, list)):
        return None, None
    for node in _walk_dicts(raw):
        if tid is None:
            for k in ("threadId", "thread_id", "conversationId"):
                if node.get(k):
                    tid = str(node[k])
                    break
        if mid is None:
            for k in ("messageId", "message_id"):
                if node.get(k):
                    mid = str(node[k])
                    break
        # A bare "id" is only trustworthy as the message id when it sits in the
        # same node as a thread/label marker (i.e. the actual message object).
        if mid is None and node.get("id") and not isinstance(node["id"], (dict, list)):
            if any(k in node for k in ("threadId", "thread_id", "labelIds")):
                mid = str(node["id"])
    if mid is None:  # last resort: first scalar id anywhere
        for node in _walk_dicts(raw):
            if node.get("id") and not isinstance(node["id"], (dict, list)):
                mid = str(node["id"])
                break
    return mid, tid


def _toolkit_slug(conn: dict[str, Any]) -> str:
    tk = conn.get("toolkit")
    if isinstance(tk, dict):
        cand = tk.get("slug") or tk.get("name")
    else:
        cand = tk
    cand = (
        cand
        or conn.get("app_name")
        or conn.get("appName")
        or conn.get("app_slug")
        or conn.get("slug")
        or ""
    )
    return str(cand).strip().lower()


async def find_connected_mailboxes(organization_id) -> list[dict[str, Any]]:
    """All connected sending mailboxes (Gmail/Outlook) for the org.

    Composio scopes connections per ENTITY, and the org uses TWO conventions:
      • the AGENCY's own connect (Settings → Connections) binds to the connecting
        USER's id (``/connections/composio/initiate`` uses user_uuid=current_user.id);
      • a CLIENT's connect (portal) binds to the bare client UUID (BYOC invariant).
    So checking only the org UUID misses the agency's own mailbox — the exact bug
    where an operator connects Gmail and the desk still says 'not connected'. We
    check the org UUID AND every member user's id."""
    out: list[dict[str, Any]] = []
    try:
        from src.core.tools.composio_service import ComposioService

        # Candidate entities: the org UUID + each member user's id.
        entities: list[str] = [str(organization_id)]
        try:
            from sqlmodel import select as _sel

            from src.db.models.user import User as _User
            from src.db.postgresql import get_session_context

            async with get_session_context() as _s:
                _uids = (
                    (
                        await _s.execute(
                            _sel(_User.id).where(
                                _User.active_organization_id == organization_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            entities += [str(u) for u in _uids]
        except Exception:  # nosec B110 — fall back to org-UUID only
            pass

        svc = ComposioService()
        seen_conn: set = set()
        for entity in dict.fromkeys(entities):  # dedupe, preserve order
            try:
                conns = await svc.get_user_connections(entity)
            except Exception:  # nosec B112 — try the next entity
                continue
            for c in conns:
                prov = _MAILBOX_PROVIDERS.get(_toolkit_slug(c))
                if not prov:
                    continue
                conn_id = (
                    c.get("id")
                    or c.get("connected_account_id")
                    or c.get("connectedAccountId")
                )
                if conn_id and str(conn_id) not in seen_conn:
                    seen_conn.add(str(conn_id))
                    out.append(
                        {
                            "connection_id": str(conn_id),
                            "entity": entity,
                            "provider": prov[0],
                            "action": prov[1],
                            "reply_trigger": prov[2],
                        }
                    )
    except Exception as exc:  # nosec B110 — no mailbox → caller uses Resend
        logger.info(f"find_connected_mailboxes: {exc}")
    return out


async def find_connected_mailbox(organization_id) -> Optional[dict[str, Any]]:
    """The org's primary connected sending mailbox, or None (→ caller uses Resend)."""
    boxes = await find_connected_mailboxes(organization_id)
    return boxes[0] if boxes else None


async def pick_send_mailbox(
    organization_id, redis_pool, *, verified_at=None
) -> Optional[dict[str, Any]]:
    """Choose which connected mailbox to send THIS message from — the deliverability
    move. Rotates across ALL connected inboxes (round-robin) and SKIPS any that
    already hit today's per-inbox warmup cap, so no single inbox gets hammered and
    reputation stays spread. Returns None only when every inbox is capped (or none
    connected). The per-inbox identity for the cap is the connection_id."""
    from src.services.sending_governor import peek_allowed

    boxes = await find_connected_mailboxes(organization_id)
    if not boxes:
        return None
    if len(boxes) == 1:
        return boxes[0]
    try:
        offset = int(await redis_pool.incr(f"outbound:mbx_rr:{organization_id}"))
    except Exception:  # nosec B110 — rotation counter is best-effort
        offset = 0
    n = len(boxes)
    for i in range(n):
        b = boxes[(offset + i) % n]
        try:
            ok = await peek_allowed(
                redis_pool,
                organization_id,
                verified_at=verified_at,
                sender=b.get("connection_id"),
            )
        except Exception:  # nosec B112 — if we can't read, don't exclude this box
            ok = True
        if ok:
            return b
    return None  # every connected inbox is at today's cap


async def ensure_inbound_reply_triggers(session, *, organization_id, agent_id) -> int:
    """For EACH connected mailbox, make sure its provider 'new message' Composio
    trigger is subscribed + recorded as a Trigger row pointing at the outbound
    agent — so a prospect's reply fires the Composio webhook, which wakes the
    agent to qualify (and auto-stops the sequence via the prospect's stage). This
    is what makes the reply loop work for ANY connected provider, not just a
    single shared IMAP mailbox. Idempotent + defensive: a failure to set one up
    never blocks the engagement (the agent can still send; reply-capture just
    needs a retry / the IMAP fallback)."""
    import uuid as _uuid

    from sqlmodel import select

    from src.core.tools.composio_service import ComposioService
    from src.db.models.trigger import Trigger

    created = 0
    boxes = await find_connected_mailboxes(organization_id)
    for mb in boxes:
        slug = mb["reply_trigger"]
        try:
            # Idempotent: skip if this agent already has this trigger type.
            existing = (
                await session.execute(
                    select(Trigger.id)
                    .where(Trigger.agent_id == agent_id)
                    .where(Trigger.trigger_type == slug)
                    .limit(1)
                )
            ).first()
            if existing:
                continue

            inst = await ComposioService().create_trigger_instance(
                user_id=mb["entity"], slug=slug, trigger_config={}
            )
            cid = (
                inst.get("id")
                or inst.get("trigger_id")
                or inst.get("triggerId")
                or inst.get("nano_id")
            )
            if not cid:
                logger.warning(f"reply-trigger {slug}: no id in Composio response")
                continue
            session.add(
                Trigger(
                    agent_id=agent_id,
                    organization_id=(
                        organization_id
                        if isinstance(organization_id, _uuid.UUID)
                        else _uuid.UUID(str(organization_id))
                    ),
                    composio_trigger_id=str(cid),
                    trigger_type=slug,
                    is_active=True,
                    config={"purpose": "outbound_reply", "provider": mb["provider"]},
                )
            )
            await session.commit()
            created += 1
            logger.info(
                f"Inbound-reply trigger set: {slug} ({mb['provider']}) "
                f"agent={agent_id} org={organization_id}"
            )
        except Exception as exc:  # nosec B110 — reply-capture is best-effort
            logger.warning(f"ensure_inbound_reply_triggers {slug} failed: {exc}")
    return created


async def send_via_mailbox(
    mailbox: dict[str, Any],
    *,
    to: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
) -> MailboxSendResult:
    """Send one email through the operator's connected mailbox. Returns a
    ``MailboxSendResult`` that is truthy on success (so ``if not sent:`` callers are
    unchanged) and carries the provider message id as proof of a real send."""
    provider = str(mailbox.get("provider") or "")
    try:
        from src.core.tools.composio_service import ComposioService

        params: dict[str, Any] = {
            "recipient_email": to,
            "subject": subject,
            "body": body,
            "is_html": False,
        }
        if reply_to:
            # Gmail's action accepts extra_headers on most SDK versions; harmless
            # if ignored.
            params["extra_headers"] = {"Reply-To": reply_to}

        res = await ComposioService().execute_action(
            mailbox["provider"],
            mailbox["action"],
            params,
            mailbox["connection_id"],
            mailbox["entity"],
        )
        if res is None:
            return MailboxSendResult(ok=False, provider=provider, error="empty_response")
        if isinstance(res, dict) and res.get("successful") is False:
            return MailboxSendResult(
                ok=False,
                provider=provider,
                error=str(res.get("error") or "send_unsuccessful"),
                raw=res,
            )
        mid, tid = extract_message_ids(res)
        return MailboxSendResult(
            ok=True,
            provider=provider,
            message_id=mid,
            thread_id=tid,
            raw=res if isinstance(res, dict) else None,
        )
    except Exception as exc:  # nosec B110 — fall back to Resend
        logger.warning(f"send_via_mailbox failed ({provider}): {exc}")
        return MailboxSendResult(ok=False, provider=provider, error=str(exc))


async def fetch_message(
    mailbox: dict[str, Any], message_id: str
) -> Optional[dict[str, Any]]:
    """Best-effort read-back: re-fetch a just-sent message from the SAME connected
    mailbox to prove it actually landed. Returns the raw provider payload (Gmail
    'full' format / Outlook message), or None if the read slug is unconfirmed or
    the message isn't found. Never raises — the send proof stands without it."""
    provider = str(mailbox.get("provider") or "")
    slug = _FETCH_ACTIONS.get(provider)
    if not slug or not message_id:
        return None
    try:
        from src.core.tools.composio_service import ComposioService

        if provider == "gmail":
            params: dict[str, Any] = {
                "message_id": message_id,
                "user_id": "me",
                "format": "full",
            }
        else:
            params = {"message_id": message_id}
        res = await ComposioService().execute_action(
            provider, slug, params, mailbox["connection_id"], mailbox["entity"]
        )
        return res if isinstance(res, dict) else None
    except Exception as exc:  # nosec B110 — read-back is best-effort proof
        logger.info(f"fetch_message ({provider}): {exc}")
        return None
