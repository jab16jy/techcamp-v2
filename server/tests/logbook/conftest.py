"""Fixtures and wiring for the offline sync push (docs/04 §Bitácora; D1-D13).

Two organizations, and two farms in the first one, so every isolation test has
a real foreign row to reach for (docs/09-cuellos-de-botella.md#seguridad) and
the `plot_id ∈ farm_id` invariant (docs/03 §extension_visit) has a real
counterexample.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyCropRepository,
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
)
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.logbook.adapters.repositories import (
    PostgresSyncTransaction,
    SqlAlchemyExtensionVisitSyncRepository,
    SqlAlchemyLogbookEntrySyncRepository,
    SqlAlchemySyncIdProbe,
)
from techcamp.logbook.application.ports import (
    ExtensionVisitChange,
    LogbookEntryChange,
)
from techcamp.logbook.application.push import PushResult, push_extension_visit, push_logbook_entry
from techcamp.shared.ids import uuid7

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)  # 10:00 Bogotá
OCCURRED_ON = date(2026, 9, 28)
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@dataclass(frozen=True, slots=True)
class Org:
    """One organization: a user per role, a farm with a plot, and a second
    farm with its own plot (the `plot_id ∈ farm_id` counterexample)."""

    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    other_farm_id: UUID
    other_plot_id: UUID
    users: dict[str, UUID]
    tokens: dict[str, str]


@dataclass(frozen=True, slots=True)
class SyncEnv:
    """The world a push runs against, and the clock every test's change is
    written on (a fixed `now`, so a tombstone is assertable)."""

    now: datetime
    occurred_on: date
    mine: Org
    theirs: Org
    alert_id: UUID
    """An alert of `mine.plot_id` (the `alert_id` of an entry, docs/03)."""

    other_alert_id: UUID
    """An alert of `mine.other_plot_id` (the `alert_plot_mismatch` case)."""

    cycle_id: UUID
    """A crop cycle of `mine.plot_id` (D6's accepted reference)."""

    other_cycle_id: UUID
    """A crop cycle of `mine.other_plot_id` (D6's `not_found` case)."""


class Pusher:
    """The router's wiring, callable from a test.

    Applies one change on the given session and never commits: the use case
    runs inside the request's transaction, and the router (or the test) owns
    the commit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def entry(
        self, change: LogbookEntryChange, *, caller_id: UUID, now: datetime = NOW
    ) -> PushResult:
        return await push_logbook_entry(
            change,
            caller_id=caller_id,
            now=now,
            tx=PostgresSyncTransaction(self.session),
            ids=SqlAlchemySyncIdProbe(self.session),
            entries=SqlAlchemyLogbookEntrySyncRepository(self.session),
            memberships=SqlAlchemyMembershipRepository(self.session),
            plots=SqlAlchemyPlotRepository(self.session),
            cycles=SqlAlchemyCropCycleRepository(self.session),
            alerts=SqlAlchemyAlertRepository(self.session),
        )

    async def visit(
        self, change: ExtensionVisitChange, *, caller_id: UUID, now: datetime = NOW
    ) -> PushResult:
        return await push_extension_visit(
            change,
            caller_id=caller_id,
            now=now,
            tx=PostgresSyncTransaction(self.session),
            ids=SqlAlchemySyncIdProbe(self.session),
            visits=SqlAlchemyExtensionVisitSyncRepository(self.session),
            memberships=SqlAlchemyMembershipRepository(self.session),
            farms=SqlAlchemyFarmRepository(self.session),
            plots=SqlAlchemyPlotRepository(self.session),
        )


async def _org(session: AsyncSession, *, roles: tuple[str, ...], name: str) -> Org:
    org_id, farm_id, plot_id, other_farm_id, other_plot_id = (uuid7() for _ in range(5))
    users = {role: uuid7() for role in roles}
    for role, user_id in users.items():
        session.add(
            AppUserRow(
                id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=f"{name} {role}"
            )
        )
    session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    await session.flush()
    for member_role, user_id in users.items():
        session.add(MembershipRow(org_id=org_id, user_id=user_id, role=member_role))
    for farm, plot in ((farm_id, plot_id), (other_farm_id, other_plot_id)):
        session.add(
            FarmRow(
                id=farm,
                org_id=org_id,
                name=f"Finca {farm.hex[:6]}",
                municipality_code="47001",
                location=_POINT,
                technician_id=users.get("technician"),
            )
        )
        await session.flush()
        session.add(
            PlotRow(
                id=plot,
                org_id=org_id,
                farm_id=farm,
                name=f"Lote {plot.hex[:6]}",
                boundary=_BOUNDARY,
                irrigation_system="drip",
            )
        )
    await session.commit()
    return Org(
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        other_farm_id=other_farm_id,
        other_plot_id=other_plot_id,
        users=users,
        tokens={role: issue_token(str(user_id)) for role, user_id in users.items()},
    )


@pytest.fixture
async def env(db_session: AsyncSession) -> SyncEnv:
    """One organization with every role, a second one to be isolated from, and
    the alert/cycle references both plots of the first."""
    mine = await _org(db_session, roles=("owner", "technician", "producer", "viewer"), name="Mine")
    theirs = await _org(db_session, roles=("owner",), name="Theirs")
    rule_id = (
        await db_session.execute(select(AlertRuleRow.id).where(AlertRuleRow.code == "water_stress"))
    ).scalar_one()
    alert_id, other_alert_id = uuid7(), uuid7()
    for alert, plot_id in ((alert_id, mine.plot_id), (other_alert_id, mine.other_plot_id)):
        db_session.add(
            AlertRow(
                id=alert,
                org_id=mine.org_id,
                rule_id=rule_id,
                plot_id=plot_id,
                state="open",
                severity="warning",
                evidence={},
                opened_at=NOW,
            )
        )
    crop = (await SqlAlchemyCropRepository(db_session).list_all())[0]
    cycle_id, other_cycle_id = uuid7(), uuid7()
    for cycle, plot_id in ((cycle_id, mine.plot_id), (other_cycle_id, mine.other_plot_id)):
        db_session.add(
            CropCycleRow(
                id=cycle,
                plot_id=plot_id,
                crop_id=crop.id,
                sown_on=date(2026, 4, 1),
                status="active",
            )
        )
    await db_session.commit()
    return SyncEnv(
        now=NOW,
        occurred_on=OCCURRED_ON,
        mine=mine,
        theirs=theirs,
        alert_id=alert_id,
        other_alert_id=other_alert_id,
        cycle_id=cycle_id,
        other_cycle_id=other_cycle_id,
    )


@pytest.fixture
def pusher(db_session: AsyncSession) -> Pusher:
    """Pushes on the test's own session."""
    return Pusher(db_session)
