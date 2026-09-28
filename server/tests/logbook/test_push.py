"""Sync push decisions through the real repositories (docs/04 §Bitácora;
docs/06 §7; docs/03 §logbook_entry, §extension_visit; D1-D13).

Every row of docs/04's push table is a test here, and each one carries its
negative assertion: what must NOT be written, or what must not change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.logbook.adapters.orm import ExtensionVisitRow, LogbookEntryRow
from techcamp.logbook.adapters.repositories import (
    SYNC_LOCK_KEY,
    SqlAlchemyLogbookEntrySyncRepository,
)
from techcamp.logbook.application.ports import ExtensionVisitChange, LogbookEntryChange
from techcamp.logbook.domain.errors import InvalidEntryError
from techcamp.logbook.domain.models import LogbookKind, RejectReason, SyncOp, SyncStatus
from techcamp.shared.config import database_url
from techcamp.shared.db import async_session_factory
from techcamp.shared.ids import uuid7

if TYPE_CHECKING:  # the fixture types live in the conftest, which pytest owns
    from tests.logbook.conftest import Pusher, SyncEnv

pytestmark = pytest.mark.anyio

_DSN = make_url(database_url()).set(drivername="postgresql").render_as_string(hide_password=False)
"""`DATABASE_URL` as a plain libpq URI for the raw connection below, so the
lane's own test database is used instead of a hardcoded port."""
_NO_HANG = 30.0
"""Guard, not synchronization: a regression in the lock or the write fails the
test instead of hanging it."""
_LOCK_WINDOW = 1.0
"""How long the blocked push is given to prove it is blocked. It asserts on the
push still being pending, never on the push having finished."""


def _later(env: SyncEnv) -> datetime:
    """One hour after the change's clock: the newer of two devices editing the
    same entry offline (ADR-0013)."""
    return env.now + timedelta(hours=1)


def _earlier(env: SyncEnv) -> datetime:
    return env.now - timedelta(hours=1)


def _entry(env: SyncEnv, **overrides: Any) -> LogbookEntryChange:
    """A `logbook_entry` upsert on the caller's plot. The default kind is
    `observation`, the one D10 asks no field of, so a test states only the
    rule it exercises."""
    base: dict[str, Any] = {
        "id": uuid7(),
        "op": SyncOp.UPSERT,
        "client_updated_at": env.now,
        "plot_id": env.mine.plot_id,
        "crop_cycle_id": None,
        "kind": LogbookKind.OBSERVATION.value,
        "occurred_on": env.occurred_on,
        "quantity": Decimal("12"),
        "unit": "kg",
        "cost_cop": None,
        "yield_kg": None,
        "sold_kg": None,
        "sale_price_cop_per_kg": None,
        "labor_days": None,
        "irrigation_mm": None,
        "alert_id": None,
        "notes": None,
    }
    return LogbookEntryChange(**{**base, **overrides})


def _visit(env: SyncEnv, **overrides: Any) -> ExtensionVisitChange:
    """An `extension_visit` upsert on the caller's farm, signed by the
    technician that owns it."""
    base: dict[str, Any] = {
        "id": uuid7(),
        "op": SyncOp.UPSERT,
        "client_updated_at": env.now,
        "farm_id": env.mine.farm_id,
        "plot_id": None,
        "technician_id": env.mine.users["technician"],
        "visited_on": env.occurred_on,
        "topics": ["natural_resources"],
        "recommendations": None,
        "commitments": None,
        "notes": None,
    }
    return ExtensionVisitChange(**{**base, **overrides})


async def _stored_entry(session: AsyncSession, entry_id: UUID) -> LogbookEntryRow | None:
    """The stored row, re-read with `populate_existing` so it never answers
    from an attribute a rolled-back savepoint expired."""
    return (
        await session.execute(
            select(LogbookEntryRow)
            .where(LogbookEntryRow.id == entry_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _stored_visit(session: AsyncSession, visit_id: UUID) -> ExtensionVisitRow | None:
    return (
        await session.execute(
            select(ExtensionVisitRow)
            .where(ExtensionVisitRow.id == visit_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _push_and_commit(
    pusher: Pusher, change: LogbookEntryChange, *, caller_id: UUID
) -> SyncStatus:
    """One change in its own transaction, the way a request runs it."""
    result = await pusher.entry(change, caller_id=caller_id)
    await pusher.session.commit()
    return result.status


async def _entry_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(LogbookEntryRow))).scalar_one()


async def _visit_count(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(ExtensionVisitRow))).scalar_one()


async def _push_and_commit(pusher: Pusher, change: LogbookEntryChange, *, caller_id: UUID) -> str:
    """One change in its own transaction, the way a request runs it."""
    result = await pusher.entry(change, caller_id=caller_id)
    await pusher.session.commit()
    return result.status


async def test_a_new_entry_is_applied_once_and_the_same_change_again_is_a_duplicate(
    env: SyncEnv, pusher: Pusher
) -> None:
    """docs/04 §Bitácora: no row → `applied`; same id and same
    `client_updated_at` → `duplicate`, no write."""
    producer = env.mine.users["producer"]
    change = _entry(env, notes="cosecha 120 kg")

    first = await pusher.entry(change, caller_id=producer)
    stored = await _stored_entry(pusher.session, change.id)
    second = await pusher.entry(change, caller_id=producer)

    assert (first.status, first.error) == (SyncStatus.APPLIED, None)
    assert first.server_version is not None
    assert stored is not None
    assert stored.created_by == producer
    assert stored.created_offline is True
    assert stored.client_updated_at == env.now
    assert stored.notes == "cosecha 120 kg"
    assert (second.status, second.server_version) == (SyncStatus.DUPLICATE, first.server_version)
    assert await _entry_count(pusher.session) == 1


async def test_the_newer_client_updated_at_wins_and_the_older_push_is_conflict_overwritten(
    env: SyncEnv, pusher: Pusher
) -> None:
    """ADR-0013 LWW: the older push is not written and answers with the stored
    `server_version` (docs/04 §Bitácora)."""
    producer = env.mine.users["producer"]
    change = _entry(env, notes="primera")
    applied = await pusher.entry(change, caller_id=producer)
    assert applied.status is SyncStatus.APPLIED

    stale = await pusher.entry(
        _entry(env, id=change.id, client_updated_at=_earlier(env), notes="de otro dispositivo"),
        caller_id=producer,
    )
    stored_after_stale = await _stored_entry(pusher.session, change.id)

    assert (stale.status, stale.error) == (SyncStatus.CONFLICT_OVERWRITTEN, None)
    assert stale.server_version == applied.server_version
    assert stored_after_stale is not None
    assert stored_after_stale.notes == "primera"
    assert stored_after_stale.server_version == applied.server_version

    newer = await pusher.entry(
        _entry(env, id=change.id, client_updated_at=_later(env), notes="revisada"),
        caller_id=producer,
    )

    assert newer.status is SyncStatus.APPLIED
    assert newer.server_version is not None and newer.server_version > applied.server_version
    assert await _entry_count(pusher.session) == 1


async def test_a_rejected_change_rolls_back_its_own_savepoint_and_the_batch_continues(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D5: each change runs in its own savepoint, so one rejection never sinks
    the batch."""
    producer = env.mine.users["producer"]
    # `harvest` without `yield_kg` fails the D10 rule the domain owns.
    invalid = _entry(env, kind=LogbookKind.HARVEST.value, yield_kg=None)
    valid = _entry(env, notes="sigue")

    rejected = await pusher.entry(invalid, caller_id=producer)
    applied = await pusher.entry(valid, caller_id=producer)

    assert (rejected.status, rejected.error) == (SyncStatus.REJECTED, RejectReason.INVALID)
    assert (applied.status, applied.error) == (SyncStatus.APPLIED, None)
    assert await _entry_count(pusher.session) == 1
    assert await _stored_entry(pusher.session, invalid.id) is None


async def test_a_viewer_may_not_push_a_logbook_entry(env: SyncEnv, pusher: Pusher) -> None:
    """D3: `logbook_entry` is `owner`, `technician` or `producer`; `viewer` is
    `forbidden`."""
    change = _entry(env)

    result = await pusher.entry(change, caller_id=env.mine.users["viewer"])

    assert (result.status, result.error) == (SyncStatus.REJECTED, RejectReason.FORBIDDEN)
    assert await _entry_count(pusher.session) == 0


async def test_a_visit_needs_the_technician_role_and_the_caller_as_its_technician(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D3: `extension_visit` is `technician` only, and `technician_id` must be
    the caller (docs/03 §extension_visit invariant)."""
    by_producer = _visit(env, technician_id=env.mine.users["producer"])
    by_another = _visit(env, technician_id=env.mine.users["owner"])

    as_producer = await pusher.visit(by_producer, caller_id=env.mine.users["producer"])
    as_technician = await pusher.visit(by_another, caller_id=env.mine.users["technician"])

    assert (as_producer.status, as_producer.error) == (SyncStatus.REJECTED, RejectReason.FORBIDDEN)
    assert (as_technician.status, as_technician.error) == (
        SyncStatus.REJECTED,
        RejectReason.FORBIDDEN,
    )
    assert await _visit_count(pusher.session) == 0


async def test_a_visit_signed_by_the_technician_applies(env: SyncEnv, pusher: Pusher) -> None:
    """The same visit the rule above refuses is applied when the technician
    pushes it, so the rejection is the rule and not a dead branch."""
    change = _visit(env, recommendations="rotar el cultivo")

    result = await pusher.visit(change, caller_id=env.mine.users["technician"])
    stored = await _stored_visit(pusher.session, change.id)

    assert (result.status, result.error) == (SyncStatus.APPLIED, None)
    assert stored is not None
    assert stored.org_id == env.mine.org_id
    assert stored.topics == ["natural_resources"]
    assert await _visit_count(pusher.session) == 1


async def test_a_client_timestamp_more_than_24_h_ahead_is_clock_skew(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D5: the phone's clock is wrong, so the change is rejected and kept
    local (docs/06 §7)."""
    change = _entry(env, client_updated_at=env.now + timedelta(hours=25))

    result = await pusher.entry(change, caller_id=env.mine.users["producer"])

    assert (result.status, result.error) == (SyncStatus.REJECTED, RejectReason.CLOCK_SKEW)
    assert await _entry_count(pusher.session) == 0


async def test_an_id_of_another_org_is_not_found_and_writes_nothing_in_either_org(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D4 and docs/09: an id another org owns answers like a missing one, and
    the foreign row is left untouched."""
    session = pusher.session
    foreign_id = uuid7()
    session.add(
        LogbookEntryRow(
            id=foreign_id,
            org_id=env.theirs.org_id,
            plot_id=env.theirs.plot_id,
            crop_cycle_id=None,
            kind=LogbookKind.OBSERVATION.value,
            occurred_on=env.occurred_on,
            quantity=Decimal("5"),
            created_by=env.theirs.users["owner"],
            created_offline=False,
            client_updated_at=_earlier(env),
            notes="de la otra organización",
        )
    )
    await session.commit()

    result = await pusher.entry(
        _entry(env, id=foreign_id, notes="mío"), caller_id=env.mine.users["producer"]
    )
    foreign = await _stored_entry(session, foreign_id)

    assert (result.status, result.error) == (SyncStatus.REJECTED, RejectReason.NOT_FOUND)
    assert foreign is not None
    assert foreign.org_id == env.theirs.org_id
    assert foreign.notes == "de la otra organización"
    assert foreign.client_updated_at == _earlier(env)


async def test_an_id_of_the_other_entity_is_not_found(env: SyncEnv, pusher: Pusher) -> None:
    """D4: the two tables have independent primary keys, so without the probe
    an entry id reused for a visit would insert and pull would return one id
    twice."""
    technician = env.mine.users["technician"]
    visit = _visit(env)
    entry = _entry(env)
    assert (await pusher.visit(visit, caller_id=technician)).status is SyncStatus.APPLIED
    assert (await pusher.entry(entry, caller_id=technician)).status is SyncStatus.APPLIED

    entry_over_visit = await pusher.entry(_entry(env, id=visit.id), caller_id=technician)
    visit_over_entry = await pusher.visit(_visit(env, id=entry.id), caller_id=technician)

    assert (entry_over_visit.status, entry_over_visit.error) == (
        SyncStatus.REJECTED,
        RejectReason.NOT_FOUND,
    )
    assert (visit_over_entry.status, visit_over_entry.error) == (
        SyncStatus.REJECTED,
        RejectReason.NOT_FOUND,
    )
    assert await _entry_count(pusher.session) == 1
    assert await _visit_count(pusher.session) == 1


async def test_an_upsert_on_a_deleted_id_is_not_found(env: SyncEnv, pusher: Pusher) -> None:
    """D13: deletes are final, there is no undelete, so a re-saved row is not
    restored."""
    producer = env.mine.users["producer"]
    change = _entry(env, notes="antes del borrado")
    applied = await pusher.entry(change, caller_id=producer)
    deleted = await pusher.entry(
        _entry(env, id=change.id, op=SyncOp.DELETE, client_updated_at=_later(env)),
        caller_id=producer,
    )
    assert deleted.status is SyncStatus.APPLIED

    again = await pusher.entry(
        _entry(env, id=change.id, client_updated_at=_later(env) + timedelta(hours=1)),
        caller_id=producer,
    )
    stored = await _stored_entry(pusher.session, change.id)

    assert applied.status is SyncStatus.APPLIED
    assert (again.status, again.error) == (SyncStatus.REJECTED, RejectReason.NOT_FOUND)
    assert stored is not None
    assert stored.deleted_at == env.now
    assert stored.server_version == deleted.server_version


async def test_a_delete_loses_to_a_newer_row_and_only_stamps_the_tombstone(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D12: the delete carries the row as last saved, the server stamps
    `deleted_at` and never validates the fields."""
    producer = env.mine.users["producer"]
    change = _entry(env, notes="contenido que el borrado no revalida")
    applied = await pusher.entry(change, caller_id=producer)

    stale = await pusher.entry(
        _entry(env, id=change.id, op=SyncOp.DELETE, client_updated_at=_earlier(env)),
        caller_id=producer,
    )
    stored_after_stale = await _stored_entry(pusher.session, change.id)
    assert stored_after_stale is not None
    assert (stale.status, stale.server_version) == (
        SyncStatus.CONFLICT_OVERWRITTEN,
        applied.server_version,
    )
    assert stored_after_stale.deleted_at is None

    won = await pusher.entry(
        _entry(env, id=change.id, op=SyncOp.DELETE, client_updated_at=_later(env)),
        caller_id=producer,
    )
    stored = await _stored_entry(pusher.session, change.id)

    assert won.status is SyncStatus.APPLIED
    assert won.server_version is not None and won.server_version > applied.server_version
    assert stored is not None
    assert stored.deleted_at == env.now
    assert stored.client_updated_at == _later(env)
    assert stored.notes == "contenido que el borrado no revalida"


async def test_a_never_stored_delete_is_applied_without_writing(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D12: created and deleted offline, nobody else has it, so there is
    nothing to write and no version to answer."""
    change = _entry(env, op=SyncOp.DELETE)

    result = await pusher.entry(change, caller_id=env.mine.users["producer"])

    assert (result.status, result.server_version, result.error) == (SyncStatus.APPLIED, None, None)
    assert await _entry_count(pusher.session) == 0


async def test_an_alert_of_another_plot_is_alert_plot_mismatch(
    env: SyncEnv, pusher: Pusher
) -> None:
    """docs/06 §7: the server checks the alert is of the same plot; the alert of
    the entry's own plot is accepted, so the check is not a blanket refusal."""
    producer = env.mine.users["producer"]

    mismatched = await pusher.entry(_entry(env, alert_id=env.other_alert_id), caller_id=producer)
    linked = await pusher.entry(_entry(env, alert_id=env.alert_id), caller_id=producer)

    assert (mismatched.status, mismatched.error) == (
        SyncStatus.REJECTED,
        RejectReason.ALERT_PLOT_MISMATCH,
    )
    assert (linked.status, linked.error) == (SyncStatus.APPLIED, None)
    assert await _entry_count(pusher.session) == 1
    assert await _stored_entry(pusher.session, linked.id) is not None


async def test_a_crop_cycle_of_another_plot_is_not_found(env: SyncEnv, pusher: Pusher) -> None:
    """D6: `crop_cycle_id` is nullable, but when present it must be a cycle of
    the same plot."""
    producer = env.mine.users["producer"]

    mismatched = await pusher.entry(
        _entry(env, crop_cycle_id=env.other_cycle_id), caller_id=producer
    )
    linked = await pusher.entry(_entry(env, crop_cycle_id=env.cycle_id), caller_id=producer)

    assert (mismatched.status, mismatched.error) == (SyncStatus.REJECTED, RejectReason.NOT_FOUND)
    assert (linked.status, linked.error) == (SyncStatus.APPLIED, None)
    assert await _entry_count(pusher.session) == 1


async def test_a_visit_with_a_plot_outside_its_farm_is_not_found(
    env: SyncEnv, pusher: Pusher
) -> None:
    """docs/03 §extension_visit: `plot_id`, when present, is a plot of
    `farm_id`."""
    change = _visit(env, plot_id=env.mine.other_plot_id)

    result = await pusher.visit(change, caller_id=env.mine.users["technician"])

    assert (result.status, result.error) == (SyncStatus.REJECTED, RejectReason.NOT_FOUND)
    assert await _visit_count(pusher.session) == 0


async def test_a_topic_outside_the_five_is_invalid(env: SyncEnv, pusher: Pusher) -> None:
    """D5 `invalid`: the closed vocabulary of Ley 1876 art. 25 (docs/03)."""
    change = _visit(env, topics=["natural_resources", "agroforestal"])

    result = await pusher.visit(change, caller_id=env.mine.users["technician"])

    assert (result.status, result.error) == (SyncStatus.REJECTED, RejectReason.INVALID)
    assert await _visit_count(pusher.session) == 0


async def test_a_check_violation_the_domain_did_not_catch_is_invalid_and_the_batch_continues(
    env: SyncEnv, pusher: Pusher
) -> None:
    """D5's backstop: the per-`kind` CHECKs are the table's last line, so a
    violation that reached the adapter comes back as `invalid`, never a 500,
    and the savepoint leaves the session able to take the next change."""
    producer = env.mine.users["producer"]
    change = _entry(env, kind="spraying")
    repository = SqlAlchemyLogbookEntrySyncRepository(pusher.session)

    # Driven through a savepoint, the way the use case drives it.
    with pytest.raises(InvalidEntryError):
        async with pusher.session.begin_nested():
            await repository.insert(
                change, org_id=env.mine.org_id, caller_id=producer, server_version=1
            )
    applied = await pusher.entry(_entry(env, notes="sigue"), caller_id=producer)

    assert applied.status is SyncStatus.APPLIED
    assert await _entry_count(pusher.session) == 1
    assert await _stored_entry(pusher.session, change.id) is None


async def test_two_concurrent_pushes_of_the_same_id_leave_the_newer_row(
    env: SyncEnv, pusher: Pusher, pusher_for: type[Pusher]
) -> None:
    """Two devices edit one entry offline (ADR-0013): the D1 lock serializes
    them, so one row survives, it holds the newer `client_updated_at`, and the
    loser is told instead of both winning."""
    producer = env.mine.users["producer"]
    entry_id = uuid7()
    older = _entry(env, id=entry_id, client_updated_at=_earlier(env), notes="del teléfono viejo")
    newer = _entry(env, id=entry_id, client_updated_at=_later(env), notes="del teléfono nuevo")

    async with (
        async_session_factory() as first,
        async_session_factory() as second,
    ):
        statuses = await asyncio.wait_for(
            asyncio.gather(
                _push_and_commit(pusher_for(first), older, caller_id=producer),
                _push_and_commit(pusher_for(second), newer, caller_id=producer),
            ),
            timeout=_NO_HANG,
        )

    stored = await _stored_entry(pusher.session, entry_id)
    assert stored is not None
    assert stored.client_updated_at == _later(env)
    assert stored.notes == "del teléfono nuevo"
    # Whoever lost the race is told; nobody is rejected, and nobody is told it
    # won when it did not.
    assert set(statuses) <= {SyncStatus.APPLIED, SyncStatus.CONFLICT_OVERWRITTEN}
    assert SyncStatus.APPLIED in statuses
    assert await _entry_count(pusher.session) == 1


async def test_a_push_that_takes_the_lock_later_gets_a_strictly_greater_server_version(
    env: SyncEnv, pusher: Pusher, pusher_for: type[Pusher]
) -> None:
    """D1: the version is taken under `pg_advisory_xact_lock`, so a push cannot
    allocate one while another transaction holds the lock, and the push that
    takes it later gets the greater version. That is what keeps a client that
    already pulled `since=N` from missing an earlier row (docs/06 §7).

    The lock holder is a plain asyncpg connection in its own transaction, not a
    second `AsyncSession`: two SQLAlchemy sessions doing concurrent work in one
    event loop wedge the client side here (the holder's `commit()` never
    resolves while the other session is blocked), and no request holds two
    sessions. What this test needs is "some other transaction holds the lock".
    """
    producer = env.mine.users["producer"]
    change = _entry(env)
    holder = await asyncpg.connect(_DSN)
    try:
        held = holder.transaction()
        await held.start()
        await holder.execute("SELECT pg_advisory_xact_lock($1)", SYNC_LOCK_KEY)
        before = await holder.fetchval("SELECT nextval('sync_server_version_seq')")

        async with async_session_factory() as later:
            task = asyncio.ensure_future(
                _push_and_commit(pusher_for(later), change, caller_id=producer)
            )
            _, pending = await asyncio.wait([task], timeout=_LOCK_WINDOW)
            # The push is still waiting for the lock, so it has allocated
            # nothing: that is the guarantee, not a timing coincidence.
            assert pending, "the push allocated a server_version without holding the D1 lock"

            await asyncio.wait_for(held.commit(), timeout=_NO_HANG)
            status = await asyncio.wait_for(task, timeout=_NO_HANG)
    finally:
        await holder.close()

    assert status is SyncStatus.APPLIED
    stored = await _stored_entry(pusher.session, change.id)
    assert stored is not None
    assert stored.server_version > before
