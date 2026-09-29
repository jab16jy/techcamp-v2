"""`GET /sync/pull` tests (docs/04 §Bitácora; docs/06 §7; docs/03; D1, D2, D12, D13).

Covers:
- Merged entries and visits in server_version order, org isolation, multi-org membership (D2).
- Cursor exclusion, pagination with limit, empty page returns next_since == since.
- Tombstoned rows come back as op: "delete" with deleted_at (D12).
- JSON serialization of amounts as numbers and web type key sets.
- Query param bounds (limit 1..500, since >= 0) -> 422 and missing auth -> 401.
- D1 commit-order acceptance: a push committed after a concurrent lock is returned by pull.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.orm import MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.logbook.adapters.repositories import (
    SYNC_LOCK_KEY,
    SqlAlchemyExtensionVisitSyncRepository,
    SqlAlchemyLogbookEntrySyncRepository,
)
from techcamp.logbook.application.ports import ExtensionVisitChange, LogbookEntryChange
from techcamp.logbook.domain.models import SyncOp, SyncStatus
from techcamp.main import app
from techcamp.shared.config import database_url
from techcamp.shared.db import async_session_factory, get_session
from techcamp.shared.ids import uuid7

if TYPE_CHECKING:
    from tests.logbook.conftest import Pusher, SyncEnv

pytestmark = pytest.mark.anyio

_DSN = make_url(database_url()).set(drivername="postgresql").render_as_string(hide_password=False)
_NO_HANG = 30.0
_LOCK_WINDOW = 1.0


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
            "quantity": 12.5,
            "unit": "kg",
            **data,
        },
    }


def _visit_payload(
    visit_id: UUID,
    farm_id: UUID,
    technician_id: UUID,
    client_updated_at: datetime,
    *,
    plot_id: UUID | None = None,
    **data: Any,
) -> dict[str, Any]:
    return {
        "id": str(visit_id),
        "entity": "extension_visit",
        "op": "upsert",
        "client_updated_at": client_updated_at.isoformat(),
        "data": {
            "farm_id": str(farm_id),
            "plot_id": str(plot_id) if plot_id is not None else None,
            "technician_id": str(technician_id),
            "visited_on": "2026-09-28",
            "topics": ["natural_resources", "participation"],
            **data,
        },
    }


def _push(token: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    with _client() as client:
        response = client.post(
            "/sync/push",
            json={"device_id": "phone-test", "changes": changes},
            headers=_auth(token),
        )
    assert response.status_code == 200, response.text
    return response.json()


async def test_entries_and_visits_merged_in_server_version_order_and_org_isolated(
    env: SyncEnv, db_session: AsyncSession
) -> None:
    """Entries and visits of the caller's org come back merged in server_version order;
    a row of another org never does (docs/09 isolation), and a caller in two orgs gets both.
    """
    producer_token = env.mine.tokens["producer"]
    tech_token = env.mine.tokens["technician"]

    entry1_id = uuid7()
    visit1_id = uuid7()
    entry2_id = uuid7()

    _push(producer_token, [_entry_payload(entry1_id, env.mine.plot_id, env.now)])
    _push(
        tech_token,
        [_visit_payload(visit1_id, env.mine.farm_id, env.mine.users["technician"], env.now)],
    )
    _push(
        producer_token,
        [_entry_payload(entry2_id, env.mine.plot_id, env.now + timedelta(seconds=1))],
    )

    # Another org's entry (docs/09 isolation)
    theirs_token = env.theirs.tokens["owner"]
    theirs_entry_id = uuid7()
    _push(theirs_token, [_entry_payload(theirs_entry_id, env.theirs.plot_id, env.now)])

    # Second org where caller is also a member
    second_org_id = uuid7()
    second_farm_id = uuid7()
    second_plot_id = uuid7()
    db_session.add(OrganizationRow(id=second_org_id, name="Second Org", kind="individual"))
    await db_session.flush()
    db_session.add(
        MembershipRow(org_id=second_org_id, user_id=env.mine.users["producer"], role="producer")
    )
    from techcamp.farms.adapters.orm import FarmRow, PlotRow

    db_session.add(
        FarmRow(
            id=second_farm_id,
            org_id=second_org_id,
            name="Finca 2",
            municipality_code="47001",
            location="SRID=4326;POINT(-74.1 10.9)",
            technician_id=None,
        )
    )
    await db_session.flush()
    boundary_wkt = (
        "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
    )
    db_session.add(
        PlotRow(
            id=second_plot_id,
            org_id=second_org_id,
            farm_id=second_farm_id,
            name="Lote 2",
            boundary=boundary_wkt,
            irrigation_system="drip",
        )
    )
    await db_session.commit()

    second_entry_id = uuid7()
    _push(producer_token, [_entry_payload(second_entry_id, second_plot_id, env.now)])

    # Pull as producer
    with _client() as client:
        response = client.get("/sync/pull?since=0&limit=500", headers=_auth(producer_token))
    assert response.status_code == 200, response.text
    data = response.json()

    returned_ids = [c["id"] for c in data["changes"]]
    versions = [c["server_version"] for c in data["changes"]]

    # Strictly sorted by server_version ascending
    assert versions == sorted(versions)

    # Positive assertions: contains mine entries and visits, and second org entry
    assert str(entry1_id) in returned_ids
    assert str(visit1_id) in returned_ids
    assert str(entry2_id) in returned_ids
    assert str(second_entry_id) in returned_ids

    # Negative assertion: theirs entry never comes back
    assert str(theirs_entry_id) not in returned_ids


async def test_since_excludes_earlier_rows_and_paging_walks_rows_exactly_once(
    env: SyncEnv,
) -> None:
    """`since` excludes rows at or below it; paging with `limit` walks every row exactly once
    and the last page has `has_more: false`; empty page -> `next_since == since`.
    """
    token = env.mine.tokens["producer"]
    tech_token = env.mine.tokens["technician"]

    ids: list[UUID] = []
    for i in range(3):
        eid = uuid7()
        ids.append(eid)
        _push(token, [_entry_payload(eid, env.mine.plot_id, env.now + timedelta(seconds=i))])

    for i in range(2):
        vid = uuid7()
        ids.append(vid)
        _push(
            tech_token,
            [
                _visit_payload(
                    vid,
                    env.mine.farm_id,
                    env.mine.users["technician"],
                    env.now + timedelta(seconds=i + 3),
                )
            ],
        )

    # Walk page by page with limit=2
    walked_ids: list[str] = []
    cursor = 0
    with _client() as client:
        while True:
            resp = client.get(f"/sync/pull?since={cursor}&limit=2", headers=_auth(token))
            assert resp.status_code == 200, resp.text
            page = resp.json()
            page_changes = page["changes"]

            for c in page_changes:
                # since excludes rows at or below it
                assert c["server_version"] > cursor
                walked_ids.append(c["id"])

            if not page["has_more"]:
                # Last page has has_more: false
                assert page["has_more"] is False
                cursor = page["next_since"]
                break

            cursor = page["next_since"]

        # An additional pull when nothing remains gives empty page and next_since == since
        empty_resp = client.get(f"/sync/pull?since={cursor}&limit=2", headers=_auth(token))
        assert empty_resp.status_code == 200
        empty_page = empty_resp.json()
        assert empty_page["changes"] == []
        assert empty_page["has_more"] is False
        assert empty_page["next_since"] == cursor

    # Every row walked exactly once
    str_ids = [str(x) for x in ids]
    for target in str_ids:
        assert walked_ids.count(target) == 1


async def test_tombstoned_row_returns_delete_op_with_deleted_at(env: SyncEnv) -> None:
    """A tombstoned row comes back as op: 'delete' with its deleted_at."""
    token = env.mine.tokens["producer"]
    entry_id = uuid7()
    _push(token, [_entry_payload(entry_id, env.mine.plot_id, env.now)])

    # Delete the entry
    delete_change = {
        "id": str(entry_id),
        "entity": "logbook_entry",
        "op": "delete",
        "client_updated_at": (env.now + timedelta(hours=1)).isoformat(),
        "data": {
            "plot_id": str(env.mine.plot_id),
            "kind": "observation",
            "occurred_on": "2026-09-28",
        },
    }
    _push(token, [delete_change])

    with _client() as client:
        resp = client.get("/sync/pull?since=0&limit=500", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()

    deleted_changes = [c for c in data["changes"] if c["id"] == str(entry_id)]
    assert len(deleted_changes) == 1
    tombstone = deleted_changes[0]

    assert tombstone["op"] == "delete"
    assert tombstone["data"]["deleted_at"] is not None

    # Negative assertion: live entry has op: "upsert" and deleted_at is None
    live_id = uuid7()
    _push(token, [_entry_payload(live_id, env.mine.plot_id, env.now)])
    with _client() as client:
        resp_live = client.get("/sync/pull?since=0&limit=500", headers=_auth(token))
    live_changes = [c for c in resp_live.json()["changes"] if c["id"] == str(live_id)]
    assert len(live_changes) == 1
    assert live_changes[0]["op"] == "upsert"
    assert live_changes[0]["data"]["deleted_at"] is None


async def test_entry_json_amounts_are_numbers_and_data_keys_match_web_types(
    env: SyncEnv,
) -> None:
    """The JSON of an entry has amounts as numbers (isinstance(v, (int, float)))
    and every key the web type lists.
    """
    token = env.mine.tokens["producer"]
    tech_token = env.mine.tokens["technician"]
    entry_id = uuid7()
    visit_id = uuid7()

    harvest_data = {
        "yield_kg": 250.75,
        "sold_kg": 150.0,
        "sale_price_cop_per_kg": 3200.0,
        "cost_cop": 85000.0,
        "quantity": 250.75,
    }
    _push(
        token,
        [_entry_payload(entry_id, env.mine.plot_id, env.now, kind="harvest", **harvest_data)],
    )
    _push(
        tech_token,
        [_visit_payload(visit_id, env.mine.farm_id, env.mine.users["technician"], env.now)],
    )

    with _client() as client:
        resp = client.get("/sync/pull?since=0&limit=500", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()

    entry_change = next(c for c in data["changes"] if c["id"] == str(entry_id))
    entry_data = entry_change["data"]

    # Numeric amounts serialized as JSON numbers, not strings
    for field in ("quantity", "yield_kg", "sold_kg", "sale_price_cop_per_kg", "cost_cop"):
        val = entry_data[field]
        assert isinstance(val, (int, float)) and not isinstance(val, bool), (
            f"{field} is {type(val)}"
        )

    # Match exact web type keys
    expected_entry_keys = {
        "id",
        "org_id",
        "plot_id",
        "crop_cycle_id",
        "kind",
        "occurred_on",
        "quantity",
        "unit",
        "cost_cop",
        "yield_kg",
        "sold_kg",
        "sale_price_cop_per_kg",
        "labor_days",
        "irrigation_mm",
        "alert_id",
        "notes",
        "created_by",
        "created_offline",
        "client_updated_at",
        "deleted_at",
    }
    assert set(entry_data.keys()) == expected_entry_keys
    # server_version goes on the change, NOT inside data
    assert "server_version" not in entry_data
    assert "server_version" in entry_change

    visit_change = next(c for c in data["changes"] if c["id"] == str(visit_id))
    visit_data = visit_change["data"]
    expected_visit_keys = {
        "id",
        "org_id",
        "farm_id",
        "plot_id",
        "technician_id",
        "visited_on",
        "topics",
        "recommendations",
        "commitments",
        "notes",
        "client_updated_at",
        "deleted_at",
    }
    assert set(visit_data.keys()) == expected_visit_keys
    assert "server_version" not in visit_data
    assert "server_version" in visit_change


async def test_pull_validation_and_auth(env: SyncEnv) -> None:
    """limit 0 or 501 -> 422; since < 0 -> 422; no bearer -> 401."""
    token = env.mine.tokens["producer"]
    with _client() as client:
        # limit bounds 1..500
        assert client.get("/sync/pull?limit=0", headers=_auth(token)).status_code == 422
        assert client.get("/sync/pull?limit=501", headers=_auth(token)).status_code == 422
        # since bound >= 0
        assert client.get("/sync/pull?since=-1", headers=_auth(token)).status_code == 422
        # missing auth -> 401
        assert client.get("/sync/pull").status_code == 401


async def _push_and_commit(
    pusher: Pusher, change: LogbookEntryChange, *, caller_id: UUID
) -> SyncStatus:
    result = await pusher.entry(change, caller_id=caller_id)
    await pusher.session.commit()
    return result.status


async def test_d1_pull_returns_push_committed_after_concurrent_lock(
    env: SyncEnv, pusher_for: type[Pusher]
) -> None:
    """D1 acceptance: a push committed after a concurrent lock is still returned by pull.

    With the D1 lock held by a raw connection, start a push (blocked), pull and record
    next_since, release the lock, let the push commit, pull again from the recorded cursor:
    the pushed row IS returned.
    """
    producer = env.mine.users["producer"]
    token = env.mine.tokens["producer"]

    # Seed with an initial row so cursor starts > 0
    seed_id = uuid7()
    _push(token, [_entry_payload(seed_id, env.mine.plot_id, env.now)])

    holder = await asyncpg.connect(_DSN)
    try:
        held = holder.transaction()
        await held.start()
        # Hold D1 lock on raw connection
        await holder.execute("SELECT pg_advisory_xact_lock($1)", SYNC_LOCK_KEY)

        # Prepare change for blocked push
        blocked_id = uuid7()
        change = LogbookEntryChange(
            id=blocked_id,
            op=SyncOp.UPSERT,
            client_updated_at=env.now + timedelta(seconds=10),
            plot_id=env.mine.plot_id,
            crop_cycle_id=None,
            kind="observation",
            occurred_on=date(2026, 9, 28),
            quantity=None,
            unit=None,
            cost_cop=None,
            yield_kg=None,
            sold_kg=None,
            sale_price_cop_per_kg=None,
            labor_days=None,
            irrigation_mm=None,
            alert_id=None,
            notes="blocked push",
            created_offline=False,
        )

        async with async_session_factory() as later:
            push_task = asyncio.ensure_future(
                _push_and_commit(pusher_for(later), change, caller_id=producer)
            )

            # Prove push is blocked
            _, pending = await asyncio.wait([push_task], timeout=_LOCK_WINDOW)
            assert pending, "the push did not wait for the D1 lock"

            # Pull while lock is held and record cursor
            with _client() as client:
                resp1 = client.get("/sync/pull?since=0", headers=_auth(token))
            assert resp1.status_code == 200
            page1 = resp1.json()
            recorded_cursor = page1["next_since"]
            # Negative assertion: blocked change is NOT in page1
            assert str(blocked_id) not in [c["id"] for c in page1["changes"]]

            # Release lock and let push commit
            await asyncio.wait_for(held.commit(), timeout=_NO_HANG)
            status = await asyncio.wait_for(push_task, timeout=_NO_HANG)
            assert status is SyncStatus.APPLIED

            # Pull again from recorded cursor
            with _client() as client:
                resp2 = client.get(f"/sync/pull?since={recorded_cursor}", headers=_auth(token))
            assert resp2.status_code == 200
            page2 = resp2.json()

            # Positive assertion: blocked change IS returned
            returned_ids2 = [c["id"] for c in page2["changes"]]
            assert str(blocked_id) in returned_ids2
    finally:
        await holder.close()


async def test_pull_caller_with_no_membership(env: SyncEnv) -> None:
    """A caller with no org membership gets an empty page with next_since == since (D2)."""
    unaffiliated_user = uuid7()
    token = issue_token(str(unaffiliated_user))
    with _client() as client:
        resp = client.get("/sync/pull?since=42", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"changes": [], "next_since": 42, "has_more": False}


async def test_pull_viewer_in_scope(env: SyncEnv) -> None:
    """A viewer has read access to their org and receives pull changes (D2)."""
    producer_token = env.mine.tokens["producer"]
    technician_token = env.mine.tokens["technician"]
    viewer_token = env.mine.tokens["viewer"]

    entry_id = uuid7()
    visit_id = uuid7()
    _push(producer_token, [_entry_payload(entry_id, env.mine.plot_id, env.now)])
    _push(
        technician_token,
        [
            _visit_payload(
                visit_id,
                env.mine.farm_id,
                env.mine.users["technician"],
                env.now,
            ),
        ],
    )

    with _client() as client:
        resp = client.get("/sync/pull?since=0", headers=_auth(viewer_token))
    assert resp.status_code == 200
    page = resp.json()
    returned_ids = [c["id"] for c in page["changes"]]
    assert str(entry_id) in returned_ids
    assert str(visit_id) in returned_ids


async def test_pull_repeatable_read_snapshot_prevents_cursor_gap(
    env: SyncEnv, pusher_for: type[Pusher], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3-pull-two-query-cursor-gap: pull reads must see a single snapshot.

    If an entry commits at version N and a visit at version N+1 between the two
    reads, the page must not return the visit at N+1 while missing the entry at N
    and advancing next_since to N+1 (which would permanently skip version N).
    Under REPEATABLE READ, both reads see ONE snapshot: page 1 excludes both
    concurrent rows, and the subsequent pull from next_since returns BOTH rows.
    """
    producer = env.mine.users["producer"]
    token = env.mine.tokens["producer"]

    seed_id = uuid7()
    _push(token, [_entry_payload(seed_id, env.mine.plot_id, env.now)])

    concurrent_entry_id = uuid7()
    concurrent_visit_id = uuid7()

    entry_change = LogbookEntryChange(
        id=concurrent_entry_id,
        op=SyncOp.UPSERT,
        client_updated_at=env.now + timedelta(seconds=1),
        plot_id=env.mine.plot_id,
        crop_cycle_id=None,
        kind="observation",
        occurred_on=date(2026, 9, 28),
        quantity=None,
        unit=None,
        cost_cop=None,
        yield_kg=None,
        sold_kg=None,
        sale_price_cop_per_kg=None,
        labor_days=None,
        irrigation_mm=None,
        alert_id=None,
        notes="concurrent entry",
        created_offline=False,
    )
    visit_change = ExtensionVisitChange(
        id=concurrent_visit_id,
        op=SyncOp.UPSERT,
        client_updated_at=env.now + timedelta(seconds=1),
        farm_id=env.mine.farm_id,
        plot_id=None,
        technician_id=env.mine.users["technician"],
        visited_on=date(2026, 9, 28),
        topics=["natural_resources"],
        recommendations=None,
        commitments=None,
        notes="concurrent visit",
    )

    orig_entries_list_for_pull = SqlAlchemyLogbookEntrySyncRepository.list_for_pull
    orig_visits_list_for_pull = SqlAlchemyExtensionVisitSyncRepository.list_for_pull
    entries_read = False
    hook_fired_after_entries = False

    async def hooked_entries_list_for_pull(
        self: Any, org_ids: Any, *, since: int, limit: int
    ) -> Any:
        nonlocal entries_read
        res = await orig_entries_list_for_pull(self, org_ids, since=since, limit=limit)
        entries_read = True
        return res

    async def hooked_visits_list_for_pull(
        self: Any, org_ids: Any, *, since: int, limit: int
    ) -> Any:
        nonlocal hook_fired_after_entries
        if not hook_fired_after_entries:
            assert entries_read, "Interleaving hook must fire AFTER entries read"
            hook_fired_after_entries = True
            async with async_session_factory() as second_session:
                pusher = pusher_for(second_session)
                res_e = await pusher.entry(entry_change, caller_id=producer)
                assert res_e.status is SyncStatus.APPLIED
                res_v = await pusher.visit(visit_change, caller_id=env.mine.users["technician"])
                assert res_v.status is SyncStatus.APPLIED
                await second_session.commit()
        return await orig_visits_list_for_pull(self, org_ids, since=since, limit=limit)

    monkeypatch.setattr(
        SqlAlchemyLogbookEntrySyncRepository, "list_for_pull", hooked_entries_list_for_pull
    )
    monkeypatch.setattr(
        SqlAlchemyExtensionVisitSyncRepository, "list_for_pull", hooked_visits_list_for_pull
    )

    with _client() as client:
        resp = client.get("/sync/pull?since=0", headers=_auth(token))
    assert resp.status_code == 200
    page = resp.json()
    change_ids = [c["id"] for c in page["changes"]]

    # Assert that the hook indeed fired after the entries read
    assert hook_fired_after_entries is True, "Interleaving hook did not fire after entries read"

    # Under REPEATABLE READ, page 1 MUST exclude both concurrent rows
    assert str(concurrent_entry_id) not in change_ids
    assert str(concurrent_visit_id) not in change_ids

    # Unconditionally pull from next_since and assert BOTH concurrent rows are returned
    with _client() as client:
        resp2 = client.get(f"/sync/pull?since={page['next_since']}", headers=_auth(token))
    assert resp2.status_code == 200
    page2 = resp2.json()
    change_ids2 = [c["id"] for c in page2["changes"]]
    assert str(concurrent_entry_id) in change_ids2
    assert str(concurrent_visit_id) in change_ids2


async def test_pull_fails_when_session_already_in_transaction(env: SyncEnv) -> None:
    """R3-001: pull fails loudly (500) if session was used before handler established isolation."""
    from collections.abc import AsyncIterator

    token = env.mine.tokens["producer"]

    async def dirty_session() -> AsyncIterator[AsyncSession]:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            assert session.in_transaction()
            yield session

    app.dependency_overrides[get_session] = dirty_session
    try:
        with _client() as client:
            resp = client.get("/sync/pull?since=0", headers=_auth(token))
        assert resp.status_code == 500
        problem = resp.json()
        assert problem["title"] == "Session already in transaction"
    finally:
        app.dependency_overrides.pop(get_session, None)
