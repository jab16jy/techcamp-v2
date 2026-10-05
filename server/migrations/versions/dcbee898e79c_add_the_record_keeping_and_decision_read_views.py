"""add the record keeping and decision read views

Creates `metrics_plot_month_logbook` and `metrics_plot_day_decision`, the
evidence behind the `record_keeping` and `decision` components of the digital
adoption index (E11 T3, D-T0.3 to D-T0.5; docs/11-metricas.md §2). Like the
monitoring view they are part of the read-only SQL views `metrics` reads other
modules' data through (docs/05-arquitectura.md §Reglas), and nothing writes to
them.

`metrics_plot_month_logbook` buckets by ISO week (Monday start) *overlapping* the
month, so a week straddling the first or the last of the month appears in both
months with only that month's entries counted and the ratio of weeks with an
entry over weeks of the month can never exceed 1. It emits one row per week that
holds at least one entry and no row for a week with none: a zero row would read
as `0` evidence instead of as none (D-T0.3). The denominator, "semanas del mes",
is the same calendar counted over the whole month and is calendar math, so it
belongs to the domain code of T5 rather than to this view.

`metrics_plot_day_decision` keeps every stored recommendation, including
`rainfed` and `no_kc`, which docs/11 §2 does not count: the view reports
evidence and the domain code of T5 decides what counts. Hiding them here would
make "no countable day" and "no evidence" indistinguishable, and on a rainfed
plot the index splits 100 points over the other three components (docs/11:52).

The day's applied depth is a correlated sum that returns NULL — not 0 — when
nothing was recorded: "did not irrigate" and "applied no millimetres" are
different facts, and only the first one means a `postpone` or `not_needed` day
was followed (D-T0.5).

Tombstoned entries (`deleted_at IS NOT NULL`) are excluded from both views: a
discarded entry is not evidence (docs/03-modelo-datos.md:237).

Revision ID: dcbee898e79c
Revises: bdc469490565
Create Date: 2026-10-02 15:04:41.220913

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'dcbee898e79c'
down_revision: Union[str, Sequence[str], None] = 'bdc469490565'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLOT_MONTH_LOGBOOK_VIEW = """
CREATE VIEW metrics_plot_month_logbook AS
SELECT
    p.org_id,
    e.plot_id,
    date_trunc('month', e.occurred_on)::date AS month,
    date_trunc('week', e.occurred_on)::date AS week_start,
    COUNT(*) AS entry_count
FROM logbook_entry e
JOIN plot p ON p.id = e.plot_id
WHERE e.deleted_at IS NULL
GROUP BY p.org_id, e.plot_id, date_trunc('month', e.occurred_on)::date,
         date_trunc('week', e.occurred_on)::date
"""

PLOT_DAY_DECISION_VIEW = """
CREATE VIEW metrics_plot_day_decision AS
SELECT
    p.org_id,
    r.plot_id,
    r.day,
    r.kind,
    r.depth_mm,
    (
        SELECT SUM(e.irrigation_mm)
        FROM logbook_entry e
        WHERE e.plot_id = r.plot_id
          AND e.kind = 'irrigation'
          AND e.deleted_at IS NULL
          AND e.occurred_on = r.day
    ) AS irrigation_mm
FROM irrigation_recommendation r
JOIN plot p ON p.id = r.plot_id
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(PLOT_MONTH_LOGBOOK_VIEW)
    op.execute(PLOT_DAY_DECISION_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_plot_day_decision")
    op.execute("DROP VIEW IF EXISTS metrics_plot_month_logbook")
