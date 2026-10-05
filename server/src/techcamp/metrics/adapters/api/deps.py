"""FastAPI dependencies wiring metrics adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.metrics.adapters.cycle_summary_repository import (
    SqlAlchemyCycleSummaryRepository,
)
from techcamp.metrics.adapters.monthly_repository import SqlAlchemyMonthlyMetricRepository
from techcamp.metrics.adapters.repositories import SqlAlchemyBaselineRepository
from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository
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


async def get_cycle_summary_repository(
    session: SessionDep,
) -> SqlAlchemyCycleSummaryRepository:
    return SqlAlchemyCycleSummaryRepository(session)


CycleSummaryRepoDep = Annotated[
    SqlAlchemyCycleSummaryRepository, Depends(get_cycle_summary_repository)
]


async def get_metrics_source_repository(
    session: SessionDep,
) -> SqlAlchemyMetricsSourceRepository:
    return SqlAlchemyMetricsSourceRepository(session)


MetricsSourceRepoDep = Annotated[
    SqlAlchemyMetricsSourceRepository, Depends(get_metrics_source_repository)
]
"""The read-only views, needed only by the active-cycle branch of the cycle
summary: a stored row is read from `crop_cycle_summary` and never recomputed."""
