"""The survey repository against the real database (docs/09-cuellos-de-botella.md
#seguridad; R3-RELIABILITY-002).

The `org_id` predicate is the security-relevant part of this repository's only
read, and the API cannot exercise it: a cross-organization request is rejected
earlier by `resolve_plot_access`, so nothing reaches `get_for_org` with a
foreign org id. This module seeds a stored survey and asks for it under another
organization's id, which is the only way to prove the filter is in the query
rather than only in the docstring.
"""

from __future__ import annotations

import datetime
from itertools import count
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.metrics.adapters.orm import PlotBaselineRow
from techcamp.metrics.adapters.repositories import SqlAlchemyBaselineRepository
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_CROP_ID = 1
"""`maize`, seeded by `67cf2dd1f13e_add_crop_catalog`; no test writes the catalog."""
_ENROLLED_ON = datetime.date(2026, 2, 10)
"""Frozen: a test clock must not drift (E11 lessons, #12)."""

# A counter, not `uuid7().int % 10**10`: the modulo could collide on the unique
# `phone` column between two `uuid7()`s generated close together (#21).
_phone_seq = count()


async def _org_with_plot(db_session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """One organization with a farm and a plot; returns `(org_id, plot_id, user_id)`."""
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730055{next(_phone_seq):05d}"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()
    return org_id, plot_id, user_id


async def _seeded_survey(db_session: AsyncSession) -> tuple[UUID, UUID, UUID]:
    """A plot with a stored survey; returns `(org_id, plot_id, recorded_by)`."""
    org_id, plot_id, user_id = await _org_with_plot(db_session)
    db_session.add(
        PlotBaselineRow(
            plot_id=plot_id,
            org_id=org_id,
            enrolled_on=_ENROLLED_ON,
            crop_id=_CROP_ID,
            last_yield_kg_ha=3200.5,
            last_cost_cop_ha=1500000.25,
            irrigation_practice="gravity",
            recorded_by=user_id,
        )
    )
    await db_session.commit()
    return org_id, plot_id, user_id


async def test_a_survey_is_read_inside_its_own_organization(db_session: AsyncSession) -> None:
    org_id, plot_id, recorded_by = await _seeded_survey(db_session)
    repository = SqlAlchemyBaselineRepository(db_session)

    found = await repository.get_for_org(plot_id, org_id)

    assert found is not None
    assert found.plot_id == plot_id
    assert found.org_id == org_id
    assert found.enrolled_on == _ENROLLED_ON
    assert found.crop_id == _CROP_ID
    assert found.last_yield_kg_ha == 3200.5
    assert found.last_cost_cop_ha == 1500000.25
    assert found.irrigation_practice.value == "gravity"
    assert found.recorded_by == recorded_by


async def test_a_survey_is_absent_for_another_organization(db_session: AsyncSession) -> None:
    org_id, plot_id, _recorded_by = await _seeded_survey(db_session)
    other_org_id, _other_plot_id, _other_user_id = await _org_with_plot(db_session)
    assert other_org_id != org_id
    repository = SqlAlchemyBaselineRepository(db_session)

    found = await repository.get_for_org(plot_id, other_org_id)

    assert found is None


async def test_a_plot_without_a_survey_is_absent(db_session: AsyncSession) -> None:
    org_id, plot_id, _user_id = await _org_with_plot(db_session)
    repository = SqlAlchemyBaselineRepository(db_session)

    assert await repository.get_for_org(plot_id, org_id) is None
