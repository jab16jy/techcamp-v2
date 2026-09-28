"""FastAPI dependencies wiring the logbook sync adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.logbook.adapters.repositories import (
    PostgresSyncTransaction,
    SqlAlchemyExtensionVisitSyncRepository,
    SqlAlchemyLogbookEntrySyncRepository,
    SqlAlchemySyncIdProbe,
)
from techcamp.shared.db import SessionDep


async def get_sync_transaction(session: SessionDep) -> PostgresSyncTransaction:
    """The request's transaction: one D1 lock for the whole batch, so a
    version is only ever allocated by a transaction that is about to commit."""
    return PostgresSyncTransaction(session)


async def get_sync_id_probe(session: SessionDep) -> SqlAlchemySyncIdProbe:
    return SqlAlchemySyncIdProbe(session)


async def get_entry_sync_repository(session: SessionDep) -> SqlAlchemyLogbookEntrySyncRepository:
    return SqlAlchemyLogbookEntrySyncRepository(session)


async def get_visit_sync_repository(session: SessionDep) -> SqlAlchemyExtensionVisitSyncRepository:
    return SqlAlchemyExtensionVisitSyncRepository(session)


SyncTransactionDep = Annotated[PostgresSyncTransaction, Depends(get_sync_transaction)]
SyncIdProbeDep = Annotated[SqlAlchemySyncIdProbe, Depends(get_sync_id_probe)]
EntrySyncRepoDep = Annotated[
    SqlAlchemyLogbookEntrySyncRepository, Depends(get_entry_sync_repository)
]
VisitSyncRepoDep = Annotated[
    SqlAlchemyExtensionVisitSyncRepository, Depends(get_visit_sync_repository)
]
