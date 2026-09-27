"""FastAPI dependencies wiring irrigation adapters into requests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.shared.db import SessionDep


async def get_water_balance_repository(session: SessionDep) -> SqlAlchemyWaterBalanceRepository:
    return SqlAlchemyWaterBalanceRepository(session)


async def get_irrigation_recommendation_repository(
    session: SessionDep,
) -> SqlAlchemyIrrigationRecommendationRepository:
    return SqlAlchemyIrrigationRecommendationRepository(session)


def get_now() -> datetime:
    """The current UTC clock dependency, overridable in tests."""
    return datetime.now(UTC)


WaterBalanceRepoDep = Annotated[
    SqlAlchemyWaterBalanceRepository, Depends(get_water_balance_repository)
]
RecommendationRepoDep = Annotated[
    SqlAlchemyIrrigationRecommendationRepository,
    Depends(get_irrigation_recommendation_repository),
]
NowDep = Annotated[datetime, Depends(get_now)]
