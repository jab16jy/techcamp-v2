"""add plot farm composite fk and range checks

Revision ID: 6628f7c0aa3b
Revises: 9098dc0927a3
Create Date: 2026-09-23 20:37:41.941178

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6628f7c0aa3b'
down_revision: Union[str, Sequence[str], None] = '9098dc0927a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # T1 review follow-up (odd/tasks/techcamp-v2-e3-farms.md): tie plot.org_id
    # to its farm's org_id with a composite FK, add range CHECKs and an
    # (org_id, farm_id) index for listing plots by farm.
    op.create_unique_constraint('uq_farm_id_org_id', 'farm', ['id', 'org_id'])
    op.create_index('ix_plot_org_farm', 'plot', ['org_id', 'farm_id'], unique=False)
    op.drop_constraint(op.f('plot_farm_id_fkey'), 'plot', type_='foreignkey')
    op.create_foreign_key(
        'fk_plot_farm_id_org_id', 'plot', 'farm', ['farm_id', 'org_id'], ['id', 'org_id']
    )
    op.create_check_constraint(
        'ck_plot_irrigation_efficiency_range',
        'plot',
        'irrigation_efficiency is null or (irrigation_efficiency > 0 and irrigation_efficiency <= 1)',
    )
    op.create_check_constraint(
        'ck_plot_system_flow_positive',
        'plot',
        'system_flow_lph is null or system_flow_lph > 0',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_plot_system_flow_positive', 'plot', type_='check')
    op.drop_constraint('ck_plot_irrigation_efficiency_range', 'plot', type_='check')
    op.drop_constraint('fk_plot_farm_id_org_id', 'plot', type_='foreignkey')
    op.create_foreign_key(op.f('plot_farm_id_fkey'), 'plot', 'farm', ['farm_id'], ['id'])
    op.drop_index('ix_plot_org_farm', table_name='plot')
    op.drop_constraint('uq_farm_id_org_id', 'farm', type_='unique')
