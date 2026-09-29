"""FastAPI dependencies wiring logbook adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.logbook.adapters.repositories import SqlAlchemyExtensionVisitRepository
from techcamp.shared.db import SessionDep


async def get_extension_visit_repository(
    session: SessionDep,
) -> SqlAlchemyExtensionVisitRepository:
    return SqlAlchemyExtensionVisitRepository(session)


VisitRepoDep = Annotated[
    SqlAlchemyExtensionVisitRepository, Depends(get_extension_visit_repository)
]
