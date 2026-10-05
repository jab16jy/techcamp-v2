"""add the org adoption read views

Creates the two read-only SQL views behind the two organization figures that D-T7.1
had to ship as `null`: `harvested_cycles_ratio` and
`median_hours_to_first_reading` (docs/11-metricas.md:69-75; D-T7.2). They are
`metrics_*` views like every other one the module reads other modules' data
through, the documented exception to "no joins between foreign tables"
(docs/05-arquitectura.md §Reglas), and nothing writes to them.

Both are organization-scoped, not plot-scoped: the figures are about the whole
organization's month, so each one row-per-… grain is the thing docs/11:75 names
as the population ("la mediana del tiempo a primera lectura toma los nodos
reclamados en ese mes") and the node — not the plot — is the unit of the median.

**Which month a cycle belongs to** (D-T7.2). `crop_cycle` carries no end date —
only `sown_on`, `expected_harvest_on` and `status` (docs/03-modelo-datos.md:128-135)
— so no query can ask "which cycles closed in March". The only date in the model
that can anchor a cycle to a month is the logbook entry that registers its close,
read by its own `occurred_on`: the date the producer wrote, never the arrival of
the offline sync. So `metrics_org_month_cycles` counts a cycle into the month of
the entry that closed it: a `harvest` entry closes a harvested cycle, and an
`observation` carrying an `alert_id` closes a lost one (that observation *is* the
loss record, docs/03-modelo-datos.md:424, the same evidence
`metrics_crop_cycle_totals` reads as `loss_kg`).

That view holds `status IN ('harvested', 'lost')` only. Two absences fall out of
the rule and are missing evidence, never a zero (docs/03-modelo-datos.md:441):
a `lost` cycle with no loss observation belongs to no month, and a cycle closed
by `PATCH /cycles/{id}` with no logbook entry at all is invisible to the figure.
An `active` cycle is excluded on both sides even when it carries a harvest
record, because the denominator docs/11-metricas.md:72 names is "ciclos
**terminados**": the ratio is a fraction of closed *and registered* cycles.

`occurred_on` is a bare `date`, already a local calendar date, so its month needs
no zone shift. `claimed_at`, by contrast, is an instant, and its month is built
with `AT TIME ZONE 'America/Bogota'` like every other view here, so a session
running under UTC cannot move a claim by five hours (D-T0.7).

"Primera lectura válida" is the validity rule docs/04-api.md:83 already documents
and `telemetry`'s `query_valid_raw` already implements — calibrated (`value IS
NOT NULL`, which is exactly what "no calibration was valid at that instant"
leaves null, docs/03-modelo-datos.md:461) and without the out-of-range bit 2 of
`quality`. It is not restated per module: the same predicate decides a reading the
dashboard shows and the one that ends the enrollment clock.

A node's first valid reading is taken **from its claim onwards**: a reading
timestamped before `claimed_at` was not received after the node was enrolled, so
it cannot show that the claimed node answered, and counting it would report a
negative latency. A node whose sensors have no valid reading at all still gets a
row, with a null first reading, so it can contribute no value without being
silently dropped from the population.

Revision ID: d2c8f4a1b3e7
Revises: c1fb5c6dcdd2
Create Date: 2026-10-05 16:20:41.118904

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd2c8f4a1b3e7'
down_revision: Union[str, Sequence[str], None] = 'c1fb5c6dcdd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORG_MONTH_CYCLES_VIEW = """
CREATE VIEW metrics_org_month_cycles AS
SELECT
    p.org_id,
    date_trunc('month', e.occurred_on)::date AS month,
    c.id AS crop_cycle_id,
    c.plot_id,
    MIN(e.occurred_on) AS closed_on,
    bool_or(e.kind = 'harvest') AS has_harvest
FROM crop_cycle c
JOIN plot p ON p.id = c.plot_id
JOIN logbook_entry e
    ON e.crop_cycle_id = c.id
   AND e.deleted_at IS NULL
   AND (
       e.kind = 'harvest'
       OR (e.kind = 'observation' AND e.alert_id IS NOT NULL)
   )
WHERE c.status IN ('harvested', 'lost')
GROUP BY p.org_id, date_trunc('month', e.occurred_on)::date, c.id, c.plot_id
"""


ORG_NODE_FIRST_READING_VIEW = """
CREATE VIEW metrics_org_node_first_reading AS
SELECT
    p.org_id,
    n.plot_id,
    n.id AS node_id,
    date_trunc('month', n.claimed_at AT TIME ZONE 'America/Bogota')::date AS month,
    n.claimed_at,
    fr.first_reading_at
FROM node n
JOIN plot p ON p.id = n.plot_id
LEFT JOIN LATERAL (
    SELECT MIN(r.time) AS first_reading_at
    FROM sensor s
    JOIN reading r ON r.sensor_id = s.id
    WHERE s.node_id = n.id
      AND r.time >= n.claimed_at
      AND r.value IS NOT NULL
      AND (r.quality & 2) = 0
) AS fr ON true
WHERE n.claimed_at IS NOT NULL
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(ORG_NODE_FIRST_READING_VIEW)
    op.execute(ORG_MONTH_CYCLES_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_org_month_cycles")
    op.execute("DROP VIEW IF EXISTS metrics_org_node_first_reading")
