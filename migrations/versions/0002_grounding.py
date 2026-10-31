"""grounding

Revision ID: 0002_grounding
Revises: 0001_init
Create Date: 2026-10-31 21:50:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_grounding"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. knowledge_concept
    op.create_table(
        "knowledge_concept",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "tags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "parents", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. knowledge_form
    op.create_table(
        "knowledge_form",
        sa.Column("form_norm", sa.Text(), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("concept_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["concept_id"], ["knowledge_concept.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("form_norm", "concept_id"),
    )

    # 3. knowledge_fact
    op.create_table(
        "knowledge_fact",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("subject_norm", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("host_uid", sa.Text(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "knowledge_fact_subject_idx", "knowledge_fact", ["subject_norm", "kind"], unique=False
    )

    # 4. serving_kind
    op.create_table(
        "serving_kind",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("course", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("pack_sizes", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("per_guests", sa.Integer(), nullable=True),
        sa.Column("meal_factor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=False),
        sa.CheckConstraint("unit IN ('g','ml','piece')", name="serving_kind_unit_check"),
        sa.CheckConstraint(
            "created_by IN ('model','host','system')", name="serving_kind_created_by_check"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 5. portion_prior
    op.create_table(
        "portion_prior",
        sa.Column("serving_kind", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("per_adult", sa.Double(), nullable=False),
        sa.Column("per_child", sa.Double(), nullable=False),
        sa.Column("weight", sa.Double(), server_default=sa.text("5"), nullable=False),
        sa.Column("citations", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["serving_kind"], ["serving_kind.id"]),
        sa.PrimaryKeyConstraint("serving_kind", "region"),
    )

    # 6. portion_outcome
    op.create_table(
        "portion_outcome",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("function_id", sa.Text(), nullable=False),
        sa.Column("serving_kind", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("planned_per_head", sa.Double(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("leftover_fraction", sa.Double(), server_default=sa.text("0"), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ran_short','about_right','leftover')",
            name="portion_outcome_outcome_check",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 7. price_observation
    op.create_table(
        "price_observation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("item_norm", sa.Text(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("inr_per_unit", sa.Double(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.CheckConstraint("inr_per_unit >= 0", name="price_observation_inr_nonneg"),
        sa.CheckConstraint(
            "source IN ('host_reported','caterer_quote','live')",
            name="price_observation_source_check",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "price_observation_idx",
        "price_observation",
        ["item_norm", "region", sa.text("observed_on DESC")],
        unique=False,
    )

    # 8. grounding_policy
    op.create_table(
        "grounding_policy",
        sa.Column("id", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("quorum", sa.SmallInteger(), nullable=False),
        sa.Column("min_agreement", sa.Double(), nullable=False),
        sa.Column("alpha", sa.Double(), nullable=False),
        sa.Column("n_calibration", sa.Integer(), nullable=False),
        sa.Column("risk_bound", sa.Double(), nullable=False),
        sa.Column("calibrated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="grounding_policy_singleton_check"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("grounding_policy")
    op.drop_index("price_observation_idx", table_name="price_observation")
    op.drop_table("price_observation")
    op.drop_table("portion_outcome")
    op.drop_table("portion_prior")
    op.drop_table("serving_kind")
    op.drop_index("knowledge_fact_subject_idx", table_name="knowledge_fact")
    op.drop_table("knowledge_fact")
    op.drop_table("knowledge_form")
    op.drop_table("knowledge_concept")
