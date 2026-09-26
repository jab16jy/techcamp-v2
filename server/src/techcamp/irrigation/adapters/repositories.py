"""Postgres repositories for irrigation (docs/03-modelo-datos.md:192-215).

`water_balance_daily` and `irrigation_recommendation` persistence:
- Job-side writes and previous-day read are org-agnostic
  (following CropCycleRepository.get_active_for_plot).
- User-exposed reads join through `plot` to filter by `PlotRow.org_id`
  (docs/09-cuellos-de-botella.md:71).
- Floats in the domain, Decimal only at the ORM boundary.
"""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import PlotRow
from techcamp.irrigation.adapters.orm import IrrigationRecommendationRow, WaterBalanceDailyRow
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    RainfedAdvice,
    RecommendationKind,
    WaterBalanceDay,
)
from techcamp.shared.ids import uuid7


def _as_decimal(value: float | decimal.Decimal | None) -> decimal.Decimal | None:
    """Exact decimal for numeric columns. Floats bound via str preserve intended digits."""
    if value is None:
        return None
    return value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))


def _balance_from_row(row: WaterBalanceDailyRow) -> WaterBalanceDay:
    return WaterBalanceDay(
        plot_id=row.plot_id,
        day=row.day,
        etc_mm=float(row.etc_mm),
        effective_rain_mm=float(row.effective_rain_mm),
        irrigation_mm=float(row.irrigation_mm),
        taw_mm=float(row.taw_mm),
        raw_mm=float(row.raw_mm),
        depletion_model_mm=float(row.depletion_model_mm),
        depletion_mm=float(row.depletion_mm),
        soil_moisture_obs_pct=(
            float(row.soil_moisture_obs_pct) if row.soil_moisture_obs_pct is not None else None
        ),
        assimilation_k=float(row.assimilation_k),
        stress_moisture_pct=float(row.stress_moisture_pct),
    )


def _recommendation_from_row(row: IrrigationRecommendationRow) -> IrrigationRecommendation:
    raw_advice: Sequence[Any] = row.advice or ()
    parsed_advice: tuple[RainfedAdvice, ...] = tuple(
        RainfedAdvice(a) for a in raw_advice if a in RainfedAdvice._value2member_map_
    )
    return IrrigationRecommendation(
        kind=RecommendationKind(row.kind),
        depth_mm=float(row.depth_mm) if row.depth_mm is not None else None,
        duration_min=row.duration_min,
        advice=parsed_advice,
        rationale=row.rationale or {},
        id=row.id,
        plot_id=row.plot_id,
        day=row.day,
    )


class SqlAlchemyWaterBalanceRepository:
    """Postgres repository for daily water balance (docs/03:192-205)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, balance: WaterBalanceDay) -> None:
        """Store one day of water balance, updating in place on conflict.

        The 04:30 daily job writes each plot's day; re-runs overwrite in place.
        """
        stmt = pg_insert(WaterBalanceDailyRow).values(
            plot_id=balance.plot_id,
            day=balance.day,
            etc_mm=_as_decimal(balance.etc_mm),
            effective_rain_mm=_as_decimal(balance.effective_rain_mm),
            irrigation_mm=_as_decimal(balance.irrigation_mm),
            taw_mm=_as_decimal(balance.taw_mm),
            raw_mm=_as_decimal(balance.raw_mm),
            depletion_model_mm=_as_decimal(balance.depletion_model_mm),
            depletion_mm=_as_decimal(balance.depletion_mm),
            soil_moisture_obs_pct=_as_decimal(balance.soil_moisture_obs_pct),
            assimilation_k=_as_decimal(balance.assimilation_k),
            stress_moisture_pct=_as_decimal(balance.stress_moisture_pct),
        )
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[WaterBalanceDailyRow.plot_id, WaterBalanceDailyRow.day],
            set_={
                "etc_mm": excluded.etc_mm,
                "effective_rain_mm": excluded.effective_rain_mm,
                "irrigation_mm": excluded.irrigation_mm,
                "taw_mm": excluded.taw_mm,
                "raw_mm": excluded.raw_mm,
                "depletion_model_mm": excluded.depletion_model_mm,
                "depletion_mm": excluded.depletion_mm,
                "soil_moisture_obs_pct": excluded.soil_moisture_obs_pct,
                "assimilation_k": excluded.assimilation_k,
                "stress_moisture_pct": excluded.stress_moisture_pct,
            },
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def get_for_plot(
        self, plot_id: UUID, day: datetime.date, org_id: UUID | None = None
    ) -> WaterBalanceDay | None:
        """Read a plot's balance for a specific day.

        When org_id is None, this is org-agnostic (used by the 04:30 daily job to read
        the previous day's balance like CropCycleRepository.get_active_for_plot).
        When org_id is passed, enforces organization isolation by joining PlotRow.org_id.
        """
        stmt = select(WaterBalanceDailyRow).where(
            WaterBalanceDailyRow.plot_id == plot_id,
            WaterBalanceDailyRow.day == day,
        )
        if org_id is not None:
            stmt = stmt.join(PlotRow, PlotRow.id == WaterBalanceDailyRow.plot_id).where(
                PlotRow.org_id == org_id
            )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _balance_from_row(row) if row is not None else None

    async def list_for_plot(
        self, plot_id: UUID, org_id: UUID, from_day: datetime.date, to_day: datetime.date
    ) -> list[WaterBalanceDay]:
        """List water balance days for a plot in [from_day, to_day], ordered by day ascending.

        User-facing read: enforces organization isolation by joining PlotRow.org_id == org_id
        (docs/09-cuellos-de-botella.md:71).
        """
        stmt = (
            select(WaterBalanceDailyRow)
            .join(PlotRow, PlotRow.id == WaterBalanceDailyRow.plot_id)
            .where(
                WaterBalanceDailyRow.plot_id == plot_id,
                PlotRow.org_id == org_id,
                WaterBalanceDailyRow.day >= from_day,
                WaterBalanceDailyRow.day <= to_day,
            )
            .order_by(WaterBalanceDailyRow.day)
        )
        result = await self._session.execute(stmt)
        return [_balance_from_row(row) for row in result.scalars()]


class SqlAlchemyIrrigationRecommendationRepository:
    """Postgres repository for irrigation recommendations (docs/03:206-215)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        recommendation: IrrigationRecommendation,
        *,
        plot_id: UUID,
        day: datetime.date,
        recommendation_id: UUID | None = None,
    ) -> IrrigationRecommendation:
        """Upsert recommendation for (plot_id, day).

        Updates existing decision in place if re-run on the same day.
        """
        rec_id = recommendation.id or recommendation_id or uuid7()
        advice_list = [
            a.value if isinstance(a, RainfedAdvice) else str(a) for a in recommendation.advice
        ]
        kind_str = (
            recommendation.kind.value
            if isinstance(recommendation.kind, RecommendationKind)
            else str(recommendation.kind)
        )
        insert_stmt = pg_insert(IrrigationRecommendationRow).values(
            id=rec_id,
            plot_id=plot_id,
            day=day,
            kind=kind_str,
            depth_mm=_as_decimal(recommendation.depth_mm),
            duration_min=recommendation.duration_min,
            advice=advice_list,
            rationale=dict(recommendation.rationale),
        )
        excluded = insert_stmt.excluded
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[
                IrrigationRecommendationRow.plot_id,
                IrrigationRecommendationRow.day,
            ],
            set_={
                "kind": excluded.kind,
                "depth_mm": excluded.depth_mm,
                "duration_min": excluded.duration_min,
                "advice": excluded.advice,
                "rationale": excluded.rationale,
            },
        ).returning(IrrigationRecommendationRow)
        result = await self._session.execute(upsert_stmt)
        await self._session.commit()
        saved_row = result.scalar_one()
        return _recommendation_from_row(saved_row)

    async def get_for_plot(
        self, plot_id: UUID, day: datetime.date, org_id: UUID | None = None
    ) -> IrrigationRecommendation | None:
        """Get irrigation recommendation for a plot and day.

        When org_id is provided, enforces organization isolation by joining PlotRow.org_id == org_id
        (docs/09-cuellos-de-botella.md:71).
        """
        stmt = select(IrrigationRecommendationRow).where(
            IrrigationRecommendationRow.plot_id == plot_id,
            IrrigationRecommendationRow.day == day,
        )
        if org_id is not None:
            stmt = stmt.join(PlotRow, PlotRow.id == IrrigationRecommendationRow.plot_id).where(
                PlotRow.org_id == org_id
            )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return _recommendation_from_row(row) if row is not None else None
