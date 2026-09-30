"""Tests for logbook home facade read queries (E9 T1b; docs/04 §Visitas; D-T0.8)."""

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.logbook.adapters.orm import ExtensionVisitRow
from techcamp.logbook.adapters.repositories import SqlAlchemyExtensionVisitRepository
from techcamp.logbook.application import get_latest_visit_dates
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"


async def _make_context(
    db_session: AsyncSession, name: str = "Org Visits"
) -> tuple[UUID, UUID, UUID]:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name=name, municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    tech_id = uuid7()
    db_session.add(AppUserRow(id=tech_id, phone=f"+57{uuid7().int % 10**13:013d}"))
    await db_session.commit()
    return org_id, farm_id, tech_id


async def _insert_visit(
    db_session: AsyncSession,
    *,
    org_id: UUID,
    farm_id: UUID,
    technician_id: UUID,
    visited_on: date,
    deleted_at: datetime | None = None,
) -> UUID:
    visit_id = uuid7()
    db_session.add(
        ExtensionVisitRow(
            id=visit_id,
            org_id=org_id,
            farm_id=farm_id,
            plot_id=None,
            technician_id=technician_id,
            visited_on=visited_on,
            topics=["natural_resources"],
            client_updated_at=datetime.now(UTC),
            deleted_at=deleted_at,
        )
    )
    await db_session.commit()
    return visit_id


async def test_get_latest_visit_dates_returns_max_visited_on_and_excludes_deleted(
    db_session: AsyncSession,
) -> None:
    org_a, farm_1, tech_a = await _make_context(db_session, "Org A")
    # Add a second farm in org_a
    farm_2 = uuid7()
    db_session.add(
        FarmRow(id=farm_2, org_id=org_a, name="Farm 2", municipality_code="47001", location=_POINT)
    )
    # Add a third farm in org_a with no visits
    farm_empty = uuid7()
    db_session.add(
        FarmRow(
            id=farm_empty,
            org_id=org_a,
            name="Farm Empty",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()

    # Org B with a farm
    org_b, farm_b, tech_b = await _make_context(db_session, "Org B")

    # Farm 1 visits:
    # Older visit: 2026-09-10
    await _insert_visit(
        db_session, org_id=org_a, farm_id=farm_1, technician_id=tech_a, visited_on=date(2026, 9, 10)
    )
    # Newer visit: 2026-09-20
    await _insert_visit(
        db_session, org_id=org_a, farm_id=farm_1, technician_id=tech_a, visited_on=date(2026, 9, 20)
    )
    # Even newer visit: 2026-09-25, but DELETED -> must be ignored!
    await _insert_visit(
        db_session,
        org_id=org_a,
        farm_id=farm_1,
        technician_id=tech_a,
        visited_on=date(2026, 9, 25),
        deleted_at=datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
    )

    # Farm 2 visit:
    await _insert_visit(
        db_session, org_id=org_a, farm_id=farm_2, technician_id=tech_a, visited_on=date(2026, 9, 15)
    )

    # Org B visit on farm_b:
    await _insert_visit(
        db_session, org_id=org_b, farm_id=farm_b, technician_id=tech_b, visited_on=date(2026, 9, 22)
    )

    repo = SqlAlchemyExtensionVisitRepository(db_session)

    # Query org_a with farms [farm_1, farm_2, farm_empty]
    res = await get_latest_visit_dates(
        farm_ids=[farm_1, farm_2, farm_empty],
        org_ids=[org_a],
        visits=repo,
    )

    # farm_1 has max non-deleted visit 2026-09-20 (not 2026-09-25 deleted)
    assert res.get(farm_1) == date(2026, 9, 20)
    # farm_2 has visit 2026-09-15
    assert res.get(farm_2) == date(2026, 9, 15)
    # farm_empty has no visits -> must be absent from res!
    assert farm_empty not in res

    # Org isolation check: querying farm_b under org_a must be absent
    res_iso = await get_latest_visit_dates(
        farm_ids=[farm_b],
        org_ids=[org_a],
        visits=repo,
    )
    assert farm_b not in res_iso


async def test_get_latest_visit_dates_empty_inputs(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyExtensionVisitRepository(db_session)
    assert await get_latest_visit_dates(farm_ids=[], org_ids=[uuid7()], visits=repo) == {}
    assert await get_latest_visit_dates(farm_ids=[uuid7()], org_ids=[], visits=repo) == {}
