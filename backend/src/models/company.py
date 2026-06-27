import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    founder_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    founder_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    outbound_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # discovered|auditing|audited|enriching|contacted|replied|client
    # --- Autonomous scout / discovery metadata --------------------------------
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # "scout"|"inbound"
    discovery_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    founder_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enrichment_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_audited_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    # Relationships
    audits: Mapped[list["Audit"]] = relationship("Audit", back_populates="company", lazy="select")
    clients: Mapped[list["Client"]] = relationship("Client", back_populates="company", lazy="select")
    outbound_emails: Mapped[list["OutboundEmail"]] = relationship(
        "OutboundEmail", back_populates="company", lazy="select"
    )
