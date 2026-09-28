"""Attachment domain rules (docs/04 §Bitácora "Fotos"; docs/03 §attachment; D8).

Pure, no I/O: the photo vocabulary, the 200 KB ceiling and the object key
layout the browser, the storage and the cleanup job all share.
"""

from __future__ import annotations

from techcamp.logbook.domain.attachments import (
    PRESIGN_EXPIRY_SECONDS,
    Attachment,
    PhotoContentType,
    ensure_valid_photo,
    photo_object_key,
)
from techcamp.logbook.domain.errors import InvalidAttachmentError
from techcamp.shared.ids import uuid7

_ORG = uuid7()
_PARENT = uuid7()


def test_every_content_type_has_an_object_key_extension() -> None:
    # A new member of the vocabulary without an extension would raise a bare
    # KeyError at presign time (a 500); this is the test that says so first.
    extensions = {
        content_type: photo_object_key(
            org_id=_ORG, parent_id=_PARENT, photo_id=uuid7(), content_type=content_type
        ).rsplit(".", 1)[1]
        for content_type in PhotoContentType
    }
    assert extensions == {PhotoContentType.JPEG: "jpg", PhotoContentType.WEBP: "webp"}


def test_photo_object_key_is_scoped_by_org_and_parent() -> None:
    photo_id = uuid7()
    key = photo_object_key(
        org_id=_ORG, parent_id=_PARENT, photo_id=photo_id, content_type=PhotoContentType.JPEG
    )
    assert key == f"attachments/{_ORG}/{_PARENT}/{photo_id}.jpg"
    # A photo of another parent in the same org is a different object, so the
    # cleanup job can tell them apart without a database lookup.
    other = photo_object_key(
        org_id=_ORG,
        parent_id=uuid7(),
        photo_id=photo_id,
        content_type=PhotoContentType.JPEG,
    )
    assert other != key and other.startswith(f"attachments/{_ORG}/")


def test_ensure_valid_photo_accepts_only_the_two_types_within_the_ceiling() -> None:
    assert ensure_valid_photo(content_type="image/jpeg", size=1) is PhotoContentType.JPEG
    assert ensure_valid_photo(content_type="image/webp", size=200 * 1024) is (PhotoContentType.WEBP)
    for content_type, size in (
        ("image/png", 1000),
        ("application/pdf", 1000),
        ("image/jpeg", 0),
        ("image/jpeg", -1),
        ("image/jpeg", 200 * 1024 + 1),
    ):
        try:
            ensure_valid_photo(content_type=content_type, size=size)
        except InvalidAttachmentError:
            continue
        raise AssertionError(f"accepted {content_type} of {size} bytes")


def test_presign_expiry_is_fifteen_minutes() -> None:
    # D8/ADR-0018: a short-lived URL, long enough for a farmer on a slow link
    # and short enough that a leaked one is not a permanent door to the bucket.
    assert PRESIGN_EXPIRY_SECONDS == 900


def test_attachment_carries_exactly_one_parent() -> None:
    attachment = Attachment(
        id=uuid7(),
        logbook_entry_id=_PARENT,
        extension_visit_id=None,
        object_key=photo_object_key(
            org_id=_ORG,
            parent_id=_PARENT,
            photo_id=uuid7(),
            content_type=PhotoContentType.WEBP,
        ),
        content_type="image/webp",
        bytes=1234,
    )
    assert attachment.extension_visit_id is None
    assert attachment.logbook_entry_id == _PARENT
    assert attachment.object_key.endswith(".webp")
