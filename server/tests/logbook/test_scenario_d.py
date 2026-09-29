"""Scenario D end to end over HTTP (docs/10-dag.md:71; docs/04 §Bitácora; docs/06 §7; docs/11).

E8's exit criterion: a harvest recorded in airplane mode syncs without
duplicating, and a visit recorded with no signal syncs too. The single pieces
are already covered — `test_sync_api.py` proves each push status, `test_sync_pull.py`
proves the cursor and the delete op. This file chains them the way the field
does: push → the same batch again → pull → one server row, and two devices
editing one entry end in one row with the newer `client_updated_at` (D4, D7).

The harvest also carries `created_offline: true`, the flag docs/11 "Uso offline"
counts: it must survive the round trip, because a pull that lost it would make
the metric read zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(env: SyncEnv, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.mine.tokens[role]}"}


def _entry_payload(
    entry_id: UUID,
    plot_id: UUID,
    client_updated_at: datetime,
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
            **data,
        },
    }


def _visit_payload(
    visit_id: UUID,
    farm_id: UUID,
    technician_id: UUID,
    client_updated_at: datetime,
    *,
    plot_id: UUID,
    **data: Any,
) -> dict[str, Any]:
    return {
        "id": str(visit_id),
        "entity": "extension_visit",
        "op": "upsert",
        "client_updated_at": client_updated_at.isoformat(),
        "data": {
            "farm_id": str(farm_id),
            "plot_id": str(plot_id),
            "technician_id": str(technician_id),
            "visited_on": "2026-09-28",
            "topics": ["natural_resources"],
            **data,
        },
    }


def _push(
    env: SyncEnv, changes: list[dict[str, Any]], *, role: str = "producer"
) -> list[dict[str, Any]]:
    with _client() as client:
        response = client.post(
            "/sync/push",
            json={"device_id": "phone-1", "changes": changes},
            headers=_auth(env, role),
        )
    assert response.status_code == 200, response.text
    results: list[dict[str, Any]] = response.json()["results"]
    return results


def _pull(env: SyncEnv, *, role: str = "producer", since: int = 0) -> list[dict[str, Any]]:
    with _client() as client:
        response = client.get(f"/sync/pull?since={since}&limit=500", headers=_auth(env, role))
    assert response.status_code == 200, response.text
    changes: list[dict[str, Any]] = response.json()["changes"]
    return changes


async def _row_count(db_session: AsyncSession, model: Any) -> int:
    count = (await db_session.execute(select(func.count()).select_from(model))).scalar_one()
    return int(count)


async def test_scenario_d_harvest_created_offline_syncs_once_and_the_retry_is_a_duplicate(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """docs/10-dag.md:71: the harvest of airplane mode reaches the server once.

    Push → `applied`; the very same batch again → `duplicate` with the same
    `server_version`; pull → the entry exactly once, with its data and
    `created_offline: true`; one row in the database (docs/11 "Uso offline").
    """
    entry_id = uuid7()
    change = _entry_payload(
        entry_id,
        env.mine.plot_id,
        env.now,
        kind="harvest",
        created_offline=True,
        yield_kg="250.5",
        notes="cosecha en modo avión",
    )

    first = _push(env, [change])[0]
    retry = _push(env, [change])[0]
    changes = _pull(env)
    returned = [c for c in changes if c["id"] == str(entry_id)]

    assert (first["status"], first["error"]) == ("applied", None)
    assert first["server_version"] is not None
    assert (retry["status"], retry["server_version"]) == ("duplicate", first["server_version"])
    assert len(returned) == 1
    assert (returned[0]["entity"], returned[0]["op"]) == ("logbook_entry", "upsert")
    assert returned[0]["data"]["created_offline"] is True
    assert returned[0]["data"]["notes"] == "cosecha en modo avión"
    assert returned[0]["data"]["yield_kg"] == 250.5
    assert await _row_count(db_session, LogbookEntryRow) == 1


async def test_scenario_d_two_devices_the_newer_edit_wins_and_the_older_push_is_overwritten(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """D4/D7: two devices edit one entry; the newer `client_updated_at` wins.

    Device B's edit applies; device A's stale edit is answered
    `conflict_overwritten` carrying the stored `server_version` and writes
    nothing; pull returns B's values exactly once (never A's), one row.
    """
    entry_id = uuid7()
    created = _entry_payload(entry_id, env.mine.plot_id, env.now, notes="creada por A")
    device_b = _entry_payload(
        entry_id, env.mine.plot_id, env.now + timedelta(hours=1), notes="editada por B"
    )
    device_a_stale = _entry_payload(
        entry_id, env.mine.plot_id, env.now + timedelta(minutes=30), notes="corregida por A"
    )

    assert _push(env, [created])[0]["status"] == "applied"
    newer = _push(env, [device_b])[0]
    older = _push(env, [device_a_stale])[0]
    returned = [c for c in _pull(env) if c["id"] == str(entry_id)]

    assert newer["status"] == "applied"
    assert older["status"] == "conflict_overwritten"
    assert older["server_version"] == newer["server_version"]
    assert len(returned) == 1
    assert returned[0]["data"]["notes"] == "editada por B"
    assert returned[0]["data"]["notes"] != "corregida por A"
    assert await _row_count(db_session, LogbookEntryRow) == 1


async def test_scenario_d_visit_recorded_offline_by_the_farm_technician_syncs_once(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """docs/03 §extension_visit: the visit recorded with no signal syncs too.

    The farm's technician pushes it → `applied`; the same batch again →
    `duplicate`; pull returns it exactly once with its topics and technician;
    one row. D3: only the technician can push it.
    """
    visit_id = uuid7()
    change = _visit_payload(
        visit_id,
        env.mine.farm_id,
        env.mine.users["technician"],
        env.now,
        plot_id=env.mine.plot_id,
        topics=["natural_resources", "participation"],
        recommendations="rotar el cultivo",
        notes="visita sin señal",
    )

    first = _push(env, [change], role="technician")[0]
    retry = _push(env, [change], role="technician")[0]
    returned = [c for c in _pull(env, role="technician") if c["id"] == str(visit_id)]

    assert (first["status"], first["error"]) == ("applied", None)
    assert retry["status"] == "duplicate"
    assert len(returned) == 1
    assert (returned[0]["entity"], returned[0]["op"]) == ("extension_visit", "upsert")
    assert returned[0]["data"]["technician_id"] == str(env.mine.users["technician"])
    assert returned[0]["data"]["topics"] == ["natural_resources", "participation"]
    assert returned[0]["data"]["recommendations"] == "rotar el cultivo"
    assert await _row_count(db_session, ExtensionVisitRow) == 1
