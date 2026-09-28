"""`POST /sync/push` over HTTP (docs/04 §Bitácora; docs/06 §7; D3-D5).

The per-change decisions are covered in `test_push.py`. What matters here is
the wire contract: the batch limit, the shape of `results`, and that org
isolation and the role rules answer from the endpoint, as docs/09 §Seguridad
demands for every endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.logbook.adapters.orm import ExtensionVisitRow, LogbookEntryRow
from techcamp.main import app
from techcamp.shared.ids import uuid7

if TYPE_CHECKING:  # the fixture types live in the conftest, which pytest owns
    from tests.logbook.conftest import SyncEnv

pytestmark = pytest.mark.anyio

_TOLERANCE = timedelta(seconds=30)
"""Over HTTP the clock is the server's: `deleted_at` is the moment the
request was served, so the test brackets it with the wall clock."""


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(env: SyncEnv, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.mine.tokens[role]}"}


def _entry_payload(
    entry_id: UUID,
    plot_id: UUID,
    client_updated_at: Any,
    *,
    kind: str = "observation",
    **data: Any,
) -> dict[str, Any]:
    return {
        "id": str(entry_id),
        "entity": "logbook_entry",
        "op": "upsert",
        "client_updated_at": client_updated_at.isoformat(),
        "data": {
            "plot_id": str(plot_id),
            "kind": kind,
            "occurred_on": "2026-09-28",
            "quantity": "12",
            "unit": "kg",
            **data,
        },
    }


def _push(env: SyncEnv, changes: list[dict[str, Any]], *, role: str = "producer") -> dict[str, Any]:
    with _client() as client:
        response = client.post(
            "/sync/push",
            json={"device_id": "phone-1", "changes": changes},
            headers=_auth(env, role),
        )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_pushed_entry_applies_and_the_same_batch_again_is_a_duplicate(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """The acceptance criterion of docs/04 §Bitácora: one row, and the second
    push of the same batch says `duplicate` without writing."""
    entry_id = uuid7()
    change = _entry_payload(entry_id, env.mine.plot_id, env.now, notes="primera")

    first = _push(env, [change])["results"][0]
    second = _push(env, [change])["results"][0]
    stored = (
        await db_session.execute(
            select(LogbookEntryRow)
            .where(LogbookEntryRow.id == entry_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert first == {
        "id": str(entry_id),
        "status": "applied",
        "server_version": first["server_version"],
        "error": None,
    }
    assert first["server_version"] is not None
    assert second["status"] == "duplicate"
    assert second["server_version"] == first["server_version"]
    assert stored.notes == "primera"
    assert stored.created_by == env.mine.users["producer"]
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 1


async def test_a_batch_of_more_than_100_changes_answers_422_and_applies_nothing(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D5: the limit is on the request, so nothing of the batch is applied."""
    changes = [_entry_payload(uuid7(), env.mine.plot_id, env.now) for _ in range(101)]

    with _client() as client:
        response = client.post(
            "/sync/push",
            json={"device_id": "phone-1", "changes": changes},
            headers=_auth(env, "producer"),
        )

    assert response.status_code == 422, response.text
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0


async def test_a_change_without_an_offset_answers_422(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """docs/04 conventions: a timestamp without an offset answers 422, and the
    LWW comparison of D4 needs both timestamps to mean the same instant."""
    change = _entry_payload(uuid7(), env.mine.plot_id, env.now)
    change["client_updated_at"] = "2026-09-28T10:00:00"

    with _client() as client:
        response = client.post(
            "/sync/push",
            json={"device_id": "phone-1", "changes": [change]},
            headers=_auth(env, "producer"),
        )

    assert response.status_code == 422, response.text
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0


async def test_one_rejected_change_does_not_sink_the_batch(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D5 over the wire: a change the client broke keeps its error and the
    other change in the same request is still applied."""
    broken_id, good_id = uuid7(), uuid7()

    results = _push(
        env,
        [
            _entry_payload(broken_id, env.mine.plot_id, env.now, kind="harvest"),
            _entry_payload(good_id, env.mine.plot_id, env.now, notes="sigue"),
        ],
    )["results"]

    assert [(r["id"], r["status"], r["error"]) for r in results] == [
        (str(broken_id), "rejected", "invalid"),
        (str(good_id), "applied", None),
    ]
    assert results[1]["server_version"] is not None
    ids = set((await db_session.execute(select(LogbookEntryRow.id))).scalars())
    assert ids == {good_id}


async def test_a_viewer_pushing_an_entry_is_forbidden_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D3 at the endpoint, with the same negative assertion: nothing is
    written."""
    entry_id = uuid7()

    result = _push(env, [_entry_payload(entry_id, env.mine.plot_id, env.now)], role="viewer")[
        "results"
    ][0]

    assert (result["status"], result["error"]) == ("rejected", "forbidden")
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0


async def test_a_producer_pushing_a_visit_is_forbidden_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D3: `extension_visit` is `technician` only, and `technician_id` must be
    the caller (docs/03 §extension_visit)."""
    visit = {
        "id": str(uuid7()),
        "entity": "extension_visit",
        "op": "upsert",
        "client_updated_at": env.now.isoformat(),
        "data": {
            "farm_id": str(env.mine.farm_id),
            "technician_id": str(env.mine.users["technician"]),
            "visited_on": "2026-09-28",
            "topics": ["natural_resources"],
        },
    }

    result = _push(env, [visit], role="producer")["results"][0]

    assert (result["status"], result["error"]) == ("rejected", "forbidden")
    count = (
        await db_session.execute(select(func.count()).select_from(ExtensionVisitRow))
    ).scalar_one()
    assert count == 0


async def test_a_visit_the_technician_pushes_applies_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """The same visit the rule above refuses, applied by its technician, so the
    rejection is the role and not a dead branch."""
    visit_id = uuid7()
    visit = {
        "id": str(visit_id),
        "entity": "extension_visit",
        "op": "upsert",
        "client_updated_at": env.now.isoformat(),
        "data": {
            "farm_id": str(env.mine.farm_id),
            "plot_id": str(env.mine.plot_id),
            "technician_id": str(env.mine.users["technician"]),
            "visited_on": "2026-09-28",
            "topics": ["natural_resources", "participation"],
            "recommendations": "rotar el cultivo",
        },
    }

    result = _push(env, [visit], role="technician")["results"][0]
    stored = (
        await db_session.execute(
            select(ExtensionVisitRow)
            .where(ExtensionVisitRow.id == visit_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert (result["status"], result["error"]) == ("applied", None)
    assert stored.org_id == env.mine.org_id
    assert stored.plot_id == env.mine.plot_id
    assert stored.topics == ["natural_resources", "participation"]


async def test_a_delete_over_http_stamps_the_tombstone_with_a_new_version(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D12 and D12's `server_version`: the delete travels as the row as last
    saved, without `deleted_at`, and the server sets it."""
    entry_id = uuid7()
    applied = _push(env, [_entry_payload(entry_id, env.mine.plot_id, env.now)])["results"][0]
    delete = {
        "id": str(entry_id),
        "entity": "logbook_entry",
        "op": "delete",
        "client_updated_at": (env.now + timedelta(hours=1)).isoformat(),
        "data": {
            "plot_id": str(env.mine.plot_id),
            "kind": "spraying",  # a kind the CHECK would refuse if it were validated
            "occurred_on": "2026-09-28",
        },
    }

    served_at = datetime.now(UTC)
    deleted = _push(env, [delete])["results"][0]
    stored = (
        await db_session.execute(
            select(LogbookEntryRow)
            .where(LogbookEntryRow.id == entry_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert deleted["status"] == "applied"
    assert deleted["server_version"] is not None
    assert deleted["server_version"] > applied["server_version"]
    assert stored.deleted_at is not None
    assert abs((stored.deleted_at - served_at).total_seconds()) <= _TOLERANCE.total_seconds()
    assert stored.kind == "observation"  # the delete did not rewrite the fields


async def test_an_id_of_another_org_is_not_found_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D4 and docs/09 at the endpoint: an id another org owns answers like a
    missing one, and the foreign row keeps its content."""
    foreign_id = uuid7()
    db_session.add(
        LogbookEntryRow(
            id=foreign_id,
            org_id=env.theirs.org_id,
            plot_id=env.theirs.plot_id,
            crop_cycle_id=None,
            kind="observation",
            occurred_on=env.occurred_on,
            quantity=None,
            created_by=env.theirs.users["owner"],
            created_offline=False,
            client_updated_at=env.now - timedelta(hours=1),
            notes="de la otra organización",
        )
    )
    await db_session.commit()

    result = _push(env, [_entry_payload(foreign_id, env.mine.plot_id, env.now, notes="mío")])[
        "results"
    ][0]
    foreign = (
        await db_session.execute(
            select(LogbookEntryRow)
            .where(LogbookEntryRow.id == foreign_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert (result["status"], result["error"]) == ("rejected", "not_found")
    assert result["server_version"] is None
    assert foreign.org_id == env.theirs.org_id
    assert foreign.notes == "de la otra organización"


async def test_a_plot_of_another_org_is_not_found_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """The same isolation read from the other side: the change's own `plot_id`
    is of another organization, so it never becomes a row."""
    result = _push(env, [_entry_payload(uuid7(), env.theirs.plot_id, env.now)])["results"][0]

    assert (result["status"], result["error"]) == ("rejected", "not_found")
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0


async def test_a_client_timestamp_more_than_24_h_ahead_is_clock_skew_over_http(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D5 at the endpoint: the change is rejected and stays local, so the
    user is asked to fix the phone's clock (docs/06 §7)."""
    result = _push(
        env, [_entry_payload(uuid7(), env.mine.plot_id, datetime.now(UTC) + timedelta(hours=25))]
    )["results"][0]

    assert (result["status"], result["error"]) == ("rejected", "clock_skew")
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0


async def test_a_push_without_a_token_answers_401(env: SyncEnv, db_session: AsyncSession) -> None:
    """docs/04 conventions: the endpoint is behind the bearer token, and
    nothing is written without it."""
    with _client() as client:
        response = client.post(
            "/sync/push",
            json={
                "device_id": "phone-1",
                "changes": [_entry_payload(uuid7(), env.mine.plot_id, env.now)],
            },
        )

    assert response.status_code == 401, response.text
    count = (
        await db_session.execute(select(func.count()).select_from(LogbookEntryRow))
    ).scalar_one()
    assert count == 0
