"""initial care workflow schema

Revision ID: 001_initial_care_schema
Revises:
Create Date: 2026-11-23 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "001_initial_care_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "care_cases",
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("household_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("urgency", sa.String(length=16), nullable=False),
        sa.Column("recipient_authorized", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("date_of_birth_on_file", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verified_documents", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("missing_fields", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("safe_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index("ix_care_cases_household_id", "care_cases", ["household_id"], unique=False)

    op.create_table(
        "benefit_applications",
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=False),
        sa.Column("program_code", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("human_review_approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["care_cases.case_id"]),
        sa.PrimaryKeyConstraint("application_id"),
    )
    op.create_index("ix_benefit_applications_case_id", "benefit_applications", ["case_id"], unique=False)

    op.create_table(
        "transition_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "entity_id", "occurred_at", "from_status", "to_status", name="uq_transition_audit_event"),
    )
    op.create_index("ix_transition_audit_logs_entity_id", "transition_audit_logs", ["entity_id"], unique=False)

    op.create_table(
        "dispatch_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["benefit_applications.application_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dispatch_attempts_application_id", "dispatch_attempts", ["application_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_dispatch_attempts_application_id", table_name="dispatch_attempts")
    op.drop_table("dispatch_attempts")
    op.drop_index("ix_transition_audit_logs_entity_id", table_name="transition_audit_logs")
    op.drop_table("transition_audit_logs")
    op.drop_index("ix_benefit_applications_case_id", table_name="benefit_applications")
    op.drop_table("benefit_applications")
    op.drop_index("ix_care_cases_household_id", table_name="care_cases")
    op.drop_table("care_cases")
