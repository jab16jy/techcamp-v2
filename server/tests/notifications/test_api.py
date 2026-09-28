"""Push subscription endpoints (docs/04-api.md §Alertas y notificaciones; D15).

A subscription belongs to a user, not to an organization, so the isolation here
is "your own row": `DELETE` of anyone else's (or an unknown) id is 404.
"""

from __future__ import annotations

from itertools import count
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.orm import AppUserRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.notifications.adapters.orm import PushSubscriptionRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_ENDPOINT = "https://push.example.com/sub/abc123"
_KEYS = {"p256dh": "public-key", "auth": "auth-secret"}

# A counter, not `uuid7().int % 100000`: the modulo could collide on the
# unique `phone` column between two ids generated close together.
_phone_seq = count()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


async def _user(session: AsyncSession) -> tuple[UUID, str]:
    user_id = uuid7()
    session.add(AppUserRow(id=user_id, phone=f"+5730077{next(_phone_seq):05d}"))
    await session.commit()
    return user_id, issue_token(str(user_id))


async def _rows(session: AsyncSession) -> list[PushSubscriptionRow]:
    return list((await session.execute(select(PushSubscriptionRow))).scalars())


async def test_registering_a_push_subscription_returns_its_id(db_session: AsyncSession) -> None:
    user_id, token = await _user(db_session)
    client = _client()

    response = client.post(
        "/push-subscriptions", json={"endpoint": _ENDPOINT, "keys": _KEYS}, headers=_auth(token)
    )

    assert response.status_code == 201, response.text
    rows = await _rows(db_session)
    assert [row.id for row in rows] == [UUID(response.json()["id"])]
    assert (rows[0].user_id, rows[0].endpoint, rows[0].keys) == (user_id, _ENDPOINT, _KEYS)


async def test_registering_the_same_endpoint_rebinds_it_to_the_caller(
    db_session: AsyncSession,
) -> None:
    """D15: `endpoint` is UNIQUE, so the same browser registering again re-binds
    the row and replaces its keys instead of failing or duplicating."""
    _first_user, first_token = await _user(db_session)
    second_user, second_token = await _user(db_session)
    client = _client()
    first = client.post(
        "/push-subscriptions",
        json={"endpoint": _ENDPOINT, "keys": _KEYS},
        headers=_auth(first_token),
    )

    response = client.post(
        "/push-subscriptions",
        json={"endpoint": _ENDPOINT, "keys": {"p256dh": "rotated", "auth": "rotated-secret"}},
        headers=_auth(second_token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["id"] == first.json()["id"]
    rows = await _rows(db_session)
    assert len(rows) == 1
    assert rows[0].user_id == second_user
    assert rows[0].keys == {"p256dh": "rotated", "auth": "rotated-secret"}


async def test_deleting_own_push_subscription_is_204(db_session: AsyncSession) -> None:
    _user_id, token = await _user(db_session)
    client = _client()
    created = client.post(
        "/push-subscriptions", json={"endpoint": _ENDPOINT, "keys": _KEYS}, headers=_auth(token)
    ).json()

    response = client.delete(f"/push-subscriptions/{created['id']}", headers=_auth(token))

    assert response.status_code == 204, response.text
    assert await _rows(db_session) == []


async def test_deleting_another_users_push_subscription_is_404(db_session: AsyncSession) -> None:
    _owner_id, owner_token = await _user(db_session)
    _other_id, other_token = await _user(db_session)
    client = _client()
    created = client.post(
        "/push-subscriptions",
        json={"endpoint": _ENDPOINT, "keys": _KEYS},
        headers=_auth(owner_token),
    ).json()

    response = client.delete(f"/push-subscriptions/{created['id']}", headers=_auth(other_token))

    assert response.status_code == 404
    assert response.json()["title"] == "Push subscription not found"
    assert len(await _rows(db_session)) == 1
