"""FastAPI dependencies wiring alerts adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.shared.db import SessionDep


async def get_alert_repository(session: SessionDep) -> SqlAlchemyAlertRepository:
    return SqlAlchemyAlertRepository(session)


async def get_alert_rule_repository(session: SessionDep) -> SqlAlchemyAlertRuleRepository:
    return SqlAlchemyAlertRuleRepository(session)


AlertRepoDep = Annotated[SqlAlchemyAlertRepository, Depends(get_alert_repository)]
AlertRuleRepoDep = Annotated[SqlAlchemyAlertRuleRepository, Depends(get_alert_rule_repository)]
