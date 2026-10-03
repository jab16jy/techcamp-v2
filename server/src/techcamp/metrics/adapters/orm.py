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
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
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
        CheckConstraint("last_yield_kg_ha >= 0", name="ck_plot_baseline_last_yield_non_negative"),
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
    last_yield_kg_ha: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    """Required by `PUT /plots/{plot_id}/baseline` (docs/04-api.md:52): only
    `last_cost_cop_ha` is optional. docs/03:245's "aproximado" annotates the cost, not
    the yield — this is the figure impact is measured against."""
    last_cost_cop_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """An approximate figure the farmer may not know, so `null` and never `0`."""
    irrigation_practice: Mapped[str] = mapped_column(String, nullable=False)
    """Same closed vocabulary as `plot.irrigation_system` (docs/03:246)."""
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    """Whoever saved the survey last; `PUT` replaces the row, so this moves (D-T0.11)."""


class PlotMetricMonthlyRow(Base):
    """The adoption index and its four components for one calendar month
    (docs/03:438, docs/11 §2, D-T0.2). The month is the first day of the month, so
    the key is the bucket itself."""

    __tablename__ = "plot_metric_monthly"
    __table_args__ = (
        CheckConstraint(
            "monitoring is null or (monitoring >= 0 and monitoring <= 1)",
            name="ck_plot_metric_monthly_monitoring_range",
        ),
        CheckConstraint(
            "record_keeping is null or (record_keeping >= 0 and record_keeping <= 1)",
            name="ck_plot_metric_monthly_record_keeping_range",
        ),
        CheckConstraint(
            "decision is null or (decision >= 0 and decision <= 1)",
            name="ck_plot_metric_monthly_decision_range",
        ),
        CheckConstraint(
            "risk_management is null or (risk_management >= 0 and risk_management <= 1)",
            name="ck_plot_metric_monthly_risk_management_range",
        ),
        CheckConstraint(
            "digital_adoption_index is null or "
            "(digital_adoption_index >= 0 and digital_adoption_index <= 100)",
            name="ck_plot_metric_monthly_index_range",
        ),
        CheckConstraint(
            "extract(day from month) = 1", name="ck_plot_metric_monthly_month_is_first_of_month"
        ),
        ForeignKeyConstraint(
            ["plot_id", "org_id"],
            ["plot.id", "plot.org_id"],
            name="fk_plot_metric_monthly_plot_id_org_id",
        ),
        # docs/04 §`GET /organizations/{org_id}/metrics?month=`: the org-month
        # listing behind `OrgMetrics` (D-T0.12).
        Index("ix_plot_metric_monthly_org_month", "org_id", "month"),
    )

    plot_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    month: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    monitoring: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    record_keeping: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    decision: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Null in a rainfed plot: there is no applied depth to follow (docs/11:52)."""
    risk_management: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """A component with no evidence is null, never 0 (D-T0.3)."""
    digital_adoption_index: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Null when all four components are null; otherwise 100 points split evenly over
    the non-null ones (D-T0.3)."""
    computed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CropCycleSummaryRow(Base):
    """The impact of one finished cycle (`harvested` or `lost`; an active cycle is
    computed on read and not stored, D-T0.8). Every metric is nullable: no applied
    water in a rainfed plot, no comparison without an enrollment survey,
    `relative_yield` null until `field_record` exists (D-T0.9)."""

    __tablename__ = "crop_cycle_summary"
    __table_args__ = (
        CheckConstraint(
            "yield_kg_ha is null or yield_kg_ha >= 0",
            name="ck_crop_cycle_summary_yield_kg_ha_non_negative",
        ),
        CheckConstraint(
            "relative_yield is null or relative_yield >= 0",
            name="ck_crop_cycle_summary_relative_yield_non_negative",
        ),
        CheckConstraint(
            "water_applied_m3_ha is null or water_applied_m3_ha >= 0",
            name="ck_crop_cycle_summary_water_applied_m3_ha_non_negative",
        ),
        CheckConstraint(
            "irrigation_wue_kg_m3 is null or irrigation_wue_kg_m3 >= 0",
            name="ck_crop_cycle_summary_irrigation_wue_kg_m3_non_negative",
        ),
        CheckConstraint(
            "water_stress_days is null or water_stress_days >= 0",
            name="ck_crop_cycle_summary_water_stress_days_non_negative",
        ),
        CheckConstraint(
            "cost_cop_ha is null or cost_cop_ha >= 0",
            name="ck_crop_cycle_summary_cost_cop_ha_non_negative",
        ),
        CheckConstraint(
            "cost_cop_kg is null or cost_cop_kg >= 0",
            name="ck_crop_cycle_summary_cost_cop_kg_non_negative",
        ),
        CheckConstraint(
            "yield_kg_per_labor_day is null or yield_kg_per_labor_day >= 0",
            name="ck_crop_cycle_summary_yield_kg_per_labor_day_non_negative",
        ),
        CheckConstraint(
            "loss_kg is null or loss_kg >= 0", name="ck_crop_cycle_summary_loss_kg_non_negative"
        ),
        CheckConstraint(
            "loss_cop is null or loss_cop >= 0",
            name="ck_crop_cycle_summary_loss_cop_non_negative",
        ),
        # Each of these ten names is generated by the migration from its column name
        # (`f'ck_crop_cycle_summary_{name}_non_negative'` over `_NON_NEGATIVE_METRICS`),
        # so they are spelled out here exactly as the migration builds them (#243) —
        # a hand-shortened name is a constraint the ORM thinks exists and the database
        # never created.
        #
        # `yield_change_vs_baseline` and `gross_margin_cop` have no range CHECK on
        # purpose: a cycle can yield less than the enrollment survey and can cost
        # more than it earns (docs/11 §1).
        ForeignKeyConstraint(
            ["plot_id", "org_id"],
            ["plot.id", "plot.org_id"],
            name="fk_crop_cycle_summary_plot_id_org_id",
        ),
        # The cycle belongs to the plot: without this, one row could report another
        # plot's metrics for a cycle of this plot.
        ForeignKeyConstraint(
            ["crop_cycle_id", "plot_id"],
            ["crop_cycle.id", "crop_cycle.plot_id"],
            name="fk_crop_cycle_summary_crop_cycle_id_plot_id",
        ),
    )

    crop_cycle_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    """No column-level FK: `fk_crop_cycle_summary_crop_cycle_id_plot_id` ties it to
    `crop_cycle.id` together with `plot_id`, so a summary cannot report another
    plot's metrics for this cycle."""
    plot_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    """No column-level FK: the composite `fk_crop_cycle_summary_plot_id_org_id` ties
    it to `plot.id` together with `org_id`."""
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organization.id"), nullable=False)
    yield_kg_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    yield_change_vs_baseline: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Null when there is no enrollment survey or the crop differs (docs/11:22)."""
    relative_yield: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    water_applied_m3_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    irrigation_wue_kg_m3: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    water_stress_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """A count of days with `Ks < 1` (docs/11 §1), so a whole number."""
    cost_cop_ha: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    cost_cop_kg: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    yield_kg_per_labor_day: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    gross_margin_cop: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    loss_kg: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    """Sum of the alert observations' `quantity`, in kg (docs/03:420)."""
    loss_cop: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    computed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
