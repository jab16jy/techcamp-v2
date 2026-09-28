"""SQLAlchemy table mappings for notifications (docs/03:298-312, 486; docs/06 §4)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class NotificationRow(Base):
    """Notification outbox entry (docs/03-modelo-datos.md:298-306; docs/06 §4; ADR-0016).

    Written in the same transaction as the alert. Worker dispatches pending
    items with backoff, up to max attempts.
    """

    __tablename__ = "notification"
    __table_args__ = (
        CheckConstraint("channel in ('push','sms','whatsapp')", name="ck_notification_channel"),
        CheckConstraint("status in ('pending','sent','failed')", name="ck_notification_status"),
        Index(
            "ix_notification_pending_next_attempt",
            "status",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_notification_alert_id", "alert_id"),
        Index("ix_notification_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alert.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PushSubscriptionRow(Base):
    """Web push subscription credentials for a user (docs/03-modelo-datos.md:307-312; docs/06 §4).

    `endpoint` is globally unique. 410 Gone response on send deletes the row.
    """

    __tablename__ = "push_subscription"
    __table_args__ = (Index("ix_push_subscription_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    keys: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
