"""add crop cycles

Revision ID: ff21853418b8
Revises: 7f9c1b3cae7f
Create Date: 2026-09-23 22:10:34.015547

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ff21853418b8'
down_revision: Union[str, Sequence[str], None] = '7f9c1b3cae7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "crop_cycle",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=False),
        sa.Column("crop_id", sa.Integer(), sa.ForeignKey("crop.id"), nullable=False),
        sa.Column("sown_on", sa.Date(), nullable=False),
        sa.Column("expected_harvest_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.CheckConstraint(
            "status in ('active','harvested','lost')", name="ck_crop_cycle_status"
        ),
    )
    op.create_index(
        "uq_crop_cycle_active_per_plot",
        "crop_cycle",
        ["plot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_crop_cycle_active_per_plot", table_name="crop_cycle")
    op.drop_table("crop_cycle")
