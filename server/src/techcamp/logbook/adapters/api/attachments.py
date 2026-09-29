"""Photo upload presign endpoint (docs/04 §Bitácora "Fotos"; D8; ADR-0018).

POST /api/v1/attachments:presign

The router carries the HTTP shape only: pydantic in, domain errors mapped to
`problem+json` out. There is no confirm endpoint — the `attachment` row is
created here, and an upload that never lands is an orphan the cleanup job owns
(ADR-0018).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.logbook.adapters.api.deps import AttachmentRepoDep, PresignerDep
from techcamp.logbook.application import presign_photo
from techcamp.logbook.domain.errors import (
    AttachmentParentNotFoundError,
    InsufficientRoleError,
    InvalidAttachmentError,
)
from techcamp.logbook.domain.models import SyncEntity
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["logbook"])


class PresignRequest(BaseModel):
    """`{ logbook_entry_id | extension_visit_id, content_type, bytes }`.

    Both parent ids are left unconstrained on purpose: FastAPI's own 422 is not
    `problem+json` (docs/04's error convention), so the router decides the
    "exactly one parent" rule and answers in the same shape as every other
    error. `content_type` and `bytes` are plain types for the same reason — the
    domain owns the 200 KB ceiling and the closed photo vocabulary (D8).
    """

    logbook_entry_id: UUID | None = None
    extension_visit_id: UUID | None = None
    content_type: str
    bytes: int


class PresignResponse(BaseModel):
    """`{ upload_url, object_key }`: the client PUTs to the first and keeps the
    second, which is the only thing the server stores (ADR-0018)."""

    upload_url: str
    object_key: str


def _parent_of(payload: PresignRequest) -> tuple[SyncEntity, UUID]:
    """The one parent the request names, or 422.

    docs/03 §attachment's CHECK is `attachment` has exactly one of
    `logbook_entry_id` / `extension_visit_id`; a request naming neither or both
    cannot become a row, so it is refused here instead of at the database.
    """
    entry_id = payload.logbook_entry_id
    visit_id = payload.extension_visit_id
    if entry_id is not None and visit_id is None:
        return SyncEntity.LOGBOOK_ENTRY, entry_id
    if visit_id is not None and entry_id is None:
        return SyncEntity.EXTENSION_VISIT, visit_id
    raise ProblemError(
        status=422,
        title="Exactly one attachment parent is required",
        detail="Send either logbook_entry_id or extension_visit_id, not both.",
    )


@router.post("/attachments:presign", response_model=PresignResponse, status_code=201)
async def presign_attachment(
    payload: PresignRequest,
    user_id: CurrentUserId,
    attachments: AttachmentRepoDep,
    memberships: MembershipRepoDep,
    presigner: PresignerDep,
) -> PresignResponse:
    """docs/04 §Bitácora "Fotos": create the row, hand back the URL.

    201 because a row was created; there is no later call to confirm it (D8).
    """
    entity, parent_id = _parent_of(payload)
    try:
        upload = await presign_photo(
            user_id=user_id,
            entity=entity,
            parent_id=parent_id,
            content_type=payload.content_type,
            size=payload.bytes,
            attachments=attachments,
            memberships=memberships,
            presigner=presigner,
        )
    except InvalidAttachmentError as exc:
        raise ProblemError(
            status=422, title="Photo is not an accepted upload", detail=str(exc)
        ) from exc
    except AttachmentParentNotFoundError as exc:
        raise ProblemError(status=404, title="Attachment parent not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot attach photos") from exc
    return PresignResponse(upload_url=upload.upload_url, object_key=upload.object_key)
