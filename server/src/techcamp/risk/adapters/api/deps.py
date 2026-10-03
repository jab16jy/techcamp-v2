"""FastAPI dependencies wiring risk adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.shared.db import SessionDep


async def get_risk_repository(session: SessionDep) -> SqlAlchemyRiskRepository:
    return SqlAlchemyRiskRepository(session)


RiskRepoDep = Annotated[SqlAlchemyRiskRepository, Depends(get_risk_repository)]
