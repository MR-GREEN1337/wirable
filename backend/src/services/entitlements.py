"""
entitlements (Wirable) — launch gating: per-user run quota + access tiers.

Every agent run burns real Claude + sandbox dollars, so anonymous runs are off
(see core.config.WIRABLE_REQUIRE_AUTH). Each authenticated user gets a small free
quota (WIRABLE_FREE_RUNS). Judges / internal viewers redeem an access code for
unlimited free access. Paying users upgrade via Stripe (services handled in the
access endpoint; this module only flips the tier).

State lives on the existing Client row (keyed by the JWT `sub` = user UUID, the
same row github.py resolves). Three columns — runs_used, runs_limit, access_tier
— are added MIGRATION-FREE via a self-ensuring `ALTER TABLE ... ADD COLUMN IF
NOT EXISTS`, mirroring the pattern in services/mcp_monitor.py (the project is
Alembic-managed with drift, so we never add a migration). Reads are defensive:
if the columns somehow aren't present we degrade to sane defaults.

Public API (all async, all defensive — they never raise on a missing column):

    can_run(db, user_id)   -> (allowed: bool, reason: str, status: dict)
    record_run(db, user_id)-> None        (increment runs_used)
    redeem_code(db, user_id, code) -> dict (sets tier=unlimited on a valid code)
    grant_paid(db, user_id)-> dict         (sets tier=paid + high limit)
    status(db, user_id)    -> dict         ({tier, runs_used, runs_limit, remaining})
"""
from __future__ import annotations

import uuid as _uuid
from typing import Optional, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings

# Tiers that bypass the run limit entirely.
_UNLIMITED_TIERS = {"unlimited", "paid"}
_PAID_RUNS_LIMIT = 1000


# ===========================================================================
# Self-ensuring columns (migration-free) — mirrors mcp_monitor's DDL pattern.
# ===========================================================================

_ALTER_DDL = [
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS runs_used INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS runs_limit INTEGER",
    "ALTER TABLE clients ADD COLUMN IF NOT EXISTS access_tier VARCHAR(32) NOT NULL DEFAULT 'free'",
]

_ensured = False


async def _ensure_columns(db: AsyncSession) -> None:
    """Add the entitlement columns to `clients` if missing (idempotent)."""
    global _ensured
    if _ensured:
        return
    try:
        for ddl in _ALTER_DDL:
            await db.execute(text(ddl))
        await db.commit()
        _ensured = True
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] could not ensure client columns")


def _coerce_uuid(user_id) -> Optional[_uuid.UUID]:
    try:
        return _uuid.UUID(str(user_id))
    except Exception:
        return None


def _free_limit() -> int:
    """The default free-tier run limit from env (>= 0)."""
    try:
        return max(0, int(settings.WIRABLE_FREE_RUNS))
    except Exception:
        return 2


def _access_codes() -> set[str]:
    """BONUS codes (csv) — grant a limited number of runs, not unlimited."""
    raw = settings.WIRABLE_ACCESS_CODES or ""
    return {c.strip() for c in raw.split(",") if c.strip()}


def _unlimited_codes() -> set[str]:
    """JUDGE/internal codes (csv) — grant unlimited runs."""
    raw = getattr(settings, "WIRABLE_UNLIMITED_CODES", "") or ""
    return {c.strip() for c in raw.split(",") if c.strip()}


def _bonus_runs() -> int:
    try:
        return max(1, int(getattr(settings, "WIRABLE_BONUS_RUNS", 10) or 10))
    except Exception:
        return 10


# ===========================================================================
# Row load / ensure (creates the Client row if absent, like github.py does)
# ===========================================================================


async def _load_row(db: AsyncSession, user_id) -> Optional[dict]:
    """Load (or create) the user's entitlement state as a plain dict.

    Returns {tier, runs_used, runs_limit} or None if the user id is unusable.
    Defensive: if the columns are absent (ensure failed) we synthesize defaults
    from a bare clients lookup so callers still get a coherent status.
    """
    uid = _coerce_uuid(user_id)
    if uid is None:
        return None

    await _ensure_columns(db)

    # Ensure a Client row exists for this user (mirrors github._get_or_create_client).
    try:
        res = await db.execute(
            text("SELECT id FROM clients WHERE user_id = :uid LIMIT 1"),
            {"uid": str(uid)},
        )
        row_id = res.scalar_one_or_none()
        if row_id is None:
            await db.execute(
                text(
                    "INSERT INTO clients (id, user_id, fix_status, created_at) "
                    "VALUES (:id, :uid, 'pending', now())"
                ),
                {"id": str(_uuid.uuid4()), "uid": str(uid)},
            )
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] ensure client row failed for %s", uid)

    # Read the entitlement state. Tolerate missing columns by degrading.
    try:
        res = await db.execute(
            text(
                "SELECT COALESCE(runs_used, 0) AS runs_used, "
                "runs_limit AS runs_limit, "
                "COALESCE(access_tier, 'free') AS access_tier "
                "FROM clients WHERE user_id = :uid LIMIT 1"
            ),
            {"uid": str(uid)},
        )
        m = res.mappings().first()
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] read state failed for %s", uid)
        m = None

    if not m:
        return {"tier": "free", "runs_used": 0, "runs_limit": _free_limit()}

    tier = (m.get("access_tier") or "free").lower()
    runs_used = int(m.get("runs_used") or 0)
    runs_limit = m.get("runs_limit")
    if runs_limit is None:
        runs_limit = _PAID_RUNS_LIMIT if tier == "paid" else _free_limit()
    return {"tier": tier, "runs_used": runs_used, "runs_limit": int(runs_limit)}


def _status_dict(state: dict) -> dict:
    tier = state["tier"]
    runs_used = state["runs_used"]
    runs_limit = state["runs_limit"]
    unlimited = tier in _UNLIMITED_TIERS
    remaining = None if unlimited else max(0, runs_limit - runs_used)
    return {
        "tier": tier,
        "runs_used": runs_used,
        "runs_limit": runs_limit,
        "remaining": remaining,
        "unlimited": unlimited,
    }


# ===========================================================================
# Public API
# ===========================================================================


async def can_run(db: AsyncSession, user_id) -> Tuple[bool, str, dict]:
    """Whether `user_id` may start a run, plus a reason + status snapshot.

    unlimited/paid tiers always pass. free tier passes while runs_used <
    runs_limit, else fails with reason "run limit reached".
    """
    state = await _load_row(db, user_id)
    if state is None:
        return False, "invalid user", {}
    st = _status_dict(state)
    if st["unlimited"]:
        return True, "ok", st
    if state["runs_used"] >= state["runs_limit"]:
        return False, "run limit reached", st
    return True, "ok", st


async def record_run(db: AsyncSession, user_id) -> None:
    """Increment runs_used for `user_id` (no-op for invalid id). Defensive."""
    uid = _coerce_uuid(user_id)
    if uid is None:
        return
    await _ensure_columns(db)
    try:
        await db.execute(
            text(
                "UPDATE clients SET runs_used = COALESCE(runs_used, 0) + 1 "
                "WHERE user_id = :uid"
            ),
            {"uid": str(uid)},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] record_run failed for %s", uid)


async def redeem_code(db: AsyncSession, user_id, code: str) -> dict:
    """Redeem a judge/internal access code -> set tier=unlimited.

    Raises HTTPException(400) on an unknown/blank code. Returns the new status.
    """
    from fastapi import HTTPException

    code = (code or "").strip()
    unlimited = _unlimited_codes()
    bonus = _access_codes()
    if not code or (code not in unlimited and code not in bonus):
        raise HTTPException(status_code=400, detail="invalid access code")

    uid = _coerce_uuid(user_id)
    if uid is None:
        raise HTTPException(status_code=400, detail="invalid user id")

    # Make sure the row + columns exist before we update.
    await _load_row(db, uid)
    try:
        if code in unlimited:
            # Judge / internal: uncapped.
            await db.execute(
                text("UPDATE clients SET access_tier = 'unlimited' WHERE user_id = :uid"),
                {"uid": str(uid)},
            )
        else:
            # Bonus code (e.g. Product Hunt): grant a LIMITED allowance, not unlimited.
            await db.execute(
                text(
                    "UPDATE clients SET access_tier = 'granted', "
                    "runs_limit = COALESCE(runs_used, 0) + :n WHERE user_id = :uid"
                ),
                {"uid": str(uid), "n": _bonus_runs()},
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] redeem_code update failed for %s", uid)
        raise HTTPException(status_code=500, detail="could not redeem code")

    state = await _load_row(db, uid)
    return _status_dict(state)


async def grant_paid(db: AsyncSession, user_id) -> dict:
    """Mark `user_id` as a paying user: tier=paid + a high run limit.

    Called by the Stripe webhook after a successful checkout. Defensive — never
    raises (the webhook must return fast/200).
    """
    uid = _coerce_uuid(user_id)
    if uid is None:
        return {}
    await _load_row(db, uid)  # ensure row + columns
    try:
        await db.execute(
            text(
                "UPDATE clients SET access_tier = 'paid', runs_limit = :lim "
                "WHERE user_id = :uid"
            ),
            {"uid": str(uid), "lim": _PAID_RUNS_LIMIT},
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("[entitlements] grant_paid failed for %s", uid)
        return {}
    state = await _load_row(db, uid)
    return _status_dict(state)


async def status(db: AsyncSession, user_id) -> dict:
    """Current entitlement status for `user_id`."""
    state = await _load_row(db, user_id)
    if state is None:
        return {"tier": "free", "runs_used": 0, "runs_limit": _free_limit(),
                "remaining": _free_limit(), "unlimited": False}
    return _status_dict(state)


async def is_pro(db: AsyncSession, user_id) -> bool:
    """True if the user is on a paid/unlimited tier. The FIX (hosted proxy +
    GitHub PR + monitoring) is gated on this; the AUDIT stays free (the funnel)."""
    state = await _load_row(db, user_id)
    return bool(state) and state.get("tier") in _UNLIMITED_TIERS
