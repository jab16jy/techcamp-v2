"""create farm and plot tables

Revision ID: 9098dc0927a3
Revises: b358c1328b49
Create Date: 2026-09-23 20:22:59.605495

"""
from typing import Sequence, Union

import geoalchemy2
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9098dc0927a3'
down_revision: Union[str, Sequence[str], None] = 'b358c1328b49'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'farm',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('municipality_code', sa.String(), nullable=False),
        sa.Column(
            'location',
            geoalchemy2.Geometry(geometry_type='POINT', srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column('technician_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['technician_id'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_farm_org_id', 'farm', ['org_id'])

    op.create_table(
        'plot',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('farm_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column(
            'boundary',
            geoalchemy2.Geometry(geometry_type='POLYGON', srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            'area_ha',
            sa.Numeric(),
            sa.Computed('ST_Area(boundary::geography) / 10000', persisted=True),
            nullable=False,
        ),
        sa.Column('weather_cell_id', sa.Integer(), nullable=True),
        sa.Column('irrigation_system', sa.String(), nullable=False),
        sa.Column('irrigation_efficiency', sa.Numeric(), nullable=True),
        sa.Column('system_flow_lph', sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "irrigation_system in ('none','drip','sprinkler','gravity')",
            name='ck_plot_irrigation_system',
        ),
        sa.CheckConstraint(
            "irrigation_system <> 'none' "
            "or (irrigation_efficiency is null and system_flow_lph is null)",
            name='ck_plot_rainfed_has_no_irrigation',
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['farm_id'], ['farm.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_plot_boundary', 'plot', ['boundary'], postgresql_using='gist')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_plot_boundary', table_name='plot')
    op.drop_table('plot')
    op.drop_index('ix_farm_org_id', table_name='farm')
    op.drop_table('farm')
