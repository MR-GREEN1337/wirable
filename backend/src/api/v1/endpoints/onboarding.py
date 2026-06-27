"""
Onboarding endpoints — the bridge that links a signed-in user's Client row to
the Company they audited anonymously.

Flow: anonymous audit creates a Company by domain → user signs in → claims that
company by domain (creating the Client↔Company link) → selects a repo. This is
the severed link that connects the public audit funnel to the authenticated
fix/verify/outbound loop.
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.auth import get_current_user
from ....core.database import get_session
from ....models.client import Client
from ....models.company import Company
from ....models.audit import Audit

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _normalise_domain(raw: str) -> str:
    """Strip scheme + path, lowercase. Mirrors audit endpoint normalisation."""
    return (
        raw.lower()
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .split("/")[0]
        .split("?")[0]
    )


class ClaimRequest(BaseModel):
    domain: str
    founder_name: str | None = None
    founder_email: str | None = None


@router.post("/claim")
async def claim_company(
    body: ClaimRequest,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Claim the audited company by domain and link it to this user's Client.

    Finds-or-creates the Company (by normalised domain) and the Client (by
    user_id), then sets client.company_id. founder_name/email, when supplied,
    are written onto the Company (this also unblocks the outbound demo).
    """
    domain = _normalise_domain(body.domain)
    user_id = uuid.UUID(user["sub"])

    # Find or create the company.
    co_res = await db.execute(select(Company).where(Company.domain == domain))
    company = co_res.scalar_one_or_none()
    if not company:
        company = Company(id=uuid.uuid4(), domain=domain)
        db.add(company)
        await db.flush()  # assign PK before linking

    if body.founder_name:
        company.founder_name = body.founder_name
    if body.founder_email:
        company.founder_email = body.founder_email

    # Find or create the client for this user.
    cl_res = await db.execute(select(Client).where(Client.user_id == user_id))
    client = cl_res.scalar_one_or_none()
    if not client:
        client = Client(id=uuid.uuid4(), user_id=user_id)
        db.add(client)

    client.company_id = company.id
    await db.commit()
    await db.refresh(company)

    # Does this company already have a completed audit?
    audit_res = await db.execute(
        select(Audit)
        .where(Audit.company_id == company.id, Audit.score.isnot(None))
        .order_by(Audit.created_at.desc())
    )
    has_audit = audit_res.scalars().first() is not None

    return {
        "company_id": str(company.id),
        "domain": company.domain,
        "has_audit": has_audit,
    }


class SelectRepoRequest(BaseModel):
    repo: str  # "owner/repo"


@router.post("/select-repo")
async def select_repo(
    body: SelectRepoRequest,
    db: AsyncSession = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Persist the chosen GitHub repo on this user's Client row."""
    user_id = uuid.UUID(user["sub"])
    cl_res = await db.execute(select(Client).where(Client.user_id == user_id))
    client = cl_res.scalar_one_or_none()
    if not client:
        client = Client(id=uuid.uuid4(), user_id=user_id)
        db.add(client)
    client.github_repo = body.repo
    await db.commit()
    return {"ok": True}
