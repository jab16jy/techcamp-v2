"""add weather cell and daily tables

`weather_cell` is the 0.1° grid a plot is assigned to, and the cache that
keeps nearby plots to a single Open-Meteo call
(docs/00-glosario.md:40, docs/06-diseno-detallado.md §6). `weather_daily`
holds the daily rows that call returns (docs/03-modelo-datos.md:176-191).
Neither table carries `org_id`: a cell is shared reference data, the same row
for every organization (docs/09-cuellos-de-botella.md:39).

`plot.weather_cell_id` has existed since E3 with no constraint; E5 owns the
referenced table, so E5 adds the foreign key.

Revision ID: c3d9f0a1b2e4
Revises: a3f1c7d92b40
Create Date: 2026-09-26 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3d9f0a1b2e4"
down_revision: Union[str, Sequence[str], None] = "a3f1c7d92b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "weather_cell",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lat", sa.Numeric(), nullable=False),
        sa.Column("lon", sa.Numeric(), nullable=False),
        # The rounded grid coordinates identify a cell, so the unique index is
        # what makes `get_or_create_cell` idempotent under concurrency.
        sa.UniqueConstraint("lat", "lon", name="uq_weather_cell_lat_lon"),
    )
    op.create_table(
        "weather_daily",
        sa.Column("cell_id", sa.Integer(), sa.ForeignKey("weather_cell.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("is_forecast", sa.Boolean(), primary_key=True),
        # Nullable: Open-Meteo reports `null` for a day it has no value for.
        sa.Column("et0_mm", sa.Numeric(), nullable=True),
        sa.Column("rain_mm", sa.Numeric(), nullable=True),
        sa.Column("tmin_c", sa.Numeric(), nullable=True),
        sa.Column("tmax_c", sa.Numeric(), nullable=True),
        sa.Column("rh_mean_pct", sa.Numeric(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_plot_weather_cell_id",
        "plot",
        "weather_cell",
        ["weather_cell_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_plot_weather_cell_id", "plot", type_="foreignkey")
    op.drop_table("weather_daily")
    op.drop_table("weather_cell")
