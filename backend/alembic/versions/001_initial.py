"""initial schema — all tables

Revision ID: 001
Revises:
Create Date: 2026-06-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # --- companies ---
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("founder_name", sa.String(255), nullable=True),
        sa.Column("founder_email", sa.String(255), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("outbound_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("last_audited_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_companies_domain", "companies", ["domain"], unique=True)

    # --- audits ---
    op.create_table(
        "audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("n_agents", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("report_url", sa.String(2048), nullable=True),
        sa.Column("is_post_fix", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_audits_company_id", "audits", ["company_id"])

    # --- audit_steps ---
    op.create_table(
        "audit_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("agent_votes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_audit_steps_audit_id", "audit_steps", ["audit_id"])

    # --- clients ---
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("github_token", sa.String(512), nullable=True),
        sa.Column("github_repo", sa.String(512), nullable=True),
        sa.Column("fix_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_clients_company_id", "clients", ["company_id"])
    op.create_index("ix_clients_user_id", "clients", ["user_id"])

    # --- mcps ---
    op.create_table(
        "mcps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audits.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("daytona_job_id", sa.String(255), nullable=True),
        sa.Column("server_code", sa.Text(), nullable=True),
        sa.Column("schemas_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("llms_txt", sa.Text(), nullable=True),
        sa.Column("evals_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pr_url", sa.String(2048), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("projected_score", sa.Integer(), nullable=True),
        sa.Column("verified_dims", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("unverified_dims", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_mcps_client_id", "mcps", ["client_id"])

    # --- outbound_emails ---
    op.create_table(
        "outbound_emails",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(512), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("clicked_at", sa.DateTime(), nullable=True),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.Column("report_url", sa.String(2048), nullable=True),
        sa.Column("token", sa.String(128), nullable=False, unique=True),
    )
    op.create_index("ix_outbound_emails_company_id", "outbound_emails", ["company_id"])
    op.create_index("ix_outbound_emails_token", "outbound_emails", ["token"], unique=True)


def downgrade() -> None:
    op.drop_table("outbound_emails")
    op.drop_table("mcps")
    op.drop_table("clients")
    op.drop_table("audit_steps")
    op.drop_table("audits")
    op.drop_table("companies")
    op.drop_table("users")
