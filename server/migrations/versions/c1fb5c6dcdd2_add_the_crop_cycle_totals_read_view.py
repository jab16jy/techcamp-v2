"""add the crop cycle totals read view

Creates `metrics_crop_cycle_totals`, the raw evidence behind the per-cycle impact
summary (E11 T3, D-T0.8; docs/11-metricas.md §1). It is a read-only SQL view, the
exception docs/05-arquitectura.md §Reglas grants `metrics`, and nothing writes to
it: the summary itself is persisted by T4, per cycle and per hectare.

Each sum attributes an entry to a cycle by `crop_cycle_id`, which is the exact
membership the offline sync guarantees (docs/03-modelo-datos.md:410). The
assimilated water balance carries no cycle id, so it is read over a date window:
from `sown_on` to `LEAST(expected_harvest_on, today)`. `crop_cycle` has no end
date to join on, and the future is never inside a cycle — so an active cycle is
measured up to today and a finished one up to the harvest date it carries. That
is the one place where the view has to choose a window rather than follow a
foreign key, and the repository documents it on the method.

Every sum is a filtered `SUM`, so a cycle with no harvest, no task, no cost or no
irrigation reads `NULL` rather than `0`: a missing datum is not a zero
(docs/03-modelo-datos.md:441, D-T0.3). `cost_cop` counts `task`, `input` and
`cost` only — an `observation` carrying an `alert_id` records a **loss**, so its
`cost_cop` lands in `loss_cop` and never in the cost of the cycle
(docs/03:424). `water_stress_days` is `NULL` when the cycle has no assimilated
balance at all, which is missing evidence rather than a stress-free cycle, and 0
when the window holds balance days that are simply never stressed.

The logbook and the water balance are aggregated in separate `LEFT JOIN
LATERAL`s on purpose: joining both one-to-many sides in a single `FROM` would
multiply them against each other and inflate every sum.

Revision ID: c1fb5c6dcdd2
Revises: 0c67563f8d20
Create Date: 2026-10-02 17:02:55.480311

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c1fb5c6dcdd2'
down_revision: Union[str, Sequence[str], None] = '0c67563f8d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CROP_CYCLE_TOTALS_VIEW = """
CREATE VIEW metrics_crop_cycle_totals AS
SELECT
    p.org_id,
    c.id AS crop_cycle_id,
    c.plot_id,
    le.yield_kg,
    le.sold_kg,
    le.revenue_cop,
    le.labor_days,
    le.cost_cop,
    le.irrigation_mm,
    le.loss_kg,
    le.loss_cop,
    CASE
        WHEN COALESCE(ws.balance_days, 0) = 0 THEN NULL
        ELSE ws.stress_days
    END AS water_stress_days
FROM crop_cycle c
JOIN plot p ON p.id = c.plot_id
LEFT JOIN LATERAL (
    SELECT
        SUM(e.yield_kg) FILTER (WHERE e.kind = 'harvest') AS yield_kg,
        SUM(e.sold_kg) FILTER (WHERE e.kind = 'harvest') AS sold_kg,
        SUM(e.sold_kg * e.sale_price_cop_per_kg) FILTER (WHERE e.kind = 'harvest') AS revenue_cop,
        SUM(e.labor_days) FILTER (WHERE e.kind = 'task') AS labor_days,
        SUM(e.cost_cop) FILTER (WHERE e.kind IN ('task', 'input', 'cost')) AS cost_cop,
        SUM(e.irrigation_mm) FILTER (WHERE e.kind = 'irrigation') AS irrigation_mm,
        SUM(e.quantity) FILTER (
            WHERE e.kind = 'observation' AND e.alert_id IS NOT NULL
        ) AS loss_kg,
        SUM(e.cost_cop) FILTER (
            WHERE e.kind = 'observation' AND e.alert_id IS NOT NULL
        ) AS loss_cop
    FROM logbook_entry e
    WHERE e.crop_cycle_id = c.id AND e.deleted_at IS NULL
) AS le ON true
LEFT JOIN LATERAL (
    SELECT
        COUNT(wb.day) AS balance_days,
        COUNT(wb.day) FILTER (WHERE wb.depletion_mm > wb.raw_mm) AS stress_days
    FROM water_balance_daily wb
    WHERE wb.plot_id = c.plot_id
      AND wb.day >= c.sown_on
      AND wb.day <= LEAST(
          COALESCE(c.expected_harvest_on, (now() AT TIME ZONE 'America/Bogota')::date),
          (now() AT TIME ZONE 'America/Bogota')::date
      )
) AS ws ON true
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CROP_CYCLE_TOTALS_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_crop_cycle_totals")
