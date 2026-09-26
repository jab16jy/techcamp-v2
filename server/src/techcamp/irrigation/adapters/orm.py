"""SQLAlchemy table mappings for irrigation (docs/03-modelo-datos.md:192-215).

Plain Postgres tables, no hypertable: `water_balance_daily` holds one row per plot
and day (evaluated at 04:30 America/Bogota), which is an ordinary relation read
by date range and by the next day's job.

Access is gated through the plot (`plot_id` FK to `plot.id`), which carries `org_id`
(docs/09-cuellos-de-botella.md:71).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class WaterBalanceDailyRow(Base):
    """Daily root zone water balance for a plot (docs/03-modelo-datos.md:192-205).

    The primary key is `(plot_id, day)`.
    """

    __tablename__ = "water_balance_daily"

    plot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plot.id"), primary_key=True)
    day: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    etc_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    effective_rain_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    irrigation_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    taw_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    raw_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    depletion_model_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    depletion_mm: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    soil_moisture_obs_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    assimilation_k: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    stress_moisture_pct: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)


class IrrigationRecommendationRow(Base):
    """Daily irrigation decision outcome for a plot (docs/03-modelo-datos.md:206-215).

    `uq_irrigation_recommendation_plot_day` makes daily upsert idempotent under concurrency.
    CHECK constraints ensure kind matches domain vocabulary and non-irrigate kinds have null
    depth and duration.
    """

    __tablename__ = "irrigation_recommendation"
    __table_args__ = (
        UniqueConstraint("plot_id", "day", name="uq_irrigation_recommendation_plot_day"),
        CheckConstraint(
            "kind in ('irrigate','postpone','not_needed','no_kc','rainfed')",
            name="ck_irrigation_recommendation_kind",
        ),
        CheckConstraint(
            "kind = 'irrigate' or (depth_mm is null and duration_min is null)",
            name="ck_irrigation_recommendation_depth_null_unless_irrigate",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    plot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plot.id"), nullable=False)
    day: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    depth_mm: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    advice: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
