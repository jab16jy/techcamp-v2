"""add the monitoring node-month read view

Creates `metrics_node_month_readings`, the evidence behind the `monitoring`
component of the digital adoption index (E11 T3, D-T0.4; docs/11-metricas.md §2).
It is the first of the read-only SQL views `metrics` reads other modules' data
through, the documented exception to "no joins between foreign tables"
(docs/05-arquitectura.md §Reglas: "`metrics` es la única excepción: lee vistas
SQL de solo lectura porque agrega datos de todos"). Nothing writes to it.

The view generates one row per node and per calendar month from the node's claim
month up to the current one, instead of only the months that happen to hold
readings. That is what lets a node which was claimed and then went silent still
produce a denominator: docs/11 §2 counts `lecturas recibidas / lecturas
esperadas` per node, and D-T0.3 makes a component with no denominator null, so a
silent node must read as 0 % monitored rather than as "no evidence".

It reads the `reading` table and not the `reading_daily` continuous aggregate:
that aggregate's `count(value)` skips out-of-range readings, which carry a null
`value`, and its refresh policy leaves the recent tail unmaterialized — while
docs/11 §2 counts every received reading whatever its `quality`. The numerator
counts distinct `reading.time` because every sensor of a node shares one
timestamp per uplink, which is the unit `interval_s` is expressed in (the same
reasoning `SqlAlchemyNodeRepository.count_readings_since` carries).

The `claimed_seconds` window is capped at `LEAST(month end, now())`. The monthly
job runs on day 1 of the following month (D-T0.7), so for every month it ever
computes the cap is the month end; the cap only matters for a month still in
flight, and it exists so an in-flight month is not charged for days that have
not happened yet. Month boundaries are built in America/Bogota (D-T0.7) with
`AT TIME ZONE`, never from the session's `TimeZone`, so a session running under
UTC cannot shift a window by five hours.

**Corrected by `270d5f102dff`.** That sentence above was true of the window
arithmetic and false of the `month` label, which read `bounds.month_start::date`
on a `timestamptz` and therefore resolved in the session's `TimeZone`. The SQL of
this revision is unchanged; only this note and the successor's view differ.

Revision ID: bdc469490565
Revises: e07a3d92b6f1
Create Date: 2026-10-02 14:12:03.884215

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'bdc469490565'
down_revision: Union[str, Sequence[str], None] = 'e07a3d92b6f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NODE_MONTH_READINGS_VIEW = """
CREATE VIEW metrics_node_month_readings AS
SELECT
    p.org_id,
    n.plot_id,
    n.id AS node_id,
    bounds.month_start::date AS month,
    n.interval_s,
    GREATEST(
        0,
        EXTRACT(EPOCH FROM (
            LEAST(bounds.month_end, now()) - GREATEST(bounds.month_start, n.claimed_at)
        ))
    )::bigint AS claimed_seconds,
    COUNT(DISTINCT r.time) AS received_readings
FROM node n
JOIN plot p ON p.id = n.plot_id
CROSS JOIN LATERAL generate_series(
    date_trunc('month', n.claimed_at AT TIME ZONE 'America/Bogota')::timestamp,
    date_trunc('month', now() AT TIME ZONE 'America/Bogota')::timestamp,
    INTERVAL '1 month'
) AS months(month_start_local)
CROSS JOIN LATERAL (
    SELECT
        (months.month_start_local AT TIME ZONE 'America/Bogota') AS month_start,
        ((months.month_start_local + INTERVAL '1 month') AT TIME ZONE 'America/Bogota')
            AS month_end
) AS bounds
LEFT JOIN sensor s ON s.node_id = n.id
LEFT JOIN reading r
    ON r.sensor_id = s.id
   AND r.time >= bounds.month_start
   AND r.time < bounds.month_end
WHERE n.claimed_at IS NOT NULL
GROUP BY p.org_id, n.plot_id, n.id, bounds.month_start, bounds.month_end, n.interval_s
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(NODE_MONTH_READINGS_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_node_month_readings")
