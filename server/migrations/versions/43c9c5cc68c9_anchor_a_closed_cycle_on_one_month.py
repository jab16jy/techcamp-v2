"""one closed cycle, one month: anchor the adoption ratio on its closing entry

Rewrites `metrics_org_month_cycles` so a closed cycle emits exactly one row.
E11 T10 (owner ruling 2026-10-05, Option A; D-T7.2, D-T7.3).

**What was wrong.** `d2c8f4a1b3e7` grouped by
`date_trunc('month', e.occurred_on)::date, c.id`, so the *entries* decided the
grain and a cycle with two closure-registering entries got two rows. A cycle with
a September `harvest` and an October loss observation satisfied the join twice and
was counted in the denominator of both months, so
`harvested_cycles_ratio` ("ciclos con cosecha registrada / ciclos terminados",
docs/11-metricas.md:78) was not a fraction of cycles: the same cycle was in two
denominators, and the numerator followed the entry that carried the harvest into
whichever month that entry fell in. The doc said "ciclos con una entrada
`harvest` **en el mes**", which permits exactly that, and the view implemented
the literal reading.

**The ruling.** A closed cycle belongs to exactly one month: the month of its
closing entry. The grain is now one row per closed cycle.

**Which entry closes it.** A reader cannot tell from the log alone which of two
qualifying entries closed the cycle, so the closure is chosen by the cycle's own
`status`, which is the rule D-T7.2 already states in prose: a `harvest` entry
closes a `harvested` cycle, an `observation` carrying an `alert_id` closes a
`lost` one (that observation *is* the loss record,
docs/03-modelo-datos.md:424). When more than one entry of that kind exists,
`MIN(occurred_on)` wins — the earliest one is when the closure was registered.
The same predicate, character for character, bounds a closed cycle's
water-stress window in `98cab252bfb3`, so the two views cannot disagree about
when a cycle ended.

**D-T7.3 is preserved exactly.** The numerator stays literal to the doc and
carries **no status filter**: the month owns the cycle, and the numerator asks
whether that same month holds a `harvest` entry for it. A `lost` cycle harvested
and lost inside one month still counts as harvested, which is the deliberate
asymmetry with the denominator (`status IN ('harvested', 'lost')`): against
contradictory data the producer's log is the strongest evidence available. Only
the month changed, never the status rule.

`occurred_on` is a bare `date`, already a local calendar date, so its month needs
no zone shift — the same statement `d2c8f4a1b3e7` made and this view keeps.
Nothing about the node view, the validity predicate or the median changes.

Revision ID: 43c9c5cc68c9
Revises: 270d5f102dff
Create Date: 2026-10-06 00:05:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "43c9c5cc68c9"
down_revision: Union[str, Sequence[str], None] = "270d5f102dff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CLOSURE_ENTRY_SQL = """
        SELECT MIN(e.occurred_on) AS closed_on
        FROM logbook_entry e
        WHERE e.crop_cycle_id = c.id
          AND e.deleted_at IS NULL
          AND (
              (c.status = 'harvested' AND e.kind = 'harvest')
              OR (c.status = 'lost' AND e.kind = 'observation' AND e.alert_id IS NOT NULL)
          )
"""
"""The closure-registering entry, selected by the cycle's status. Character for
character the predicate `98cab252bfb3` uses to bound a finished cycle's
water-stress window, so one cycle cannot end on two different days across two
views (D-T7.2)."""


MONTHLY_CYCLES_VIEW = f"""
CREATE VIEW metrics_org_month_cycles AS
SELECT
    p.org_id,
    date_trunc('month', closure.closed_on)::date AS month,
    c.id AS crop_cycle_id,
    c.plot_id,
    closure.closed_on,
    harvest.has_harvest
FROM crop_cycle c
JOIN plot p ON p.id = c.plot_id
JOIN LATERAL (
    {CLOSURE_ENTRY_SQL}
) AS closure ON closure.closed_on IS NOT NULL
CROSS JOIN LATERAL (
    SELECT EXISTS (
        SELECT 1
        FROM logbook_entry e
        WHERE e.crop_cycle_id = c.id
          AND e.deleted_at IS NULL
          AND e.kind = 'harvest'
          AND date_trunc('month', e.occurred_on)::date
              = date_trunc('month', closure.closed_on)::date
    ) AS has_harvest
) AS harvest
WHERE c.status IN ('harvested', 'lost')
"""
"""One row per closed cycle.

`JOIN LATERAL ... ON closure.closed_on IS NOT NULL` is what keeps a closed cycle
with no closing entry out of every month: the aggregate over no rows is NULL, and
that is the missing-evidence absence D-T7.2 already required (docs/03:441), now
stated by the join instead of by a `WHERE` that had to notice the shape of the
rows. `EXISTS` always returns exactly one row, so the numerator lateral needs no
outer join of its own: `False` and "no harvest" are the same answer here, and the
denominator only needs the row, which the join above already decided.
"""


PREVIOUS_MONTHLY_CYCLES_VIEW = """
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
"""`d2c8f4a1b3e7`'s view, byte for byte, so `downgrade()` is a real rollback and
not a second guess at what the previous revision contained."""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_org_month_cycles")
    op.execute(MONTHLY_CYCLES_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_org_month_cycles")
    op.execute(PREVIOUS_MONTHLY_CYCLES_VIEW)