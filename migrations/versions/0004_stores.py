"""stores

Revision ID: 0004_stores
Revises: 0003_learning
Create Date: 2026-11-07 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_stores"
down_revision: str | None = "0003_learning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_connection",
        sa.Column("host_uid", sa.Text(), nullable=False),
        sa.Column("store", sa.Text(), nullable=False),
        sa.Column("token_enc", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "connected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["host_uid"], ["host.uid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("host_uid", "store"),
    )

    op.create_table(
        "delivery_report",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("item_kind", sa.Text(), nullable=False),
        sa.Column("promised_max", sa.Double(), nullable=False),
        sa.Column("actual_minutes", sa.Double(), nullable=False),
        sa.Column(
            "reported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "delivery_report_chan_reg_idx",
        "delivery_report",
        ["channel", "region"],
    )


def downgrade() -> None:
    op.drop_index("delivery_report_chan_reg_idx", table_name="delivery_report")
    op.drop_table("delivery_report")
    op.drop_table("store_connection")
