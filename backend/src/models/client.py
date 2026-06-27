import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # GitHub token stored as-is (plaintext for now, encrypt at rest in prod)
    github_token: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    github_repo: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    fix_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # pending|analyzing|generating|pr_open|verified|done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    company: Mapped[Optional["Company"]] = relationship("Company", back_populates="clients")
    user: Mapped[Optional["User"]] = relationship("User")
    mcps: Mapped[list["MCP"]] = relationship("MCP", back_populates="client", cascade="all, delete-orphan")
