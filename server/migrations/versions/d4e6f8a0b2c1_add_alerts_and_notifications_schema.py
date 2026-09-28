"""add alerts and notifications schema

Revision ID: d4e6f8a0b2c1
Revises: d8a2f1c4e9b7
Create Date: 2026-09-26 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from techcamp.alerts.adapters.seed import FACTORY_RULES

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c1"
down_revision: Union[str, Sequence[str], None] = "d8a2f1c4e9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alert_rule",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("metric", sa.String(), nullable=True),
        sa.Column("operator", sa.String(), nullable=True),
        sa.Column("threshold", sa.Numeric(), nullable=True),
        sa.Column("hysteresis", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "min_duration_min", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("crop_id", sa.Integer(), sa.ForeignKey("crop.id"), nullable=True),
        sa.CheckConstraint("operator in ('<', '>')", name="ck_alert_rule_operator"),
        sa.CheckConstraint(
            "severity in ('info', 'warning', 'critical')", name="ck_alert_rule_severity"
        ),
    )
    op.create_index(
        "uq_alert_rule_factory_code",
        "alert_rule",
        ["code"],
        unique=True,
        postgresql_where=sa.text("org_id IS NULL"),
    )
    op.create_index("ix_alert_rule_org_id", "alert_rule", ["org_id"])

    # Seed factory rules
    alert_rule_table = sa.table(
        "alert_rule",
        sa.column("id", sa.Uuid),
        sa.column("org_id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("metric", sa.String),
        sa.column("operator", sa.String),
        sa.column("threshold", sa.Numeric),
        sa.column("hysteresis", sa.Numeric),
        sa.column("min_duration_min", sa.Integer),
        sa.column("severity", sa.String),
        sa.column("crop_id", sa.Integer),
    )
    op.bulk_insert(alert_rule_table, [dict(r) for r in FACTORY_RULES])

    op.create_table(
        "alert",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("alert_rule.id"), nullable=False),
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=True),
        sa.Column("node_id", sa.Uuid(), sa.ForeignKey("node.id"), nullable=True),
        sa.Column("state", sa.String(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.CheckConstraint(
            "num_nonnulls(plot_id, node_id) = 1", name="ck_alert_target_exactly_one"
        ),
        sa.CheckConstraint(
            "state in ('open', 'acknowledged', 'resolved')", name="ck_alert_state"
        ),
        sa.CheckConstraint(
            "severity in ('info', 'warning', 'critical')", name="ck_alert_severity"
        ),
        sa.CheckConstraint("outcome in ('confirmed', 'false_alarm')", name="ck_alert_outcome"),
    )
    op.create_index(
        "uq_alert_non_resolved_plot",
        "alert",
        ["rule_id", "plot_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'resolved'"),
    )
    op.create_index(
        "uq_alert_non_resolved_node",
        "alert",
        ["rule_id", "node_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'resolved'"),
    )
    op.create_index(
        "ix_alert_org_state_opened",
        "alert",
        ["org_id", "state", sa.text("opened_at DESC")],
        postgresql_where=sa.text("state <> 'resolved'"),
    )

    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "alert_id",
            sa.Uuid(),
            sa.ForeignKey("alert.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column(
            "status", sa.String(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "channel in ('push', 'sms', 'whatsapp')", name="ck_notification_channel"
        ),
        sa.CheckConstraint(
            "status in ('pending', 'sent', 'failed')", name="ck_notification_status"
        ),
    )
    op.create_index(
        "ix_notification_pending_next_attempt",
        "notification",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_notification_alert_id", "notification", ["alert_id"])
    op.create_index("ix_notification_user_id", "notification", ["user_id"])

    op.create_table(
        "push_subscription",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("app_user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), unique=True, nullable=False),
        sa.Column("keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_push_subscription_user_id", "push_subscription", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("push_subscription")
    op.drop_table("notification")
    op.drop_table("alert")
    op.drop_table("alert_rule")
