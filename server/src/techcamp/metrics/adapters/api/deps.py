"""FastAPI dependencies wiring metrics adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.metrics.adapters.repositories import SqlAlchemyBaselineRepository
from techcamp.shared.db import SessionDep


async def get_baseline_repository(session: SessionDep) -> SqlAlchemyBaselineRepository:
    return SqlAlchemyBaselineRepository(session)


BaselineRepoDep = Annotated[SqlAlchemyBaselineRepository, Depends(get_baseline_repository)]
