"""add plot metric monthly

Creates `plot_metric_monthly`, the digital adoption index and its four components
for one calendar month (docs/03-modelo-datos.md:438; docs/11-metricas.md §2,
E11 D-T0.2).

`month` is the first day of the month, and a CHECK says so: the primary key is the
bucket itself, so a mid-month date would silently create a second bucket for the
same month and `D-T0.3`'s "split the 100 points over the non-null components" would
run twice.

Every component and the index are nullable, and each range CHECK admits null
(docs/03:441): a component with no denominator is `null`, never `0` or `1`
(docs/11:57, D-T0.3). The index is null when all four components are null.

`org_id` is tied to the plot's own through a composite foreign key, the pattern
`6628f7c0aa3b` introduced for `plot` -> `farm`
(docs/09-cuellos-de-botella.md#seguridad). `ix_plot_metric_monthly_org_month` serves
the org-month listing behind `GET /organizations/{org_id}/metrics?month=`
(docs/04-api.md:226, D-T0.12); it is the only index the documented queries need.

Revision ID: d5c92e14f8b6
Revises: b3f81c07d2a4
Create Date: 2026-10-02 10:31:07.884210

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5c92e14f8b6'
down_revision: Union[str, Sequence[str], None] = 'b3f81c07d2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The four equal-weight components, each a 0-1 ratio (docs/11-metricas.md:42-52).
_COMPONENTS = ('monitoring', 'record_keeping', 'decision', 'risk_management')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'plot_metric_monthly',
        sa.Column('plot_id', sa.Uuid(), primary_key=True),
        sa.Column('month', sa.Date(), primary_key=True),
        sa.Column('org_id', sa.Uuid(), sa.ForeignKey('organization.id'), nullable=False),
        *[sa.Column(name, sa.Numeric(), nullable=True) for name in _COMPONENTS],
        sa.Column('digital_adoption_index', sa.Numeric(), nullable=True),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        *[
            sa.CheckConstraint(
                f"{name} is null or ({name} >= 0 and {name} <= 1)",
                name=f'ck_plot_metric_monthly_{name}_range',
            )
            for name in _COMPONENTS
        ],
        sa.CheckConstraint(
            "digital_adoption_index is null or "
            "(digital_adoption_index >= 0 and digital_adoption_index <= 100)",
            name='ck_plot_metric_monthly_index_range',
        ),
        sa.CheckConstraint(
            "extract(day from month) = 1",
            name='ck_plot_metric_monthly_month_is_first_of_month',
        ),
        sa.ForeignKeyConstraint(
            ['plot_id', 'org_id'],
            ['plot.id', 'plot.org_id'],
            name='fk_plot_metric_monthly_plot_id_org_id',
        ),
        sa.Index('ix_plot_metric_monthly_org_month', 'org_id', 'month'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('plot_metric_monthly')