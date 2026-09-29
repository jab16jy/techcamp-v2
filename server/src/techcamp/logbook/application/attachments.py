"""Photo upload presign (docs/04 §Bitácora "Fotos"; D8; ADR-0018).

The client compresses a photo, asks for a URL, and PUTs the bytes straight to
object storage. The API stores the `object_key` and never sees the bytes
(ADR-0018), so this use case's whole job is to decide WHO may sign a URL for
WHICH parent and to write the row that says a photo was offered.

The two ports sit beside this use case rather than in `application/ports.py`:
each belongs to one use case, and a growing shared module is where port
definitions go to be discovered only at import time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from techcamp.identity.application.ports import MembershipRepository
from techcamp.logbook.domain.attachments import (
    PRESIGN_EXPIRY_SECONDS,
    Attachment,
    ensure_valid_photo,
    photo_object_key,
)
from techcamp.logbook.domain.errors import AttachmentParentNotFoundError
from techcamp.logbook.domain.models import SyncEntity, ensure_can_sync
from techcamp.shared.ids import uuid7


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    """What the client needs to finish an upload: the URL, and the key to keep."""

    upload_url: str
    object_key: str


class Presigner(Protocol):
    """The S3-compatible store that signs an upload (ADR-0018; ADR-0021).

    External I/O no test may reach, which is what a port is for: the use case
    is exercised with a double, while the adapter's own test signs for real —
    presigning is a local signature with no socket involved, so there is
    nothing to stub and nothing to wait for.
    """

    async def presign_put(self, *, object_key: str, content_type: str, expires_in: int) -> str:
        """Sign a `PUT` of `object_key` and return the URL the browser uses.

        The content type is part of the signature, so the upload cannot be a
        different type than the row says (ADR-0018 keeps the bucket to images).
        """
        ...


class AttachmentRepository(Protocol):
    """Attachment persistence, every query scoped by `org_id` (docs/09)."""

    async def find_parent_org_id(
        self, *, entity: SyncEntity, parent_id: UUID, org_ids: Sequence[UUID]
    ) -> UUID | None:
        """The parent entity's org when it is one of `org_ids` and not deleted.

        `org_ids` is the caller's own set: a parent outside it is reported as
        absent, so this query cannot become a way to probe another org's ids.
        """
        ...

    async def add(self, attachment: Attachment) -> None:
        """Write the row and commit it."""
        ...


async def presign_photo(
    *,
    user_id: UUID,
    entity: SyncEntity,
    parent_id: UUID,
    content_type: str,
    size: int,
    attachments: AttachmentRepository,
    memberships: MembershipRepository,
    presigner: Presigner,
) -> PresignedUpload:
    """Create the `attachment` row and return the URL the client uploads to.

    Four refusals, in the order the request is judged (docs/04 §Bitácora "Fotos";
    D8; docs/09):

    1. `InvalidAttachmentError` (422) for a size outside 1..200 KB or a content
       type that is neither `image/jpeg` nor `image/webp`. Checked first
       because it is pure: a 422 says nothing about which parents exist, and no
       query is spent on a request that cannot succeed.
    2. `AttachmentParentNotFoundError` (404) when the entry or visit is in no
       org the caller belongs to, or is deleted. D8 wants the parent already
       synced; the tombstone is not somewhere a photo can be added.
    3. `InsufficientRoleError` (403) from `ensure_can_sync`, the D3 mapping: the
       roles that may write the entity may attach to it, so a producer attaches
       an entry and only a technician a visit. 403 and not 404 because
       membership — the thing 404 protects — already passed.
    """
    photo_type = ensure_valid_photo(content_type=content_type, size=size)

    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    org_id = await attachments.find_parent_org_id(
        entity=entity, parent_id=parent_id, org_ids=list(roles_by_org)
    )
    if org_id is None:
        raise AttachmentParentNotFoundError(parent_id)

    # `find_parent_org_id` answers with one of the orgs it was given or with
    # nothing, so this is the membership of the org that owns the parent.
    ensure_can_sync(entity, roles_by_org[org_id])

    photo_id = uuid7()
    object_key = photo_object_key(
        org_id=org_id, parent_id=parent_id, photo_id=photo_id, content_type=photo_type
    )
    # Signed before the row exists: a presigner that cannot sign leaves nothing
    # claiming an upload that was never offered (D8's row exists from presign).
    upload_url = await presigner.presign_put(
        object_key=object_key, content_type=photo_type.value, expires_in=PRESIGN_EXPIRY_SECONDS
    )
    await attachments.add(
        Attachment(
            id=photo_id,
            logbook_entry_id=parent_id if entity is SyncEntity.LOGBOOK_ENTRY else None,
            extension_visit_id=parent_id if entity is SyncEntity.EXTENSION_VISIT else None,
            object_key=object_key,
            content_type=photo_type.value,
            bytes=size,
        )
    )
    return PresignedUpload(upload_url=upload_url, object_key=object_key)
