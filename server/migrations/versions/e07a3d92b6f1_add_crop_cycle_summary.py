"""add crop cycle summary

Creates `crop_cycle_summary`, the per-cycle impact (docs/03-modelo-datos.md:439;
docs/11-metricas.md §1; E11 D-T0.8). Only `harvested` and `lost` cycles are stored;
an active cycle's summary is computed on read and never persisted (docs/03:443).

Every metric column is nullable and every CHECK admits null (docs/03:441). A
rainfed plot has no applied water and no irrigation WUE; there is no yield change
without an enrollment survey or the same crop; `relative_yield` stays null until
`field_record` exists, because the v1 EVA data was not recovered (D-T0.9).

`yield_change_vs_baseline` and `gross_margin_cop` get no non-negative CHECK on
purpose: a cycle can yield less than the enrollment survey, and a cycle can cost
more than it earns. Constraining them to >= 0 would refuse a real, negative result.

`org_id` is tied to the plot's own through a composite foreign key, the pattern
`6628f7c0aa3b` introduced for `plot` -> `farm`
(docs/09-cuellos-de-botella.md#seguridad). `plot_id` is in turn tied to the cycle's
own plot through `(crop_cycle_id, plot_id)` -> `crop_cycle(id, plot_id)`, so a
summary row cannot report one plot's metrics for another plot's cycle. No index:
every documented query reaches the row through `crop_cycle_id` or through the
plot's own cycles.

Revision ID: e07a3d92b6f1
Revises: d5c92e14f8b6
Create Date: 2026-10-02 10:47:51.226017

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e07a3d92b6f1'
down_revision: Union[str, Sequence[str], None] = 'd5c92e14f8b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Cycle metrics that are physically non-negative. `yield_change_vs_baseline` and
# `gross_margin_cop` are deliberately absent (see the module docstring).
_NON_NEGATIVE_METRICS = (
    'yield_kg_ha',
    'relative_yield',
    'water_applied_m3_ha',
    'irrigation_wue_kg_m3',
    'water_stress_days',
    'cost_cop_ha',
    'cost_cop_kg',
    'yield_kg_per_labor_day',
    'loss_kg',
    'loss_cop',
)


def upgrade() -> None:
    """Upgrade schema."""
    # Referenced by `fk_crop_cycle_summary_crop_cycle_id_plot_id` below, so a summary
    # row cannot name a cycle of one plot while naming another plot.
    op.create_unique_constraint('uq_crop_cycle_id_plot_id', 'crop_cycle', ['id', 'plot_id'])

    op.create_table(
        'crop_cycle_summary',
        # No column-level FK: `fk_crop_cycle_summary_crop_cycle_id_plot_id` ties it to
        # `crop_cycle.id` together with `plot_id`.
        sa.Column('crop_cycle_id', sa.Uuid(), primary_key=True),
        sa.Column('plot_id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), sa.ForeignKey('organization.id'), nullable=False),
        sa.Column('yield_kg_ha', sa.Numeric(), nullable=True),
        sa.Column('yield_change_vs_baseline', sa.Numeric(), nullable=True),
        sa.Column('relative_yield', sa.Numeric(), nullable=True),
        sa.Column('water_applied_m3_ha', sa.Numeric(), nullable=True),
        sa.Column('irrigation_wue_kg_m3', sa.Numeric(), nullable=True),
        sa.Column('water_stress_days', sa.Integer(), nullable=True),
        sa.Column('cost_cop_ha', sa.Numeric(), nullable=True),
        sa.Column('cost_cop_kg', sa.Numeric(), nullable=True),
        sa.Column('yield_kg_per_labor_day', sa.Numeric(), nullable=True),
        sa.Column('gross_margin_cop', sa.Numeric(), nullable=True),
        sa.Column('loss_kg', sa.Numeric(), nullable=True),
        sa.Column('loss_cop', sa.Numeric(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        *[
            sa.CheckConstraint(
                f"{name} is null or {name} >= 0",
                name=f'ck_crop_cycle_summary_{name}_non_negative',
            )
            for name in _NON_NEGATIVE_METRICS
        ],
        sa.ForeignKeyConstraint(
            ['plot_id', 'org_id'],
            ['plot.id', 'plot.org_id'],
            name='fk_crop_cycle_summary_plot_id_org_id',
        ),
        # The cycle belongs to the plot: without this, one row could report another
        # plot's metrics for a cycle of this plot.
        sa.ForeignKeyConstraint(
            ['crop_cycle_id', 'plot_id'],
            ['crop_cycle.id', 'crop_cycle.plot_id'],
            name='fk_crop_cycle_summary_crop_cycle_id_plot_id',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('crop_cycle_summary')
    op.drop_constraint('uq_crop_cycle_id_plot_id', 'crop_cycle', type_='unique')
