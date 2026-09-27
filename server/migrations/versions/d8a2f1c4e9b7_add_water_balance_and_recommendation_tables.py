"""add water balance and recommendation tables

Tables for E6 daily water balance and irrigation recommendations
(docs/03-modelo-datos.md:192-215). `water_balance_daily` holds one row per plot
and day with root zone depletion before and after sensor assimilation (ADR-0022).
`irrigation_recommendation` holds the daily decision outcome per plot: irrigate
(depth and duration), postpone, not_needed, no_kc, or rainfed with advice codes
(docs/06-diseno-detallado.md §5, ADR-0023).

Revision ID: d8a2f1c4e9b7
Revises: b7e2c9a41d38
Create Date: 2026-09-26 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d8a2f1c4e9b7"
down_revision: Union[str, Sequence[str], None] = "b7e2c9a41d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "water_balance_daily",
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("etc_mm", sa.Numeric(), nullable=False),
        sa.Column("effective_rain_mm", sa.Numeric(), nullable=False),
        sa.Column("irrigation_mm", sa.Numeric(), nullable=False),
        sa.Column("taw_mm", sa.Numeric(), nullable=False),
        sa.Column("raw_mm", sa.Numeric(), nullable=False),
        sa.Column("depletion_model_mm", sa.Numeric(), nullable=False),
        sa.Column("depletion_mm", sa.Numeric(), nullable=False),
        sa.Column("soil_moisture_obs_pct", sa.Numeric(), nullable=True),
        sa.Column("assimilation_k", sa.Numeric(), nullable=False),
        sa.Column("stress_moisture_pct", sa.Numeric(), nullable=False),
    )

    op.create_table(
        "irrigation_recommendation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("depth_mm", sa.Numeric(), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("advice", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("plot_id", "day", name="uq_irrigation_recommendation_plot_day"),
        sa.CheckConstraint(
            "kind in ('irrigate','postpone','not_needed','no_kc','rainfed')",
            name="ck_irrigation_recommendation_kind",
        ),
        sa.CheckConstraint(
            "kind = 'irrigate' or (depth_mm is null and duration_min is null)",
            name="ck_irrigation_recommendation_depth_null_unless_irrigate",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("irrigation_recommendation")
    op.drop_table("water_balance_daily")
