"""Tests for farms home facade read queries (E9 T1a; docs/04 §Visitas; D-T0.8)."""

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow
from techcamp.farms.adapters.repositories import SqlAlchemyFarmRepository
from techcamp.farms.application import list_farms_for_technician
from techcamp.identity.adapters.orm import AppUserRow
from techcamp.shared.ids import uuid7

from .test_repositories import _make_org

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"


async def test_list_farms_for_technician_returns_assigned_ordered_by_name(
    db_session: AsyncSession,
) -> None:
    org_1 = await _make_org(db_session, "Org 1")
    org_2 = await _make_org(db_session, "Org 2")
    org_other = await _make_org(db_session, "Org Other")

    technician_me = uuid7()
    technician_other = uuid7()
    db_session.add(AppUserRow(id=technician_me, phone="+573001111111"))
    db_session.add(AppUserRow(id=technician_other, phone="+573002222222"))
    await db_session.commit()

    # Farm Z in org 1 assigned to technician_me
    farm_z = uuid7()
    db_session.add(
        FarmRow(
            id=farm_z,
            org_id=cast_uuid(org_1),
            name="Zanahoria Farm",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician_me,
        )
    )

    # Farm A in org 2 assigned to technician_me
    farm_a = uuid7()
    db_session.add(
        FarmRow(
            id=farm_a,
            org_id=cast_uuid(org_2),
            name="Aguacate Farm",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician_me,
        )
    )

    # Farm in org 1 assigned to someone else (technician_other) -> must be excluded!
    farm_other_tech = uuid7()
    db_session.add(
        FarmRow(
            id=farm_other_tech,
            org_id=cast_uuid(org_1),
            name="Other Tech Farm",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician_other,
        )
    )

    # Farm in org_other assigned to technician_me, but org_other NOT in member orgs
    # -> must be excluded!
    farm_non_member_org = uuid7()
    db_session.add(
        FarmRow(
            id=farm_non_member_org,
            org_id=cast_uuid(org_other),
            name="Non Member Farm",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician_me,
        )
    )

    # Farm in org 1 with NO technician -> must be excluded!
    farm_unassigned = uuid7()
    db_session.add(
        FarmRow(
            id=farm_unassigned,
            org_id=cast_uuid(org_1),
            name="Unassigned Farm",
            municipality_code="47001",
            location=_POINT,
            technician_id=None,
        )
    )

    await db_session.commit()

    repo = SqlAlchemyFarmRepository(db_session)
    result = await list_farms_for_technician(
        technician_id=technician_me,
        org_ids=[cast_uuid(org_1), cast_uuid(org_2)],
        farms=repo,
    )

    # Only farm_a and farm_z must be returned, ordered by name (Aguacate before Zanahoria)
    result_ids = [f.id for f in result]
    assert result_ids == [farm_a, farm_z]
    assert [f.name for f in result] == ["Aguacate Farm", "Zanahoria Farm"]

    # Negative assertions
    assert farm_other_tech not in result_ids
    assert farm_non_member_org not in result_ids
    assert farm_unassigned not in result_ids


async def test_list_farms_for_technician_empty_org_ids(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyFarmRepository(db_session)
    result = await list_farms_for_technician(
        technician_id=uuid7(),
        org_ids=[],
        farms=repo,
    )
    assert result == []


def cast_uuid(val: object) -> UUID:
    if isinstance(val, UUID):
        return val
    return UUID(str(val))
