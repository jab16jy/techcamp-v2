"""derive the node-month label from the local month start, not the session zone

Rewrites `metrics_node_month_readings` so its `month` column cannot move with the
session's `TimeZone`. E11 T10 (CodeRabbit finding on `bdc469490565`).

**What was wrong.** The view selected

    bounds.month_start::date AS month

`bounds.month_start` is a `timestamptz` — Bogota local month start, converted to
an instant — and casting a `timestamptz` to `date` resolves it **in the session's
`TimeZone`**. A claim at Bogota midnight on the 1st is the instant
`2026-09-01T05:00Z`, which is 22:00 on the previous day in `America/Los_Angeles`,
so the row was labelled `2026-08-31`: a September query found nothing and an
August query returned a month of evidence the node never claimed. Only sessions
west of UTC-5 are affected, which is why this passed on a UTC host and on a UTC-5
one — the docstring's own claim that boundaries are built "never from the
session's `TimeZone`" was true of the arithmetic and false of the label.

**The fix.** `month` is now taken from `months.month_start_local`, which the
lateral already computes as a `timestamp` — a *naive* Bogota local timestamp.
Casting a `timestamp` to `date` has no zone to resolve, so the label is
time-zone-independent by construction rather than by coincidence. The `GROUP BY`
moves with it, from `bounds.month_start` to `months.month_start_local`; the two
are in one-to-one correspondence, since `bounds` is derived from the same value,
so the grouping grain is unchanged.

**Nothing else moves.** The window arithmetic was never wrong and is not touched:
`month_start`, `month_end` and `claimed_at` are subtracted as absolute instants,
so `claimed_seconds` is identical under every session zone. `AT TIME ZONE` on the
`generate_series` seed and on each bound stays exactly as it was — it is the
correct way to build the boundaries, and the only defect was reading a zone
dependent cast out of them.

Revision ID: 270d5f102dff
Revises: 98cab252bfb3
Create Date: 2026-10-05 23:12:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "270d5f102dff"
down_revision: Union[str, Sequence[str], None] = "98cab252bfb3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MONTH_EXPRESSION = "months.month_start_local::date AS month"
"""The label. `month_start_local` is the naive Bogota timestamp the `generate_series`
lateral already projects, so `::date` on it has no session `TimeZone` to resolve
and the column is the same date under every zone."""

_GROUP_EXPRESSIONS = (
    "p.org_id",
    "n.plot_id",
    "n.id",
    "months.month_start_local",
    "bounds.month_start",
    "bounds.month_end",
    "n.interval_s",
)
"""`bounds.month_start` stays in the list even though it is a pure function of
`months.month_start_local` and the new key groups strictly finer: `claimed_seconds`
selects it, and PostgreSQL only accepts an ungrouped column when it is grouped
itself or an aggregate. Grouping by both keeps the grain identical — the two
expressions are in one-to-one correspondence — so no row is split or merged."""


def _node_month_readings_view(month_expression: str, group_expression: str) -> str:
    """The view, parameterized on the `month` label and its grouping key.

    A function rather than two module constants because the change is one
    expression appearing twice, and a downgrade that restated the view by hand
    would be a second place for the two to drift apart.
    """
    return f"""
CREATE VIEW metrics_node_month_readings AS
SELECT
    p.org_id,
    n.plot_id,
    n.id AS node_id,
    {month_expression},
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
GROUP BY {group_expression}
"""


NODE_MONTH_READINGS_VIEW = _node_month_readings_view(
    _MONTH_EXPRESSION, ", ".join(_GROUP_EXPRESSIONS)
)


PREVIOUS_NODE_MONTH_READINGS_VIEW = _node_month_readings_view(
    "bounds.month_start::date AS month",
    "p.org_id, n.plot_id, n.id, bounds.month_start, bounds.month_end, n.interval_s",
)
"""`bdc469490565`'s view, byte for byte, so `downgrade()` is a real rollback."""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_node_month_readings")
    op.execute(NODE_MONTH_READINGS_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_node_month_readings")
    op.execute(PREVIOUS_NODE_MONTH_READINGS_VIEW)
