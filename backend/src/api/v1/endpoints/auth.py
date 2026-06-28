"""
Auth endpoints — Google OAuth, email/password signup+login, guest access.
"""
import hashlib
import logging
import random
import re
import uuid as _uuid
from datetime import timedelta

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ....core.ratelimit import rate_limit
from ....core.database import get_session
from ....models.user import User
from ....services import email as email_service
from ....services import turnstile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# bcrypt password hashing. We call the `bcrypt` lib directly (installed via the
# passlib[bcrypt] extra) because passlib 1.7's backend-version detection is
# broken against bcrypt >= 4. bcrypt has a hard 72-byte input limit, so we
# pre-hash overlong passwords with sha256 before bcrypt (standard mitigation).
def _pw_input(password: str) -> bytes:
    raw = (password or "").encode("utf-8")
    if len(raw) > 72:
        raw = hashlib.sha256(raw).hexdigest().encode("utf-8")  # 64 bytes, < 72
    return raw


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_input(password), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_input(password), password_hash.encode("utf-8"))
    except Exception:
        return False


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Self-ensuring column (migration-free) — mirrors entitlements._ensure_columns.
_PASSWORD_DDL = "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR"
_pw_col_ensured = False


async def _ensure_password_column(db: AsyncSession) -> None:
    """Add users.password_hash if missing (idempotent, run once)."""
    global _pw_col_ensured
    if _pw_col_ensured:
        return
    try:
        await db.execute(text(_PASSWORD_DDL))
        await db.commit()
        _pw_col_ensured = True
    except Exception:
        await db.rollback()
        logger.exception("[auth] could not ensure users.password_hash column")

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

class SignupRequest(BaseModel):
    email: str
    password: str
    turnstile_token: str

class LoginRequest(BaseModel):
    email: str
    password: str


def _token_response(user: User) -> dict:
    """Build the standard {access_token, token_type, user} payload.

    Claim shape is IDENTICAL to /google: sub=user.id, email, name, guest:false.
    """
    token = create_access_token(
        {"sub": str(user.id), "email": user.email, "name": user.name or "", "guest": False},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "name": user.name}}


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


@router.post("/signup", dependencies=[rate_limit("signup", 10, 3600)])
async def signup(
    body: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Create an email/password account (free tier). Gated by Turnstile."""
    # 1. Bot check (Turnstile). Empty secret = dev bypass (returns True).
    ok = await turnstile.verify(
        body.turnstile_token,
        remoteip=request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None,
    )
    if not ok:
        raise HTTPException(status_code=400, detail="bot check failed")

    # 2. Validate input.
    email = (body.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="invalid email")
    if len(body.password or "") < 8:
        raise HTTPException(status_code=422, detail="password must be at least 8 characters")

    # 3. Ensure the password column exists (migration-free), then dedupe.
    await _ensure_password_column(db)

    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="account exists, please sign in")

    # 4. Create the user. New users default to free tier via entitlements.
    name = email.split("@", 1)[0]
    user = User(
        id=_uuid.uuid4(),
        email=email,
        name=name,
        password_hash=_hash_password(body.password),
    )
    db.add(user)
    try:
        await db.commit()
        await db.refresh(user)
    except Exception:
        # Race: another request inserted the same email between check + commit.
        await db.rollback()
        raise HTTPException(status_code=409, detail="account exists, please sign in")

    # 5. Fire-and-forget welcome email (never blocks/breaks signup).
    try:
        await email_service.send_welcome(user.email, name=user.name)
    except Exception:
        logger.exception("[auth] welcome email failed (non-fatal)")

    return _token_response(user)


@router.post("/login", dependencies=[rate_limit("login", 20, 3600)])
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_session),
):
    """Email/password login. Generic 401 on any failure (no user enumeration)."""
    await _ensure_password_column(db)

    email = (body.email or "").strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not _verify_password(body.password or "", user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    return _token_response(user)


@router.get("/guest-name")
async def guest_name_suggestion():
    """Return a random cool guest name — call this on page load to pre-populate the button."""
    return {"name": _random_name()}


@router.post("/guest", dependencies=[rate_limit("guest", 20, 3600)])
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
