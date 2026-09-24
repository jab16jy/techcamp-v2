"""add soil profile

Revision ID: 7f9c1b3cae7f
Revises: 67cf2dd1f13e
Create Date: 2026-09-23 21:35:24.854487

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f9c1b3cae7f'
down_revision: Union[str, Sequence[str], None] = '67cf2dd1f13e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "soil_profile",
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), primary_key=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("ph", sa.Numeric(), nullable=True),
        sa.Column("organic_matter_pct", sa.Numeric(), nullable=True),
        sa.Column("texture", sa.String(), nullable=True),
        sa.Column("field_capacity_pct", sa.Numeric(), nullable=True),
        sa.Column("wilting_point_pct", sa.Numeric(), nullable=True),
        sa.Column("root_depth_cm", sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "source is null or source in ('soilgrids','lab','fao56_texture')",
            name="ck_soil_profile_source",
        ),
        sa.CheckConstraint("ph is null or (ph >= 0 and ph <= 14)", name="ck_soil_profile_ph_range"),
        sa.CheckConstraint(
            "organic_matter_pct is null or (organic_matter_pct >= 0 and organic_matter_pct <= 100)",
            name="ck_soil_profile_organic_matter_range",
        ),
        sa.CheckConstraint(
            "field_capacity_pct is null or (field_capacity_pct > 0 and field_capacity_pct <= 100)",
            name="ck_soil_profile_field_capacity_range",
        ),
        sa.CheckConstraint(
            "wilting_point_pct is null or (wilting_point_pct >= 0 and wilting_point_pct < 100)",
            name="ck_soil_profile_wilting_point_range",
        ),
        sa.CheckConstraint(
            "root_depth_cm is null or root_depth_cm > 0",
            name="ck_soil_profile_root_depth_positive",
        ),
        sa.CheckConstraint(
            "field_capacity_pct is null or wilting_point_pct is null "
            "or wilting_point_pct < field_capacity_pct",
            name="ck_soil_profile_wilting_point_lt_field_capacity",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("soil_profile")
