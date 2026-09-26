"""backfill plot weather cells

`plot.weather_cell_id` has existed since E3 and every stored plot has it null,
so the plots already in the database never got the 0.1° cell the weather
module groups them by (docs/06-diseno-detallado.md §6, docs/00-glosario.md:40).
The write path assigns the cell from now on; this assigns it to the ones that
predate E5.

The representative point is `ST_Centroid(boundary)`, the same point
`PlotRepository.get_centroid` treats as the plot's location, and the grid rule
is `round(..., 1)`: Postgres rounds `numeric` half away from zero, the same tie
rule as the domain's `cell_for` (weather/domain/models.py, T1b), so a backfilled
plot and a plot created later share one cell row and not two.

Revision ID: b7e2c9a41d38
Revises: c3d9f0a1b2e4
Create Date: 2026-09-26 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c9a41d38"
down_revision: Union[str, Sequence[str], None] = "c3d9f0a1b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BACKFILL_STATEMENTS: tuple[str, ...] = (
    # One row per distinct 0.1° square the stored plots fall into. `DISTINCT`
    # collapses the plots of one square inside the statement, `ON CONFLICT` the
    # squares a write already created.
    """
    INSERT INTO weather_cell (lat, lon)
    SELECT DISTINCT
        round(ST_Y(ST_Centroid(boundary))::numeric, 1),
        round(ST_X(ST_Centroid(boundary))::numeric, 1)
    FROM plot
    WHERE weather_cell_id IS NULL
    ON CONFLICT (lat, lon) DO NOTHING
    """,
    # Only the plots still unassigned, so a replay changes nothing and a plot
    # that was moved to another cell since keeps the cell it was given.
    """
    UPDATE plot AS p
    SET weather_cell_id = c.id
    FROM weather_cell AS c
    WHERE p.weather_cell_id IS NULL
      AND c.lat = round(ST_Y(ST_Centroid(p.boundary))::numeric, 1)
      AND c.lon = round(ST_X(ST_Centroid(p.boundary))::numeric, 1)
    """,
)
"""The statements `upgrade` runs, exposed so a test can replay them against the
migrated schema (the replay-safety test the telemetry migration has,
tests/telemetry/test_schema.py)."""


def upgrade() -> None:
    """Upgrade schema."""
    for statement in BACKFILL_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema.

    Deliberately empty: the assignment is derived from `boundary`, which the
    downgrade does not touch, and `plot.weather_cell_id` is nullable, so
    leaving it costs nothing and re-running the upgrade is idempotent. Clearing
    it instead would also erase the cells the write path assigned after this
    migration ran, which no earlier revision ever had a reason to remove.
    """
