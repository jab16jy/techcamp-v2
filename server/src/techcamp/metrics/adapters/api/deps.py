"""FastAPI dependencies wiring metrics adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.metrics.adapters.monthly_repository import SqlAlchemyMonthlyMetricRepository
from techcamp.metrics.adapters.repositories import SqlAlchemyBaselineRepository
from techcamp.shared.db import SessionDep


async def get_baseline_repository(session: SessionDep) -> SqlAlchemyBaselineRepository:
    return SqlAlchemyBaselineRepository(session)


BaselineRepoDep = Annotated[SqlAlchemyBaselineRepository, Depends(get_baseline_repository)]


async def get_monthly_metric_repository(
    session: SessionDep,
) -> SqlAlchemyMonthlyMetricRepository:
    return SqlAlchemyMonthlyMetricRepository(session)


MonthlyMetricRepoDep = Annotated[
    SqlAlchemyMonthlyMetricRepository, Depends(get_monthly_metric_repository)
]
