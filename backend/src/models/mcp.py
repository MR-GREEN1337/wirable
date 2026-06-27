import uuid
from datetime import datetime
from typing import Optional, Any

from sqlalchemy import String, Integer, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class MCP(Base):
    __tablename__ = "mcps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    audit_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("audits.id", ondelete="SET NULL"), nullable=True
    )
    daytona_job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    server_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schemas_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    llms_txt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evals_json: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    pr_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    pr_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pr_status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    projected_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    verified_dims: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)    # list of dim names
    unverified_dims: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)  # list of dim names
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    client: Mapped["Client"] = relationship("Client", back_populates="mcps")
    audit: Mapped[Optional["Audit"]] = relationship("Audit")
