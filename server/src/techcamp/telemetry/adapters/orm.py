"""SQLAlchemy table mappings for telemetry (docs/03-modelo-datos.md:136-175, 372-385, 459-475).

`reading` is a TimescaleDB hypertable (created by the migration's raw SQL,
not by this mapping) with compression and two continuous aggregates
(`reading_hourly`, `reading_daily`); this module maps only the plain
`reading` row shape, since T1 has no use case reading or writing through it
yet (ingest is T4, `raw|hour|day` queries are T5).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class NodeRow(Base):
    __tablename__ = "node"
    __table_args__ = (
        CheckConstraint("transport in ('wifi','cellular','lorawan')", name="ck_node_transport"),
        CheckConstraint(
            "status in ('provisioned','online','offline','retired')", name="ck_node_status"
        ),
        UniqueConstraint("dev_eui", name="uq_node_dev_eui"),
        UniqueConstraint("claim_code", name="uq_node_claim_code"),
        Index("ix_node_org_id", "org_id"),
        Index("ix_node_plot_id", "plot_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    plot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plot.id"), nullable=False)
    transport: Mapped[str] = mapped_column(String, nullable=False)
    dev_eui: Mapped[str | None] = mapped_column(String, nullable=True)
    claim_code: Mapped[str] = mapped_column(String, nullable=False)
    credential_hash: Mapped[str] = mapped_column(String, nullable=False)
    firmware: Mapped[str | None] = mapped_column(String, nullable=True)
    interval_s: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False)


class SensorRow(Base):
    __tablename__ = "sensor"
    __table_args__ = (UniqueConstraint("node_id", "channel_key", name="uq_sensor_node_channel"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id"), nullable=False)
    channel_key: Mapped[str] = mapped_column(String, nullable=False)
    metric: Mapped[str] = mapped_column(String, nullable=False)
    depth_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit: Mapped[str] = mapped_column(String, nullable=False)


class CalibrationRow(Base):
    __tablename__ = "calibration"
    __table_args__ = (
        CheckConstraint(
            "method in ('linear','two_point','polynomial')", name="ck_calibration_method"
        ),
        CheckConstraint("kind in ('lab','field')", name="ck_calibration_kind"),
        UniqueConstraint("sensor_id", "version", name="uq_calibration_sensor_version"),
        Index("ix_calibration_sensor_valid_from", "sensor_id", "valid_from"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey("sensor.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    rmse_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    valid_from: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReadingRow(Base):
    """The hypertable's plain row shape (docs/03-modelo-datos.md:376-385): the
    migration owns `create_hypertable`, compression and the continuous
    aggregates, none of which this mapping expresses."""

    __tablename__ = "reading"
    __table_args__ = (CheckConstraint("quality in (0,1,2)", name="ck_reading_quality"),)

    time: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    sensor_id: Mapped[int] = mapped_column(
        ForeignKey("sensor.id"), primary_key=True, nullable=False
    )
    raw_value: Mapped[float] = mapped_column(nullable=False)
    value: Mapped[float | None] = mapped_column(nullable=True)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality: Mapped[int] = mapped_column(SmallInteger, nullable=False)
