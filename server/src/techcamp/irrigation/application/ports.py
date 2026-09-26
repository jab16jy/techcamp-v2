"""Application ports for the irrigation module.

Hexagonal layering per ADR-0002 and ADR-0003: application defines the interfaces
it needs, adapters implement them.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from techcamp.irrigation.domain.models import IrrigationRecommendation, WaterBalanceDay


class WaterBalanceRepository(Protocol):
    async def upsert(self, balance: WaterBalanceDay) -> None:
        """Upsert daily water balance row for (plot_id, day).

        Job-side write: idempotent under concurrency via ON CONFLICT (plot_id, day).
        """
        ...

    async def get_for_plot(
        self, plot_id: UUID, day: date, org_id: UUID | None = None
    ) -> WaterBalanceDay | None:
        """Get water balance day for a plot and date.

        When org_id is None, this is org-agnostic (used by the 04:30 daily job to read
        the previous day's balance, following CropCycleRepository.get_active_for_plot).
        When org_id is provided, enforces organization isolation by joining plot.org_id.
        """
        ...

    async def list_for_plot(
        self, plot_id: UUID, org_id: UUID, from_day: date, to_day: date
    ) -> list[WaterBalanceDay]:
        """List water balance days for a plot in inclusive [from_day, to_day], ordered by day.

        User-facing read: enforces organization isolation by joining PlotRow.org_id == org_id
        (docs/09-cuellos-de-botella.md:71).
        """
        ...


class IrrigationRecommendationRepository(Protocol):
    async def upsert(
        self,
        recommendation: IrrigationRecommendation,
        *,
        plot_id: UUID,
        day: date,
        recommendation_id: UUID | None = None,
    ) -> IrrigationRecommendation:
        """Upsert recommendation for (plot_id, day).

        Job-side write: idempotent under concurrency via ON CONFLICT (plot_id, day).
        """
        ...

    async def get_for_plot(
        self, plot_id: UUID, day: date, org_id: UUID | None = None
    ) -> IrrigationRecommendation | None:
        """Get irrigation recommendation for a plot and date.

        When org_id is provided, enforces organization isolation by joining PlotRow.org_id == org_id
        (docs/09-cuellos-de-botella.md:71).
        """
        ...
