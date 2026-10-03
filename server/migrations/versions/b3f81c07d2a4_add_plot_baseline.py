"""add plot baseline enrollment survey

Creates `plot_baseline`, the survey of how a plot produced before TechCamp that
impact is measured against (docs/03-modelo-datos.md:239-248, 426-430;
docs/11-metricas.md §1, ADR-0024). One row per plot, so the plot id is the primary
key; `PUT /plots/{id}/baseline` replaces the row whole (D-T0.11).

`plot` gains `uq_plot_id_org_id` first, so this table can tie its `(plot_id,
org_id)` to `(plot.id, plot.org_id)` with a composite foreign key — the pattern
`6628f7c0aa3b` introduced for `plot` -> `farm`. That is what stops a survey row from
naming one organization while pointing at another organization's plot
(docs/09-cuellos-de-botella.md#seguridad).

The declared yield and cost are nullable and their CHECKs admit null: they are
approximate figures a farmer may simply not know (docs/03:245).

Revision ID: b3f81c07d2a4
Revises: e8b109b00c01
Create Date: 2026-10-02 10:14:22.318904

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f81c07d2a4'
down_revision: Union[str, Sequence[str], None] = 'e8b109b00c01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Referenced by the composite foreign key below.
    op.create_unique_constraint('uq_plot_id_org_id', 'plot', ['id', 'org_id'])

    op.create_table(
        'plot_baseline',
        sa.Column('plot_id', sa.Uuid(), primary_key=True),
        sa.Column('org_id', sa.Uuid(), sa.ForeignKey('organization.id'), nullable=False),
        sa.Column('enrolled_on', sa.Date(), nullable=False),
        sa.Column('crop_id', sa.Integer(), sa.ForeignKey('crop.id'), nullable=False),
        sa.Column('last_yield_kg_ha', sa.Numeric(), nullable=True),
        sa.Column('last_cost_cop_ha', sa.Numeric(), nullable=True),
        sa.Column('irrigation_practice', sa.String(), nullable=False),
        sa.Column('recorded_by', sa.Uuid(), sa.ForeignKey('app_user.id'), nullable=True),
        sa.CheckConstraint(
            "irrigation_practice in ('none','drip','sprinkler','gravity')",
            name='ck_plot_baseline_irrigation_practice',
        ),
        sa.CheckConstraint(
            "last_yield_kg_ha is null or last_yield_kg_ha >= 0",
            name='ck_plot_baseline_last_yield_non_negative',
        ),
        sa.CheckConstraint(
            "last_cost_cop_ha is null or last_cost_cop_ha >= 0",
            name='ck_plot_baseline_last_cost_non_negative',
        ),
        sa.ForeignKeyConstraint(
            ['plot_id', 'org_id'],
            ['plot.id', 'plot.org_id'],
            name='fk_plot_baseline_plot_id_org_id',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('plot_baseline')
    op.drop_constraint('uq_plot_id_org_id', 'plot', type_='unique')