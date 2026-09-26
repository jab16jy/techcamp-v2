"""SQLAlchemy table mappings for alerts (docs/03-modelo-datos.md:272-297, 484; docs/06 §3)."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class AlertRuleRow(Base):
    """Alert evaluation rule (docs/03-modelo-datos.md:272-283; docs/06 §3).

    `org_id = null` represents a factory/system rule. Custom threshold rules
    carry their owning `org_id` (D11).
    """

    __tablename__ = "alert_rule"
    __table_args__ = (
        CheckConstraint("operator in ('<', '>')", name="ck_alert_rule_operator"),
        CheckConstraint(
            "severity in ('info', 'warning', 'critical')", name="ck_alert_rule_severity"
        ),
        Index(
            "uq_alert_rule_factory_code",
            "code",
            unique=True,
            postgresql_where=text("org_id IS NULL"),
        ),
        Index("ix_alert_rule_org_id", "org_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), nullable=True
    )
    code: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str | None] = mapped_column(String, nullable=True)
    operator: Mapped[str | None] = mapped_column(String, nullable=True)
    threshold: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    hysteresis: Mapped[decimal.Decimal] = mapped_column(
        Numeric, default=decimal.Decimal(0), nullable=False
    )
    min_duration_min: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    crop_id: Mapped[int | None] = mapped_column(ForeignKey("crop.id"), nullable=True)


class AlertRow(Base):
    """An alert raised for a plot or node (docs/03-modelo-datos.md:284-297; docs/06 §3).

    One non-resolved alert per rule and target: partial unique indexes on
    `(rule_id, plot_id)` and `(rule_id, node_id)` where `state <> 'resolved'`.
    `org_id` is denormalized for fast filtering without joins (docs/03:18).
    """

    __tablename__ = "alert"
    __table_args__ = (
        CheckConstraint("num_nonnulls(plot_id, node_id) = 1", name="ck_alert_target_exactly_one"),
        CheckConstraint("state in ('open', 'acknowledged', 'resolved')", name="ck_alert_state"),
        CheckConstraint("severity in ('info', 'warning', 'critical')", name="ck_alert_severity"),
        CheckConstraint("outcome in ('confirmed', 'false_alarm')", name="ck_alert_outcome"),
        Index(
            "uq_alert_non_resolved_plot",
            "rule_id",
            "plot_id",
            unique=True,
            postgresql_where=text("state <> 'resolved'"),
        ),
        Index(
            "uq_alert_non_resolved_node",
            "rule_id",
            "node_id",
            unique=True,
            postgresql_where=text("state <> 'resolved'"),
        ),
        Index(
            "ix_alert_org_state_opened",
            "org_id",
            "state",
            text("opened_at DESC"),
            postgresql_where=text("state <> 'resolved'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_rule.id"), nullable=False)
    plot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plot.id"), nullable=True)
    node_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("node.id"), nullable=True)
    state: Mapped[str] = mapped_column(String, default="open", nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    opened_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String, nullable=True)
