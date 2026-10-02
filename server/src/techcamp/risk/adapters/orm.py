"""SQLAlchemy table mappings for climate risk (docs/03-modelo-datos.md
§`municipality`, `model_version` y `risk_prediction`: riesgo climático (E10)).

Neither table carries `org_id`: a prediction is a property of a *cell*, and a
cell is shared reference data — the same row for every organization
(docs/09-cuellos-de-botella.md:39, docs/03-modelo-datos.md:39). Isolation is
enforced where this data leaves the server, at the plot endpoint (T6b).

The CHECKs and the two unique constraints are the documented decisions, not
defensive extras: the `flood|drought` and `low|high|critical` vocabularies, the
probability range, one promoted version per event, and one prediction per
`(cell, event, month, version)` so the daily job is idempotent (docs/06 §8).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class ModelVersionRow(Base):
    """A registered model or baseline (docs/03-modelo-datos.md:319-333).

    `is_baseline` rows are the served fallback when no model passes the gate
    (docs/08 §M2 "Línea base servida"), and `thresholds` is why the severity
    belongs to the version: calibrating an operating point is part of the
    model, not of the server (docs/03 §Umbrales).
    """

    __tablename__ = "model_version"
    __table_args__ = (
        Index(
            "uq_model_version_promoted_name",
            "name",
            unique=True,
            postgresql_where=text("promoted"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    baseline_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    dataset_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    git_commit: Mapped[str | None] = mapped_column(String, nullable=True)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    promotion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskPredictionRow(Base):
    """One cell's risk for one event and one month
    (docs/03-modelo-datos.md:335-347).

    The unique key is what makes the daily job idempotent: a month, an event and
    a cell are predicted once, and promoting another version adds its row
    without deleting the one being served (docs/03 §Unicidad de la predicción).
    """

    __tablename__ = "risk_prediction"
    __table_args__ = (
        CheckConstraint("event_type in ('flood', 'drought')", name="ck_risk_prediction_event_type"),
        CheckConstraint(
            "severity in ('low', 'high', 'critical')", name="ck_risk_prediction_severity"
        ),
        CheckConstraint(
            "probability >= 0 and probability <= 1", name="ck_risk_prediction_probability"
        ),
        UniqueConstraint(
            "cell_id",
            "event_type",
            "horizon_start",
            "model_version_id",
            name="uq_risk_prediction_cell_event_month_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    cell_id: Mapped[int] = mapped_column(ForeignKey("weather_cell.id"), nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_version.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    horizon_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    top_factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
