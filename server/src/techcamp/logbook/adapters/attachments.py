"""Storage adapters for logbook photos (docs/03 §attachment; ADR-0018; D8).

Two collaborators of the presign use case: the `attachment` rows, and the
S3-compatible store that signs the upload. The API never receives photo bytes
(ADR-0018), so nothing here moves an object — it only names one.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import boto3
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.logbook.adapters.orm import AttachmentRow, ExtensionVisitRow, LogbookEntryRow
from techcamp.logbook.domain.attachments import Attachment
from techcamp.logbook.domain.models import SyncEntity


class SqlAlchemyAttachmentRepository:
    """Attachment rows, every query scoped by `org_id` (docs/09)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_parent_org_id(
        self, *, entity: SyncEntity, parent_id: UUID, org_ids: Sequence[UUID]
    ) -> UUID | None:
        """The parent entity's org, only when the caller is a member of it.

        `deleted_at IS NULL` is part of the same read because a tombstoned
        parent is not somewhere a photo can be added (D8), and answering from
        a row the caller may not see would turn this into a probe.
        """
        if entity is SyncEntity.LOGBOOK_ENTRY:
            stmt = select(LogbookEntryRow.org_id).where(
                LogbookEntryRow.id == parent_id,
                LogbookEntryRow.org_id.in_(org_ids),
                LogbookEntryRow.deleted_at.is_(None),
            )
        else:
            stmt = select(ExtensionVisitRow.org_id).where(
                ExtensionVisitRow.id == parent_id,
                ExtensionVisitRow.org_id.in_(org_ids),
                ExtensionVisitRow.deleted_at.is_(None),
            )
        org_id: UUID | None = await self._session.scalar(stmt)
        return org_id

    async def add(self, attachment: Attachment) -> None:
        """Insert the row D8 says presign creates, and commit it.

        One row, one commit: there is no confirm step, so the row existing is
        what the client and the cleanup job read.
        """
        self._session.add(
            AttachmentRow(
                id=attachment.id,
                logbook_entry_id=attachment.logbook_entry_id,
                extension_visit_id=attachment.extension_visit_id,
                object_key=attachment.object_key,
                content_type=attachment.content_type,
                bytes=attachment.bytes,
            )
        )
        await self._session.commit()


class Boto3Presigner:
    """SigV4 presigned `PUT` against any S3-compatible store (ADR-0018; ADR-0021).

    `endpoint_url` is the PUBLIC url the browser reaches, not the in-network
    one, because the URL is signed for whoever uses it and that is the farmer's
    phone (ADR-0021: MinIO in the seminar profile, a paid provider in
    production, same code path).

    Addressing is path style so the public host survives into the URL: the
    virtual-hosted style would hand the browser `logbook-photos.localhost:9000`,
    which does not resolve. The region is signed in even for the local emulator
    because SigV4 covers it.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def presign_put(self, *, object_key: str, content_type: str, expires_in: int) -> str:
        """Sign the `PUT` locally: no request is sent, so nothing can time out.

        boto3 ships no type marker, so the URL's type is stated here instead of
        being read off an untyped call; the test that follows asserts what that
        URL actually is.
        """
        url: str = self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": object_key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )
        return url
