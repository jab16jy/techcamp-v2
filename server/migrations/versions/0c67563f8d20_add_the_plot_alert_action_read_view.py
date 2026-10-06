"""add the plot alert action read view

Creates `metrics_plot_alert_action`, the evidence behind the `risk_management`
component of the digital adoption index (E11 T3, D-T0.6;
docs/11-metricas.md §2). It is a read-only SQL view, the exception
docs/05-arquitectura.md §Reglas grants `metrics`, and nothing writes to it.

The view inner-joins `plot`, which drops every node alert: `alert` carries
exactly one of `plot_id`/`node_id` (docs/03-modelo-datos.md:285) and a node alert
goes to the technician, counting for nothing in the plot's index (docs/11:50).

`has_timely_action` is the existence of an action, not the alert's state, because
acknowledging an alert is not acting on it (docs/11:50). A `water_stress` alert on
an irrigated plot is answered by a registered irrigation inside the window, and
every other plot alert by an entry carrying its `alert_id`. A rainfed plot has no
irrigation to register, so the clause cannot apply there (ADR-0023).

That irrigation clause is scoped to the **plot**, not to a crop cycle. The owner's
ruling D-T3.1 (2026-10-03) makes that explicit in docs/11-metricas.md §2 and
docs/adr/0025-la-accion-de-water-stress-es-de-la-parcela.md: the action is a registered
irrigation **on that plot**, with **no crop-cycle qualifier**, so an entry whose
`crop_cycle_id` is null counts too.

The reason is in the model, not in this view. `logbook_entry.crop_cycle_id` is
nullable (docs/03-modelo-datos.md:410) because the phone sends the cycle it happens
to have cached and often sends none, and `alert` carries no `crop_cycle_id` at all, so
scoping the clause by cycle would need a temporal join no doc defines and would discard
real actions. Proved in `test_alert_action_view.py`. This paragraph closes the escalated
CRITICAL `R3-reliability.alert-action.cross-plot` by making the doc say what the SQL
already did; the predicate below is deliberately unchanged.

The window is the logbook's own grain — `occurred_on` is a date, so "timely" runs
from the *local* day of `opened_at` to the local day of `opened_at + 48 h`
(D-T0.6), both resolved in America/Bogota rather than in the session's `TimeZone`.
That local day is also exposed as `opened_day`, so the month a caller asks for is
filtered on the same calendar: comparing `opened_at` against a bare date would
read the bound in the session's zone and shift the month by five hours under UTC
(D-T0.7).

Tombstoned entries (`deleted_at IS NOT NULL`) are not actions: a discarded entry
is not evidence (docs/03-modelo-datos.md:237).

Revision ID: 0c67563f8d20
Revises: dcbee898e79c
Create Date: 2026-10-02 16:21:09.117462

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '0c67563f8d20'
down_revision: Union[str, Sequence[str], None] = 'dcbee898e79c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PLOT_ALERT_ACTION_VIEW = """
CREATE VIEW metrics_plot_alert_action AS
SELECT
    a.org_id,
    a.plot_id,
    a.id AS alert_id,
    a.opened_at,
    w.opened_day,
    r.code AS rule_code,
    (
        EXISTS (
            SELECT 1
            FROM logbook_entry e
            WHERE e.alert_id = a.id
              AND e.deleted_at IS NULL
              AND e.occurred_on >= w.opened_day
              AND e.occurred_on <= w.due_day
        )
        OR (
            r.code = 'water_stress'
            AND p.irrigation_system <> 'none'
            AND EXISTS (
                SELECT 1
                FROM logbook_entry e
                WHERE e.plot_id = a.plot_id
                  AND e.kind = 'irrigation'
                  AND e.deleted_at IS NULL
                  AND e.occurred_on >= w.opened_day
                  AND e.occurred_on <= w.due_day
            )
        )
    ) AS has_timely_action
FROM alert a
JOIN alert_rule r ON r.id = a.rule_id
JOIN plot p ON p.id = a.plot_id
CROSS JOIN LATERAL (
    SELECT
        (a.opened_at AT TIME ZONE 'America/Bogota')::date AS opened_day,
        ((a.opened_at + INTERVAL '48 hours') AT TIME ZONE 'America/Bogota')::date AS due_day
) AS w
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(PLOT_ALERT_ACTION_VIEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP VIEW IF EXISTS metrics_plot_alert_action")
