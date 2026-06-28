import uuid
from datetime import datetime
from typing import Optional, Any

from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Audit(Base):
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Who ran this test. Nullable for pre-existing rows + anonymous runs. Added
    # migration-free via ALTER ... ADD COLUMN IF NOT EXISTS (see run.py). The
    # dashboard lists a user's targets by this column.
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    n_agents: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    report_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    is_post_fix: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    company: Mapped["Company"] = relationship("Company", back_populates="audits")
    steps: Mapped[list["AuditStep"]] = relationship(
        "AuditStep", back_populates="audit", cascade="all, delete-orphan", lazy="select"
    )


class AuditStep(Base):
    __tablename__ = "audit_steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    evidence: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    agent_votes: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)

    # Relationships
    audit: Mapped["Audit"] = relationship("Audit", back_populates="steps")
