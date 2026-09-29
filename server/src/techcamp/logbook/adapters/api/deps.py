"""FastAPI dependencies wiring logbook adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.logbook.adapters.attachments import (
    Boto3Presigner,
    SqlAlchemyAttachmentRepository,
)
from techcamp.logbook.adapters.repositories import (
    PostgresSyncTransaction,
    SqlAlchemyExtensionVisitRepository,
    SqlAlchemyExtensionVisitSyncRepository,
    SqlAlchemyLogbookEntrySyncRepository,
    SqlAlchemySyncIdProbe,
)
from techcamp.logbook.application import Presigner
from techcamp.shared.config import (
    s3_access_key,
    s3_bucket,
    s3_public_url,
    s3_region,
    s3_secret_key,
)
from techcamp.shared.db import SessionDep
from techcamp.shared.errors import ProblemError


async def get_extension_visit_repository(
    session: SessionDep,
) -> SqlAlchemyExtensionVisitRepository:
    return SqlAlchemyExtensionVisitRepository(session)


VisitRepoDep = Annotated[
    SqlAlchemyExtensionVisitRepository, Depends(get_extension_visit_repository)
]


async def get_attachment_repository(session: SessionDep) -> SqlAlchemyAttachmentRepository:
    return SqlAlchemyAttachmentRepository(session)


AttachmentRepoDep = Annotated[SqlAlchemyAttachmentRepository, Depends(get_attachment_repository)]


def get_presigner() -> Presigner:
    """The signer configured for this profile, or 503 when there is none.

    The seminar profile has MinIO with the emulator's root credentials already
    in `infra/compose.yaml` (ADR-0021), so a default is safe there and nowhere
    else. Production has no default on purpose (docs/09 "Secretos"), and an
    unsigned photo cannot be offered by inventing a credential: the honest
    answer is that object storage is not configured, which is a state an
    operator can act on and a 5xx the client can retry later.

    Read per request, not once at import, so the settings follow the same
    env-driven pattern as every other `shared/config.py` reader.
    """
    access_key = s3_access_key()
    secret_key = s3_secret_key()
    if access_key is None or secret_key is None:
        raise ProblemError(
            status=503,
            title="Object storage is not configured",
            detail=(
                "Set TECHCAMP_S3_ACCESS_KEY and TECHCAMP_S3_SECRET_KEY. The seminar "
                "profile defaults them to the MinIO container (ADR-0021); production "
                "has no default (ADR-0018)."
            ),
        )
    return Boto3Presigner(
        endpoint_url=s3_public_url(),
        bucket=s3_bucket(),
        access_key=access_key,
        secret_key=secret_key,
        region=s3_region(),
    )


PresignerDep = Annotated[Presigner, Depends(get_presigner)]


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
