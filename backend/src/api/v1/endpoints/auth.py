"""
Auth endpoints — Google OAuth callback, guest access, and token verify.
"""
import random
import uuid as _uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ....core.database import get_session
from ....models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Guest name corpus ─────────────────────────────────────────────────────────
_ADJECTIVES = [
    "cosmic", "silent", "neon", "blazing", "velvet", "phantom", "midnight",
    "steel", "frozen", "crimson", "electric", "hollow", "obsidian", "spectral",
    "gilded", "quantum", "verdant", "ashen", "amber", "scarlet", "onyx",
    "azure", "silver", "brutal", "ancient", "feral", "solemn", "radiant",
    "cryptic", "molten",
]
_NOUNS = [
    "badger", "falcon", "nebula", "glacier", "corvus", "panther", "vortex",
    "titan", "specter", "lynx", "raven", "cipher", "wraith", "condor",
    "vector", "prism", "harrier", "mantis", "jackal", "herald", "drifter",
    "signal", "axiom", "current", "eclipse", "haven", "vertex", "relay",
    "nomad", "flare",
]


def _random_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"


# ── Schemas ───────────────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    email: str
    name: str
    google_id: str

class GuestNameRequest(BaseModel):
    name: str | None = None  # client can suggest; we generate if absent


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/google")
async def google_auth(
    body: GoogleAuthRequest,
    db: AsyncSession = Depends(get_session),
):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=_uuid.uuid4(), email=body.email, name=body.name)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_access_token(
        {"sub": str(user.id), "email": user.email, "name": user.name or "", "guest": False},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "name": user.name}}


@router.get("/guest-name")
async def guest_name_suggestion():
    """Return a random cool guest name — call this on page load to pre-populate the button."""
    return {"name": _random_name()}


@router.post("/guest")
async def guest_auth(
    body: GuestNameRequest,
    db: AsyncSession = Depends(get_session),
):
    """
    Create an ephemeral guest account.
    The name is shown in the UI (e.g. 'Enter as Cosmic Badger →').
    """
    name = body.name or _random_name()
    uid = _uuid.uuid4()
    email = f"guest-{uid.hex[:10]}@guest.wirable"
    user = User(id=uid, email=email, name=name)
    db.add(user)
    await db.commit()

    token = create_access_token(
        {"sub": str(uid), "email": email, "name": name, "guest": True},
        expires_delta=timedelta(minutes=60 * 24 * 7),  # 7-day guest token
    )
    return {"access_token": token, "token_type": "bearer",
            "user": {"id": str(uid), "name": name, "guest": True}}
