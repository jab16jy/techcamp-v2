"""SQLAlchemy table mappings for logbook (docs/03:216-271; docs/06 §7; D1, D6, D10)."""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base

sync_server_version_seq = Sequence("sync_server_version_seq", metadata=Base.metadata)


class LogbookEntryRow(Base):
    """Offline logbook entry (docs/03:216-238, 401-425; docs/06 §7; D10).

    Uses a client-generated UUIDv7 for idempotency. Shares `sync_server_version_seq`
    with `extension_visit`. Strict typed columns and CHECKs per kind enforce
    impact metric fields at DB level.
    """

    __tablename__ = "logbook_entry"
    __table_args__ = (
        CheckConstraint(
            "kind in ('task','input','irrigation','harvest','observation','cost')",
            name="ck_logbook_entry_kind",
        ),
        CheckConstraint(
            "kind <> 'harvest' or yield_kg is not null",
            name="ck_logbook_entry_harvest_yield",
        ),
        CheckConstraint(
            "kind <> 'irrigation' or irrigation_mm is not null",
            name="ck_logbook_entry_irrigation_depth",
        ),
        CheckConstraint(
            "kind <> 'task' or labor_days is not null",
            name="ck_logbook_entry_task_labor",
        ),
        CheckConstraint(
            "kind not in ('input', 'cost') or cost_cop is not null",
            name="ck_logbook_entry_cost_required",
        ),
        CheckConstraint(
            "kind = 'harvest' or "
            "(yield_kg is null and sold_kg is null and sale_price_cop_per_kg is null)",
            name="ck_logbook_entry_harvest_exclusive",
        ),
        CheckConstraint(
            "kind = 'task' or labor_days is null",
            name="ck_logbook_entry_task_exclusive",
        ),
        CheckConstraint(
            "kind = 'irrigation' or irrigation_mm is null",
            name="ck_logbook_entry_irrigation_exclusive",
        ),
        CheckConstraint(
            "(sold_kg is null and sale_price_cop_per_kg is null) "
            "or (sold_kg is not null and sale_price_cop_per_kg is not null)",
            name="ck_logbook_entry_sold_and_price",
        ),
        CheckConstraint(
            "sold_kg is null or sold_kg <= yield_kg",
            name="ck_logbook_entry_sold_le_yield",
        ),
        CheckConstraint(
            "(quantity is null or quantity >= 0) and (cost_cop is null or cost_cop >= 0) and "
            "(yield_kg is null or yield_kg >= 0) and (sold_kg is null or sold_kg >= 0) and "
            "(sale_price_cop_per_kg is null or sale_price_cop_per_kg >= 0) and "
            "(labor_days is null or labor_days >= 0) and "
            "(irrigation_mm is null or irrigation_mm >= 0)",
            name="ck_logbook_entry_non_negative",
        ),
        Index("ix_logbook_entry_org_server_version", "org_id", "server_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    plot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plot.id"))
    crop_cycle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crop_cycle.id"))
    kind: Mapped[str] = mapped_column(String, nullable=False)
    occurred_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    quantity: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String)
    cost_cop: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    yield_kg: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    sold_kg: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    sale_price_cop_per_kg: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    labor_days: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    irrigation_mm: Mapped[decimal.Decimal | None] = mapped_column(Numeric)
    alert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alert.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    created_offline: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    client_updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    server_version: Mapped[int] = mapped_column(
        BigInteger, server_default=sync_server_version_seq.next_value()
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ExtensionVisitRow(Base):
    """Extension visit (docs/03:249-263, 430-448; docs/06 §7).

    Topics vocabulary strictly restricted to the 5 Ley 1876 aspects.
    Shares `sync_server_version_seq` with `logbook_entry`.
    """

    __tablename__ = "extension_visit"
    __table_args__ = (
        CheckConstraint(
            "topics <@ ARRAY['human_capacities','social_capacities','information_access',"
            "'natural_resources','participation']::text[]",
            name="ck_extension_visit_topics",
        ),
        Index("ix_extension_visit_org_server_version", "org_id", "server_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"))
    plot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plot.id"))
    technician_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"))
    visited_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    recommendations: Mapped[str | None] = mapped_column(Text)
    commitments: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    client_updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    server_version: Mapped[int] = mapped_column(
        BigInteger, server_default=sync_server_version_seq.next_value()
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class AttachmentRow(Base):
    """Attachment metadata for logbook entries or extension visits (docs/03:264-271; ADR-0018).

    Guarantees exactly one parent via `num_nonnulls(logbook_entry_id, extension_visit_id) = 1`.
    """

    __tablename__ = "attachment"
    __table_args__ = (
        CheckConstraint("bytes > 0", name="ck_attachment_bytes_positive"),
        CheckConstraint(
            "num_nonnulls(logbook_entry_id, extension_visit_id) = 1",
            name="ck_attachment_parent_exactly_one",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    logbook_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("logbook_entry.id", ondelete="CASCADE")
    )
    extension_visit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extension_visit.id", ondelete="CASCADE")
    )
    object_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False)
