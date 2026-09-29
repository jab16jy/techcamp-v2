"""add logbook and sync schema

Revision ID: e8b109b00c01
Revises: d4e6f8a0b2c1
Create Date: 2026-09-28 13:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e8b109b00c01"
down_revision: Union[str, Sequence[str], None] = "d4e6f8a0b2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Shared sequence for sync cursor ordering across logbook_entry and extension_visit (D1, D2)
    op.execute("CREATE SEQUENCE sync_server_version_seq AS bigint")

    op.create_table(
        "logbook_entry",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=False),
        sa.Column("crop_cycle_id", sa.Uuid(), sa.ForeignKey("crop_cycle.id"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("cost_cop", sa.Numeric(), nullable=True),
        sa.Column("yield_kg", sa.Numeric(), nullable=True),
        sa.Column("sold_kg", sa.Numeric(), nullable=True),
        sa.Column("sale_price_cop_per_kg", sa.Numeric(), nullable=True),
        sa.Column("labor_days", sa.Numeric(), nullable=True),
        sa.Column("irrigation_mm", sa.Numeric(), nullable=True),
        sa.Column("alert_id", sa.Uuid(), sa.ForeignKey("alert.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column(
            "created_offline", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "server_version",
            sa.BigInteger(),
            server_default=sa.text("nextval('sync_server_version_seq')"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind in ('task','input','irrigation','harvest','observation','cost')",
            name="ck_logbook_entry_kind",
        ),
        sa.CheckConstraint(
            "kind <> 'harvest' or yield_kg is not null",
            name="ck_logbook_entry_harvest_yield",
        ),
        sa.CheckConstraint(
            "kind <> 'irrigation' or irrigation_mm is not null",
            name="ck_logbook_entry_irrigation_depth",
        ),
        sa.CheckConstraint(
            "kind <> 'task' or labor_days is not null",
            name="ck_logbook_entry_task_labor",
        ),
        sa.CheckConstraint(
            "kind not in ('input', 'cost') or cost_cop is not null",
            name="ck_logbook_entry_cost_required",
        ),
        sa.CheckConstraint(
            "kind = 'harvest' or "
            "(yield_kg is null and sold_kg is null and sale_price_cop_per_kg is null)",
            name="ck_logbook_entry_harvest_exclusive",
        ),
        sa.CheckConstraint(
            "kind = 'task' or labor_days is null",
            name="ck_logbook_entry_task_exclusive",
        ),
        sa.CheckConstraint(
            "kind = 'irrigation' or irrigation_mm is null",
            name="ck_logbook_entry_irrigation_exclusive",
        ),
        sa.CheckConstraint(
            "(sold_kg is null and sale_price_cop_per_kg is null) "
            "or (sold_kg is not null and sale_price_cop_per_kg is not null)",
            name="ck_logbook_entry_sold_and_price",
        ),
        sa.CheckConstraint(
            "sold_kg is null or sold_kg <= yield_kg",
            name="ck_logbook_entry_sold_le_yield",
        ),
        sa.CheckConstraint(
            "(quantity is null or quantity >= 0) and (cost_cop is null or cost_cop >= 0) and "
            "(yield_kg is null or yield_kg >= 0) and (sold_kg is null or sold_kg >= 0) and "
            "(sale_price_cop_per_kg is null or sale_price_cop_per_kg >= 0) and "
            "(labor_days is null or labor_days >= 0) and "
            "(irrigation_mm is null or irrigation_mm >= 0)",
            name="ck_logbook_entry_non_negative",
        ),
    )
    op.create_index(
        "ix_logbook_entry_org_server_version",
        "logbook_entry",
        ["org_id", "server_version"],
    )

    op.create_table(
        "extension_visit",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("farm_id", sa.Uuid(), sa.ForeignKey("farm.id"), nullable=False),
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=True),
        sa.Column("technician_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("visited_on", sa.Date(), nullable=False),
        sa.Column("topics", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("recommendations", sa.Text(), nullable=True),
        sa.Column("commitments", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "server_version",
            sa.BigInteger(),
            server_default=sa.text("nextval('sync_server_version_seq')"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "topics <@ ARRAY['human_capacities','social_capacities','information_access',"
            "'natural_resources','participation']::text[]",
            name="ck_extension_visit_topics",
        ),
    )
    op.create_index(
        "ix_extension_visit_org_server_version",
        "extension_visit",
        ["org_id", "server_version"],
    )

    op.create_table(
        "attachment",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "logbook_entry_id",
            sa.Uuid(),
            sa.ForeignKey("logbook_entry.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "extension_visit_id",
            sa.Uuid(),
            sa.ForeignKey("extension_visit.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("object_key", sa.Text(), unique=True, nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint("bytes > 0", name="ck_attachment_bytes_positive"),
        sa.CheckConstraint(
            "num_nonnulls(logbook_entry_id, extension_visit_id) = 1",
            name="ck_attachment_parent_exactly_one",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("attachment")
    op.drop_index("ix_extension_visit_org_server_version", table_name="extension_visit")
    op.drop_table("extension_visit")
    op.drop_index("ix_logbook_entry_org_server_version", table_name="logbook_entry")
    op.drop_table("logbook_entry")
    op.execute("DROP SEQUENCE IF EXISTS sync_server_version_seq")
