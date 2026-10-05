"""bound the water-stress window of a finished cycle by its closing entry

Rewrites `metrics_crop_cycle_totals` so `water_stress_days` stops reading the
assimilated balance of a **closed** cycle past the day that cycle was closed.
E11 T10 (CodeRabbit finding on `c1fb5c6dcdd2`; docs/11-metricas.md §1).

**What was wrong.** The window ended at

    LEAST(COALESCE(c.expected_harvest_on, today), today)

`expected_harvest_on` is derived, but not always present: `compute_expected_harvest_on`
returns `None` for a crop with no FAO-56 stages (yam, farms/domain/models.py:181),
and `_reject_explicit_null` deliberately exempts that field, so a PATCH may null
it without a 422. `COALESCE` then turned it into `today`, and the only filters
left were `wb.plot_id` and `wb.day >= c.sown_on` — **nothing bounds the window by
cycle**. A closed cycle therefore kept accruing stressed days indefinitely,
including days belonging to the next cycle of the same plot. A wrong number is
worse than a null in this repo's convention (docs/03-modelo-datos.md:441).

**The window now depends on the cycle's status.**

- `active` keeps exactly the old bound: an open cycle has no closure to anchor to,
  so it is measured up to `LEAST(expected_harvest_on, today)` and an expectation
  of its own is the only thing that can stop it earlier.
- `harvested` and `lost` are bounded at the `occurred_on` of the logbook entry
  that registers the closure: a `harvest` entry closes a harvested cycle, an
  `observation` carrying an `alert_id` closes a lost one (that observation *is*
  the loss record, docs/03-modelo-datos.md:424). This is the same anchor D-T7.2
  gives a closed cycle's **month**, and the predicate here is character for
  character the one `metrics_org_month_cycles` uses, so the two views cannot
  disagree about when a cycle ended. A late or wrong `expected_harvest_on` no
  longer extends a window past the day the crop came out of the ground.
- A finished cycle with **no** closing entry has no window at all. That is missing
  evidence, so `water_stress_days` is `null` — never a count capped at `today`.

The `today` cap is kept on the closure side, because the future is never inside a
cycle: a closure entry dated in the future cannot vouch for balance days that
have not happened.

**`null` is now explicit, not a coincidence.** The aggregate always returns one
row, so a closed-but-unanchored cycle used to reach the outer `CASE` with
`balance_days = 0` and be mapped to `null` by the *count* branch — right answer,
wrong reason, and lost the moment the count logic changed. The lateral now also
projects `window_end`, and the outer `CASE` asks it directly whether there is a
window at all. `0` keeps its own branch: a window that holds balance days and none
of them stressed is measured and stress-free, which is not the same fact as
unmeasured (D-T0.3).

Note `LEAST(NULL, today)` would be a trap here: PostgreSQL's `LEAST` ignores null
arguments, so it would answer `today` and silently restore the bug. The null case
is therefore spelled out as its own `WHEN` before the `LEAST`.

Revision ID: 98cab252bfb3
Revises: d2c8f4a1b3e7
Create Date: 2026-10-05 22:55:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "98cab252bfb3"
down_revision: Union[str, Sequence[str], None] = "d2c8f4a1b3e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TODAY = "(now() AT TIME ZONE 'America/Bogota')::date"
"""The local calendar day in America/Bogota (shared.dates.BOGOTA_TZ). Repeated as
a literal because a SQL view has no place to bind a variable, and every use of it
in this file is the same instant."""


CLOSURE_ANCHOR_SQL = """
        SELECT MIN(e.occurred_on) AS closed_on
        FROM logbook_entry e
        WHERE e.crop_cycle_id = c.id
          AND e.deleted_at IS NULL
          AND (
              (c.status = 'harvested' AND e.kind = 'harvest')
              OR (c.status = 'lost' AND e.kind = 'observation' AND e.alert_id IS NOT NULL)
          )
"""
"""The closure-registering entry, by the cycle's own status. Character for
character the predicate of `metrics_org_month_cycles`, so this view and that one
date a cycle's end the same way (D-T7.2)."""


def _water_stress_lateral() -> str:
    """The `ws` lateral: the water-stress window and its two counts.

    Built by a function because the same block has to exist twice — once for the
    new view and once, verbatim, in `downgrade()` — and a copy that can drift
    between them would make the rollback a second migration hiding inside this
    one.
    """
    return f"""
LEFT JOIN LATERAL (
    {CLOSURE_ANCHOR_SQL}
) AS closure ON true
LEFT JOIN LATERAL (
    SELECT
        CASE
            WHEN c.status = 'active' THEN LEAST(
                COALESCE(c.expected_harvest_on, {_TODAY}),
                {_TODAY}
            )
            WHEN closure.closed_on IS NULL THEN NULL
            ELSE LEAST(closure.closed_on, {_TODAY})
        END AS window_end,
        COUNT(wb.day) AS balance_days,
        COUNT(wb.day) FILTER (WHERE wb.depletion_mm > wb.raw_mm) AS stress_days
    FROM water_balance_daily wb
    WHERE wb.plot_id = c.plot_id
      AND wb.day >= c.sown_on
      AND wb.day <= CASE
            WHEN c.status = 'active' THEN LEAST(
                COALESCE(c.expected_harvest_on, {_TODAY}),
                {_TODAY}
            )
            WHEN closure.closed_on IS NULL THEN NULL
            ELSE LEAST(closure.closed_on, {_TODAY})
        END
) AS ws ON true
"""


CROP_CYCLE_TOTALS_VIEW = f"""
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
        WHEN ws.window_end IS NULL THEN NULL
        WHEN ws.balance_days = 0 THEN NULL
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
{_water_stress_lateral()}
"""


PREVIOUS_CROP_CYCLE_TOTALS_VIEW = f"""
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
          COALESCE(c.expected_harvest_on, {_TODAY}),
          {_TODAY}
      )
) AS ws ON true
"""
"""`c1fb5c6dcdd2`'s view, byte for byte, so `downgrade()` is a real rollback and
not a second guess at what the previous revision contained."""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_crop_cycle_totals")
    op.execute(CROP_CYCLE_TOTALS_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_crop_cycle_totals")
    op.execute(PREVIOUS_CROP_CYCLE_TOTALS_VIEW)