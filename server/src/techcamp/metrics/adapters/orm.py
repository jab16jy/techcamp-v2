"""SQLAlchemy table mappings for the metrics module (docs/03-modelo-datos.md; E11).

The metrics module owns derived tables only: the enrollment survey
(`plot_baseline`, docs/03 §`plot_baseline`), the monthly adoption index
(`plot_metric_monthly`, docs/03:438) and the per-cycle impact
(`crop_cycle_summary`, docs/03:439).

Each of them carries `org_id` because every query filters by it
(docs/09-cuellos-de-botella.md#seguridad). That `org_id` is not free text next to a
`plot_id`: each table ties the two with a composite foreign key to
`plot(id, org_id)`, the pattern `6628f7c0aa3b` introduced for `plot` -> `farm`, so a
row can never name one organization while pointing at another organization's plot.

Invariants are enforced with CHECK constraints, not only in application code
(docs/03's modeling rules). A metric with no evidence is `null` — never `0` — so
every range CHECK is written `is null or ...`.
"""

from __future__ import annotations

import datetime
import decimal
import uuid

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class PlotBaselineRow(Base):
    """The enrollment survey: how the plot produced before TechCamp, which impact is
    measured against (docs/03 §`plot_baseline`, ADR-0024). One row per plot, so the
    plot id is the primary key; `PUT` replaces it whole (D-T0.11)."""

    __tablename__ = "plot_baseline"
    __table_args__ = (
        CheckConstraint(
            "irrigation_practice in ('none','drip','sprinkler','gravity')",
            name="ck_plot_baseline_irrigation_practice",
        ),
        CheckConstraint(
            "last_yield_kg_ha is null or last_yield_kg_ha >= 0",
            name="ck_plot_baseline_last_yield_non_negative",
        ),
        CheckConstraint(
            "last_cost_cop_ha is null or last_cost_cop_ha >= 0",
            name="ck_plot_baseline_last_cost_non_negative",
        ),
        ForeignKeyConstraint(
            ["plot_id", "org_id"],
            ["plot.id", "plot.org_id"],
            name="fk_plot_baseline_plot_id_org_id",
        ),
    )

    plot_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    """No column-level FK: the composite `fk_plot_baseline_plot_id_org_id` ties it to
    `plot.id` together with `org_id`."""
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    enrolled_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    crop_id: Mapped[int] = mapped_column(ForeignKey("crop.id"), nullable=False)
    """Crop of the last cycle, from the global catalog (docs/03:243)."""
    last_yield_kg_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    last_cost_cop_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Approximate figures the farmer may not know (docs/03:245)."""
    irrigation_practice: Mapped[str] = mapped_column(String, nullable=False)
    """Same closed vocabulary as `plot.irrigation_system` (docs/03:246)."""
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    """Whoever saved the survey last; `PUT` replaces the row, so this moves (D-T0.11)."""
