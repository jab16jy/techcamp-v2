"""`POST /attachments:presign` tests (docs/04 §Bitácora "Fotos"; D8; ADR-0018; docs/09).

The API never receives photo bytes (ADR-0018): presign creates the `attachment`
row and hands back a URL the browser uploads to directly. Every case here also
asserts what was NOT written, because D8's row is the record of an upload that
may never land (the orphan the cleanup job owns).
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.logbook.adapters.api.deps import get_presigner
from techcamp.logbook.adapters.orm import AttachmentRow, ExtensionVisitRow, LogbookEntryRow
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
_MAX_BYTES = 200 * 1024


@dataclass(frozen=True, slots=True)
class OrgContext:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    entry_id: UUID
    visit_id: UUID
    user_ids: dict[str, UUID]
    tokens: dict[str, str]


async def _create_org_context(session: AsyncSession) -> OrgContext:
    org_id = uuid7()
    farm_id = uuid7()
    plot_id = uuid7()
    roles = ("owner", "technician", "producer", "viewer")
    user_ids = {role: uuid7() for role in roles}

    session.add(OrganizationRow(id=org_id, name=f"Org {org_id.hex[:6]}", kind="individual"))
    for role, user_id in user_ids.items():
        session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=role))
    await session.commit()

    for role, user_id in user_ids.items():
        session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await session.commit()

    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
            technician_id=user_ids["technician"],
        )
    )
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()

    entry_id = uuid7()
    session.add(
        LogbookEntryRow(
            id=entry_id,
            org_id=org_id,
            plot_id=plot_id,
            kind="observation",
            occurred_on=date(2026, 9, 28),
            notes="Cosecha registrada sin senal",
            created_by=user_ids["owner"],
            created_offline=True,
            client_updated_at=_NOW,
        )
    )
    visit_id = uuid7()
    session.add(
        ExtensionVisitRow(
            id=visit_id,
            org_id=org_id,
            farm_id=farm_id,
            plot_id=plot_id,
            technician_id=user_ids["technician"],
            visited_on=date(2026, 9, 27),
            topics=["human_capacities"],
            notes="Visita sin senal",
            client_updated_at=_NOW,
        )
    )
    await session.commit()

    return OrgContext(
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        entry_id=entry_id,
        visit_id=visit_id,
        user_ids=user_ids,
        tokens={role: issue_token(str(user_id)) for role, user_id in user_ids.items()},
    )


class FakePresigner:
    """The port's test double (AGENTS.md: a port exists when external I/O needs
    a double). It records what it was asked to sign instead of signing it."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def presign_put(
        self, *, object_key: str, content_type: str, size: int, expires_in: int
    ) -> str:
        self.calls.append(
            {
                "object_key": object_key,
                "content_type": content_type,
                "size": size,
                "expires_in": expires_in,
            }
        )
        return f"https://objects.test/{object_key}?X-Amz-Signature=fake"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _presign(
    client: TestClient,
    token: str,
    *,
    entry_id: UUID | None = None,
    visit_id: UUID | None = None,
    content_type: str = "image/jpeg",
    size: int = 180_000,
) -> Any:
    payload: dict[str, Any] = {"content_type": content_type, "bytes": size}
    if entry_id is not None:
        payload["logbook_entry_id"] = str(entry_id)
    if visit_id is not None:
        payload["extension_visit_id"] = str(visit_id)
    return client.post("/attachments:presign", json=payload, headers=_auth(token))


async def _attachments_for(
    session: AsyncSession, parent_column: str, parent_id: UUID
) -> list[AttachmentRow]:
    column = getattr(AttachmentRow, parent_column)
    result = await session.execute(select(AttachmentRow).where(column == parent_id))
    return list(result.scalars())


async def test_presign_creates_the_attachment_row_and_returns_a_url_for_an_entry(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        response = _presign(_client(), org.tokens["owner"], entry_id=org.entry_id)
    finally:
        del app.dependency_overrides[get_presigner]

    assert response.status_code == 201, response.text
    body = response.json()
    # The key layout is the contract the cleanup job and the browser share.
    photo_id = body["object_key"].rsplit("/", 1)[1]
    assert body["object_key"] == f"attachments/{org.org_id}/{org.entry_id}/{photo_id}"
    assert body["object_key"].endswith(".jpg")
    assert body["upload_url"] == f"https://objects.test/{body['object_key']}?X-Amz-Signature=fake"
    # The declared size travels to the signer, so the length is inside the
    # signature and not only inside the row.
    assert presigner.calls == [
        {
            "object_key": body["object_key"],
            "content_type": "image/jpeg",
            "size": 180_000,
            "expires_in": 900,
        }
    ]

    rows = await _attachments_for(db_session, "logbook_entry_id", org.entry_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.object_key == body["object_key"]
    assert row.content_type == "image/jpeg"
    assert row.bytes == 180_000
    # The row has exactly one parent (docs/03 §attachment CHECK): the other one
    # is never filled in by a presign.
    assert row.logbook_entry_id == org.entry_id
    assert row.extension_visit_id is None
    assert str(row.id) in body["object_key"]


async def test_presign_for_a_visit_by_a_technician_creates_the_visit_attachment(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        response = _presign(
            _client(), org.tokens["technician"], visit_id=org.visit_id, content_type="image/webp"
        )
    finally:
        del app.dependency_overrides[get_presigner]

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["object_key"] == (
        f"attachments/{org.org_id}/{org.visit_id}/{body['object_key'].rsplit('/', 1)[1]}"
    )
    assert body["object_key"].endswith(".webp")
    assert presigner.calls[0]["content_type"] == "image/webp"

    rows = await _attachments_for(db_session, "extension_visit_id", org.visit_id)
    assert len(rows) == 1
    assert rows[0].logbook_entry_id is None
    # The entry of the same org is untouched: a visit photo does not attach to it.
    assert await _attachments_for(db_session, "logbook_entry_id", org.entry_id) == []


async def test_presign_roles_follow_the_write_roles_of_the_parent_entity(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        # D3: a logbook entry is written by owner, technician and producer.
        for role in ("owner", "technician", "producer"):
            response = _presign(client, org.tokens[role], entry_id=org.entry_id)
            assert response.status_code == 201, (role, response.text)
        # D3: an extension visit is written by a technician only, so every other
        # role is refused even though it is a member of the same org — 403, not
        # 404, because membership is what 404 protects and it already passed.
        for role in ("owner", "producer", "viewer"):
            response = _presign(client, org.tokens[role], visit_id=org.visit_id)
            assert response.status_code == 403, (role, response.text)
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["title"] == "Role cannot attach photos"
        # A viewer may not write a logbook entry either (D3).
        response = _presign(client, org.tokens["viewer"], entry_id=org.entry_id)
        assert response.status_code == 403
        assert response.json()["title"] == "Role cannot attach photos"
    finally:
        del app.dependency_overrides[get_presigner]

    # Negative: only the three allowed entry presigns and no visit presign wrote a row.
    assert len(await _attachments_for(db_session, "logbook_entry_id", org.entry_id)) == 3
    assert await _attachments_for(db_session, "extension_visit_id", org.visit_id) == []
    assert len(presigner.calls) == 3


async def test_presign_rejects_out_of_range_bytes_and_unsupported_content_type(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        for size, content_type in (
            (0, "image/jpeg"),
            (_MAX_BYTES + 1, "image/jpeg"),
            (180_000, "image/png"),
        ):
            response = _presign(
                client,
                org.tokens["owner"],
                entry_id=org.entry_id,
                content_type=content_type,
                size=size,
            )
            assert response.status_code == 422, (size, content_type, response.text)
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json()["title"] == "Photo is not an accepted upload"
        # The boundary itself is accepted: 200 KB exactly (docs/04:188, D8).
        response = _presign(client, org.tokens["owner"], entry_id=org.entry_id, size=_MAX_BYTES)
        assert response.status_code == 201, response.text
    finally:
        del app.dependency_overrides[get_presigner]

    # Negative: the three rejected requests wrote nothing and signed nothing.
    assert presigner.calls[0]["expires_in"] == 900
    assert len(presigner.calls) == 1
    rows = await _attachments_for(db_session, "logbook_entry_id", org.entry_id)
    assert len(rows) == 1
    assert rows[0].bytes == _MAX_BYTES


async def test_presign_for_a_parent_in_another_org_returns_404_and_writes_nothing(
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org_context(db_session)
    org_b = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        # An entry of another org answers exactly like a missing one (docs/09):
        # both are 404, so existence never leaks.
        foreign = _presign(client, org_b.tokens["owner"], entry_id=org_a.entry_id)
        missing = _presign(client, org_b.tokens["owner"], entry_id=uuid7())
    finally:
        del app.dependency_overrides[get_presigner]

    assert foreign.status_code == 404
    assert foreign.headers["content-type"] == "application/problem+json"
    assert foreign.json()["title"] == "Attachment parent not found"
    assert missing.status_code == 404
    assert missing.json() == foreign.json()
    assert str(org_a.entry_id) not in foreign.text
    assert str(org_a.org_id) not in foreign.text

    assert presigner.calls == []
    assert await _attachments_for(db_session, "logbook_entry_id", org_a.entry_id) == []
    assert await _attachments_for(db_session, "extension_visit_id", org_a.visit_id) == []


async def test_presign_for_a_deleted_parent_returns_404_while_a_live_one_succeeds(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    deleted_entry_id = uuid7()
    db_session.add(
        LogbookEntryRow(
            id=deleted_entry_id,
            org_id=org.org_id,
            plot_id=org.plot_id,
            kind="observation",
            occurred_on=date(2026, 9, 20),
            created_by=org.user_ids["owner"],
            client_updated_at=_NOW,
            deleted_at=_NOW,
        )
    )
    deleted_visit_id = uuid7()
    db_session.add(
        ExtensionVisitRow(
            id=deleted_visit_id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            technician_id=org.user_ids["technician"],
            visited_on=date(2026, 9, 20),
            topics=["participation"],
            client_updated_at=_NOW,
            deleted_at=_NOW,
        )
    )
    await db_session.commit()

    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        deleted_entry = _presign(client, org.tokens["owner"], entry_id=deleted_entry_id)
        deleted_visit = _presign(client, org.tokens["technician"], visit_id=deleted_visit_id)
        # A live parent of the same org still works, so the 404 above is the
        # tombstone and not a broken route.
        live = _presign(client, org.tokens["owner"], entry_id=org.entry_id)
    finally:
        del app.dependency_overrides[get_presigner]

    assert deleted_entry.status_code == 404
    assert deleted_entry.json()["title"] == "Attachment parent not found"
    assert deleted_visit.status_code == 404
    assert live.status_code == 201, live.text
    assert len(presigner.calls) == 1

    assert await _attachments_for(db_session, "logbook_entry_id", deleted_entry_id) == []
    assert await _attachments_for(db_session, "extension_visit_id", deleted_visit_id) == []


async def test_presign_requires_exactly_one_parent(db_session: AsyncSession) -> None:
    org = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        neither = _presign(client, org.tokens["owner"])
        both = _presign(client, org.tokens["owner"], entry_id=org.entry_id, visit_id=org.visit_id)
    finally:
        del app.dependency_overrides[get_presigner]

    for response in (neither, both):
        assert response.status_code == 422, response.text
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["title"] == "Exactly one attachment parent is required"
    assert presigner.calls == []
    assert await _attachments_for(db_session, "logbook_entry_id", org.entry_id) == []


async def test_presign_without_configured_credentials_is_503_and_writes_nothing(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = await _create_org_context(db_session)
    # Production has no default credentials (ADR-0021 profiles, ADR-0018): a
    # seminar-shaped default would be a committed secret in production.
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.delenv("TECHCAMP_S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("TECHCAMP_S3_SECRET_KEY", raising=False)

    response = _presign(_client(), org.tokens["owner"], entry_id=org.entry_id)

    assert response.status_code == 503, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Object storage is not configured"
    assert await _attachments_for(db_session, "logbook_entry_id", org.entry_id) == []

    # An EMPTY value is how infra/compose.yaml passes "not set", and it must
    # read as absent rather than as a configured empty credential: in the
    # seminar profile the MinIO default takes over and the wired real presigner
    # signs, with no test double anywhere in the request.
    monkeypatch.setenv("TECHCAMP_PROFILE", "seminar")
    monkeypatch.setenv("TECHCAMP_S3_ACCESS_KEY", "")
    monkeypatch.setenv("TECHCAMP_S3_SECRET_KEY", "")
    signed = _presign(_client(), org.tokens["owner"], entry_id=org.entry_id)

    assert signed.status_code == 201, signed.text
    assert urlparse(signed.json()["upload_url"]).netloc == "localhost:9000"
    assert len(await _attachments_for(db_session, "logbook_entry_id", org.entry_id)) == 1


async def test_presigned_upload_url_is_a_sigv4_put_for_the_configured_public_host(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from techcamp.logbook.adapters.attachments import Boto3Presigner

    org = await _create_org_context(db_session)

    def _no_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("presigning must be a local signature, with no socket")

    # ADR-0018's promise is that the API hands out a URL and never carries
    # bytes; the signing half of that is that building the client and signing
    # touch no network, which this makes an assertion instead of a claim. The
    # guard covers the boto3 work only: the fixture's own database connection
    # is a socket this test legitimately opens.
    monkeypatch.setattr(socket, "socket", _no_socket)
    # The real adapter, not the double: presigning is a local signature, so
    # there is nothing to reach and nothing to fake.
    presigner = Boto3Presigner(
        endpoint_url="http://localhost:9000",
        bucket="logbook-photos",
        access_key="techcamp",
        secret_key="techcamp123",
        region="us-east-1",
    )
    object_key = f"attachments/{org.org_id}/{org.entry_id}/{uuid7()}.webp"
    url = await presigner.presign_put(
        object_key=object_key, content_type="image/webp", size=180_000, expires_in=900
    )

    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc) == ("http", "localhost:9000")
    # Path style, so the host stays the public one: a virtual-hosted style would
    # ask the browser for `logbook-photos.localhost:9000`, which does not resolve.
    assert parsed.path == f"/logbook-photos/{object_key}"
    query = parse_qs(parsed.query)
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Expires"] == ["900"]
    assert query["X-Amz-Signature"] != [""]
    # The content type is signed, so the upload cannot be a different type than
    # the row says (ADR-0018: only images reach the bucket), and the length is
    # signed too, so the store refuses a body of a different size: D8's 200 KB
    # ceiling is a promise about what actually lands, not only about what the
    # phone declared.
    assert query["X-Amz-SignedHeaders"] == ["content-length;content-type;host"]

    # The signature belongs to the request and not to a constant: another
    # secret signs the same key differently. (Two signatures inside the same
    # second are equal, which is why this changes the credentials instead of
    # calling twice.)
    other = Boto3Presigner(
        endpoint_url="http://localhost:9000",
        bucket="logbook-photos",
        access_key="techcamp",
        secret_key="a-different-secret",
        region="us-east-1",
    )
    other_url = await other.presign_put(
        object_key=object_key, content_type="image/webp", size=180_000, expires_in=900
    )
    assert parse_qs(urlparse(other_url).query)["X-Amz-Signature"] != query["X-Amz-Signature"]

    # Nothing was written: this is the adapter's own signing, isolated from the
    # endpoint and from the use case.
    monkeypatch.undo()
    assert await _attachments_for(db_session, "logbook_entry_id", org.entry_id) == []


async def test_presigned_upload_url_signs_the_declared_length(db_session: AsyncSession) -> None:
    from techcamp.logbook.adapters.attachments import Boto3Presigner

    presigner = Boto3Presigner(
        endpoint_url="http://localhost:9000",
        bucket="logbook-photos",
        access_key="techcamp",
        secret_key="techcamp123",
        region="us-east-1",
    )
    object_key = f"attachments/{uuid7()}/{uuid7()}/{uuid7()}.jpg"

    declared = await presigner.presign_put(
        object_key=object_key, content_type="image/jpeg", size=180_000, expires_in=900
    )
    bigger = await presigner.presign_put(
        object_key=object_key, content_type="image/jpeg", size=200_000, expires_in=900
    )
    same = await presigner.presign_put(
        object_key=object_key, content_type="image/jpeg", size=180_000, expires_in=900
    )

    # Same key, same type, same second: only the declared length differs, and
    # the signature changes with it. That is what puts the length INSIDE the
    # signature — `X-Amz-SignedHeaders` only names the headers, so a URL that
    # listed `content-length` without binding its value would sign the same here.
    declared_signature = parse_qs(urlparse(declared).query)["X-Amz-Signature"]
    assert parse_qs(urlparse(bigger).query)["X-Amz-Signature"] != declared_signature
    # The negative of that: nothing else moved, so an identical request signs
    # identically. Without this the first assertion could pass for a signature
    # that simply varies per call.
    assert parse_qs(urlparse(same).query)["X-Amz-Signature"] == declared_signature


async def test_attachment_rows_are_counted_per_org_parent(
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org_context(db_session)
    org_b = await _create_org_context(db_session)
    presigner = FakePresigner()
    app.dependency_overrides[get_presigner] = lambda: presigner
    try:
        client = _client()
        first = _presign(client, org_a.tokens["owner"], entry_id=org_a.entry_id)
        second = _presign(client, org_b.tokens["owner"], entry_id=org_b.entry_id)
    finally:
        del app.dependency_overrides[get_presigner]

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["object_key"] != second.json()["object_key"]
    assert org_a.org_id != org_b.org_id
    assert first.json()["object_key"].startswith(f"attachments/{org_a.org_id}/")
    assert second.json()["object_key"].startswith(f"attachments/{org_b.org_id}/")

    total = await db_session.scalar(select(func.count()).select_from(AttachmentRow))
    assert total == 2
