"""Photo attachment rules (docs/03 §attachment; docs/04 §Bitácora "Fotos"; ADR-0018; D8).

Pure, like the rest of `domain`: the photo vocabulary, the ceiling the client
compresses to, and the object key layout. The API never receives the bytes
(ADR-0018), so what is validated here is the only thing the server ever knows
about a photo.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from techcamp.logbook.domain.errors import InvalidAttachmentError


class PhotoContentType(StrEnum):
    """The two types a logbook photo may have (docs/04:188; D8)."""

    JPEG = "image/jpeg"
    WEBP = "image/webp"


PHOTO_EXTENSIONS: dict[PhotoContentType, str] = {
    PhotoContentType.JPEG: "jpg",
    PhotoContentType.WEBP: "webp",
}
"""Object key suffix per type. Both sides come from the same closed vocabulary,
so a type without an extension is a test failure (`test_every_content_type_has_
an_object_key_extension`) and not a `KeyError` in the middle of a presign."""


MAX_PHOTO_BYTES: int = 200 * 1024
"""D8: 200 KB, the size the client compresses to (ADR-0018)."""


PRESIGN_EXPIRY_SECONDS: int = 900
"""15 minutes. Long enough for a slow rural link to finish an upload the farmer
started, short enough that a URL that leaked is not a standing door to the
bucket (ADR-0018)."""


@dataclass(frozen=True, slots=True)
class Attachment:
    """Photo metadata (docs/03:264-271): exactly one parent, never the bytes."""

    id: UUID
    logbook_entry_id: UUID | None
    extension_visit_id: UUID | None
    object_key: str
    content_type: str
    bytes: int


def ensure_valid_photo(*, content_type: str, size: int) -> PhotoContentType:
    """Return the accepted content type, or raise `InvalidAttachmentError` (422).

    docs/04 §Bitácora "Fotos" (D8): `bytes ≤ 200 KB` and `image/jpeg` or
    `image/webp`, anything else 422. A size of zero is refused here as well:
    an empty object is not a photo, and the table's `bytes > 0` CHECK would
    refuse the insert as a 500 rather than as the 422 it is.
    """
    if not 1 <= size <= MAX_PHOTO_BYTES:
        raise InvalidAttachmentError(
            f"photo must be between 1 and {MAX_PHOTO_BYTES} bytes (got {size})"
        )
    try:
        return PhotoContentType(content_type)
    except ValueError as exc:
        accepted = " or ".join(t.value for t in PhotoContentType)
        raise InvalidAttachmentError(
            f"content_type must be {accepted} (got {content_type!r})"
        ) from exc


def photo_object_key(
    *, org_id: UUID, parent_id: UUID, photo_id: UUID, content_type: PhotoContentType
) -> str:
    """`attachments/<org_id>/<parent_id>/<photo_id>.<ext>`, the only name of a photo.

    Org first so a bucket listing partitions per organization (docs/09 scopes
    everything else by `org_id` too), parent second so all the photos of one
    entry or one visit share a prefix — the shape both the orphan cleanup job
    (ADR-0018) and the client's pending-upload queue work against.
    """
    return f"attachments/{org_id}/{parent_id}/{photo_id}.{PHOTO_EXTENSIONS[content_type]}"
