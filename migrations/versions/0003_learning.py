"""learning

Revision ID: 0003_learning
Revises: 0002_grounding
Create Date: 2026-11-01 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_learning"
down_revision: str | None = "0002_grounding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add short_waste_slider to event
    op.add_column(
        "event",
        sa.Column(
            "short_waste_slider",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "event_short_waste_slider_check",
        "event",
        "short_waste_slider >= 1 AND short_waste_slider <= 10",
    )

    # 2. turnout_observation
    op.create_table(
        "turnout_observation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("function_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("function_kind", sa.Text(), nullable=False),
        sa.Column("reply_bucket", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("invited_households", sa.Integer(), nullable=False),
        sa.Column("arrived_households", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("weight", sa.Double(), server_default=sa.text("1.0"), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "turnout_obs_segment_idx",
        "turnout_observation",
        ["state", "event_type", "function_kind", "reply_bucket"],
    )

    # 3. turnout_model
    op.create_table(
        "turnout_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("segment_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("a", sa.Double(), nullable=False),
        sa.Column("b", sa.Double(), nullable=False),
        sa.Column("strength", sa.Double(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_key", "status", name="uq_turnout_model_segment_status"),
    )

    # 4. portion_model
    op.create_table(
        "portion_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("serving_kind", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("median", sa.Double(), nullable=False),
        sa.Column("p10", sa.Double(), nullable=False),
        sa.Column("p90", sa.Double(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serving_kind", "region", name="uq_portion_model_kind_region"),
    )

    # 5. elasticity_model
    op.create_table(
        "elasticity_model",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("course", sa.Text(), nullable=False),
        sa.Column("mean", sa.Double(), nullable=False),
        sa.Column("sd", sa.Double(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course"),
    )

    # 6. channel_stat
    op.create_table(
        "channel_stat",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("context_key", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("successes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("context_key", "channel", name="uq_channel_stat_context_channel"),
    )

    # 7. channel_health
    op.create_table(
        "channel_health",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "last_checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel"),
    )

    # 8. preference_comparison
    op.create_table(
        "preference_comparison",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_key", sa.CHAR(length=5), nullable=True),
        sa.Column("winner", sa.Text(), nullable=False),
        sa.Column("loser", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 9. function_pattern
    op.create_table(
        "function_pattern",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("traditions", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("structure", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("accepted_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # 10. event_rule
    op.create_table(
        "event_rule",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("event_key", sa.CHAR(length=5), nullable=False),
        sa.Column("function_id", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("forbid", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "confirm",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["event_key"], ["event.event_key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "event_rule_event_fn_idx",
        "event_rule",
        ["event_key", "function_id"],
    )

    # 11. ui_translation
    op.create_table(
        "ui_translation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("lang", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("agreement", sa.Double(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lang", "key", name="uq_ui_translation_lang_key"),
    )

    # 12. tuning_value
    op.create_table(
        "tuning_value",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("n", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # 13. osm_cache
    op.create_table(
        "osm_cache",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )


def downgrade() -> None:
    op.drop_table("osm_cache")
    op.drop_table("tuning_value")
    op.drop_table("ui_translation")
    op.drop_index("event_rule_event_fn_idx", table_name="event_rule")
    op.drop_table("event_rule")
    op.drop_table("function_pattern")
    op.drop_table("preference_comparison")
    op.drop_table("channel_health")
    op.drop_table("channel_stat")
    op.drop_table("elasticity_model")
    op.drop_table("portion_model")
    op.drop_table("turnout_model")
    op.drop_index("turnout_obs_segment_idx", table_name="turnout_observation")
    op.drop_table("turnout_observation")
    op.drop_constraint("event_short_waste_slider_check", "event", type_="check")
    op.drop_column("event", "short_waste_slider")
