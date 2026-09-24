from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
)
from techcamp.farms.domain.models import CropCycleStatus, IrrigationSystem
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_BOUNDARY_AREA_HA = pytest.approx(120.9259121752739, rel=1e-9)


async def _make_org(db_session: AsyncSession, name: str = "Finca") -> object:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    await db_session.commit()
    return org_id


async def test_farm_repository_round_trips_by_id_and_org(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca A",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()

    farm = await SqlAlchemyFarmRepository(db_session).get(farm_id, org_id)

    assert farm is not None
    assert farm.id == farm_id
    assert farm.org_id == org_id
    assert farm.municipality_code == "47001"
    assert farm.technician_id is None


async def test_farm_repository_hides_farms_of_other_orgs(db_session: AsyncSession) -> None:
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_a, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()

    assert await SqlAlchemyFarmRepository(db_session).get(farm_id, org_b) is None
    assert await SqlAlchemyFarmRepository(db_session).get(farm_id, org_a) is not None


async def test_plot_repository_computes_area_ha_from_boundary(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
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
            irrigation_system="none",
        )
    )
    await db_session.commit()

    plot = await SqlAlchemyPlotRepository(db_session).get(plot_id, org_id)

    assert plot is not None
    assert plot.area_ha == _BOUNDARY_AREA_HA
    assert plot.irrigation_system is IrrigationSystem.NONE
    assert plot.irrigation_efficiency is None
    assert plot.system_flow_lph is None
    assert plot.weather_cell_id is None


async def test_plot_repository_hides_plots_of_other_orgs(db_session: AsyncSession) -> None:
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_a, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_a,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    await db_session.commit()

    assert await SqlAlchemyPlotRepository(db_session).get(plot_id, org_b) is None
    assert await SqlAlchemyPlotRepository(db_session).get(plot_id, org_a) is not None


async def test_irrigated_plot_keeps_its_efficiency_and_flow(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
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
            irrigation_efficiency=0.9,
            system_flow_lph=250,
        )
    )
    await db_session.commit()

    plot = await SqlAlchemyPlotRepository(db_session).get(plot_id, org_id)

    assert plot is not None
    assert plot.irrigation_system is IrrigationSystem.DRIP
    assert plot.irrigation_efficiency == pytest.approx(0.9)
    assert plot.system_flow_lph == pytest.approx(250)


async def test_rainfed_plot_with_efficiency_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
            irrigation_efficiency=0.9,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_unknown_irrigation_system_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="not-a-system",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_out_of_range_efficiency_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
            irrigation_efficiency=1.5,
            system_flow_lph=100,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_non_positive_flow_is_rejected_by_the_database(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
            irrigation_efficiency=0.9,
            system_flow_lph=0,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_plot_of_a_different_orgs_farm_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    """T1 review follow-up: the composite FK ties plot.org_id to its farm's org_id."""
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_a, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org_b,
            farm_id=farm_id,
            name="Lote ajeno",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_boundary_round_trips_as_wkt(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
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
            irrigation_system="none",
        )
    )
    await db_session.commit()

    plot = await SqlAlchemyPlotRepository(db_session).get(plot_id, org_id)

    assert plot is not None
    assert plot.boundary.startswith("POLYGON((")
    from techcamp.farms.adapters.geojson import wkt_to_polygon

    rings = wkt_to_polygon(plot.boundary)
    assert rings[0][0] == pytest.approx((-74.10, 10.90))
    assert rings[0][0] == rings[0][-1]  # closed ring


async def test_farm_repository_create_and_update(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    technician_id = uuid7()
    db_session.add(AppUserRow(id=technician_id, phone="+573001234567"))
    await db_session.commit()
    repo = SqlAlchemyFarmRepository(db_session)

    created = await repo.create(
        farm_id=farm_id,
        org_id=org_id,
        name="Finca A",
        municipality_code="47001",
        location_wkt=_POINT,
        technician_id=None,
    )
    assert created.id == farm_id
    assert created.technician_id is None

    updated = await repo.update(
        farm_id, org_id, name="Finca A renamed", technician_id=technician_id
    )
    assert updated.name == "Finca A renamed"
    assert updated.technician_id == technician_id


async def test_plot_repository_create_and_update(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    plot_id = uuid7()
    repo = SqlAlchemyPlotRepository(db_session)

    created = await repo.create(
        plot_id=plot_id,
        org_id=org_id,
        farm_id=farm_id,
        name="Lote 1",
        boundary_wkt=_BOUNDARY,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )
    assert created.id == plot_id
    assert created.area_ha == _BOUNDARY_AREA_HA

    updated = await repo.update(
        plot_id,
        org_id,
        name="Lote 1 renamed",
        boundary_wkt=_BOUNDARY,
        irrigation_system=IrrigationSystem.DRIP,
        irrigation_efficiency=0.9,
        system_flow_lph=300,
    )
    assert updated.name == "Lote 1 renamed"
    assert updated.irrigation_system is IrrigationSystem.DRIP
    assert updated.irrigation_efficiency == pytest.approx(0.9)


async def test_list_for_org_is_empty_for_an_org_with_no_farms(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    assert await SqlAlchemyFarmRepository(db_session).list_for_org(org_id) == []


async def test_list_for_org_filters_by_org_and_orders_deterministically(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    first_id, second_id = uuid7(), uuid7()
    db_session.add_all(
        [
            FarmRow(
                id=first_id, org_id=org_a, name="1", municipality_code="47001", location=_POINT
            ),
            FarmRow(
                id=second_id, org_id=org_a, name="2", municipality_code="47001", location=_POINT
            ),
            FarmRow(id=uuid7(), org_id=org_b, name="3", municipality_code="47001", location=_POINT),
        ]
    )
    await db_session.commit()

    farms = await SqlAlchemyFarmRepository(db_session).list_for_org(org_a)

    assert [f.id for f in farms] == sorted([first_id, second_id])


async def test_list_for_org_paginates_by_cursor(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    ids = sorted(uuid7() for _ in range(3))
    db_session.add_all(
        [
            FarmRow(id=farm_id, org_id=org_id, name="f", municipality_code="47001", location=_POINT)
            for farm_id in ids
        ]
    )
    await db_session.commit()

    first_page = await SqlAlchemyFarmRepository(db_session).list_for_org(org_id, limit=2)
    second_page = await SqlAlchemyFarmRepository(db_session).list_for_org(
        org_id, limit=2, cursor=first_page[-1].id
    )

    assert [f.id for f in first_page] == ids[:2]
    assert [f.id for f in second_page] == ids[2:]


async def test_list_for_farm_is_empty_for_a_farm_with_no_plots(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()

    assert await SqlAlchemyPlotRepository(db_session).list_for_farm(farm_id, org_id) == []


async def test_list_for_farm_orders_deterministically(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    first_id, second_id = uuid7(), uuid7()
    db_session.add_all(
        [
            PlotRow(
                id=pid,
                org_id=org_id,
                farm_id=farm_id,
                name="p",
                boundary=_BOUNDARY,
                irrigation_system="none",
            )
            for pid in (second_id, first_id)
        ]
    )
    await db_session.commit()

    plots = await SqlAlchemyPlotRepository(db_session).list_for_farm(farm_id, org_id)

    assert [p.id for p in plots] == sorted([first_id, second_id])


async def test_get_for_orgs_hides_farms_outside_the_given_orgs(db_session: AsyncSession) -> None:
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_a, name="Finca A", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    repo = SqlAlchemyFarmRepository(db_session)

    assert await repo.get_for_orgs(farm_id, [org_b]) is None
    assert await repo.get_for_orgs(farm_id, []) is None
    assert await repo.get_for_orgs(farm_id, [org_a, org_b]) is not None


_MAIZE_ID = 1
"""Seeded by the `67cf2dd1f13e` migration (T3): FAO-56, stage lengths sum to 90 days."""


async def _make_org_and_plot(
    db_session: AsyncSession, org_name: str = "Finca"
) -> tuple[object, object]:
    org_id = await _make_org(db_session, org_name)
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name=org_name, municipality_code="47001", location=_POINT
        )
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
            irrigation_system="none",
        )
    )
    await db_session.commit()
    return org_id, plot_id


async def test_crop_cycle_repository_round_trips_by_id(db_session: AsyncSession) -> None:
    _org_id, plot_id = await _make_org_and_plot(db_session)
    repo = SqlAlchemyCropCycleRepository(db_session)
    cycle_id = uuid7()

    created = await repo.create(
        cycle_id=cycle_id,
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )

    assert created.id == cycle_id
    assert created.plot_id == plot_id
    assert created.crop_id == _MAIZE_ID
    assert created.sown_on == date(2026, 1, 1)
    assert created.expected_harvest_on == date(2026, 4, 1)
    assert created.status is CropCycleStatus.ACTIVE


async def test_get_active_for_plot_finds_only_the_active_cycle(db_session: AsyncSession) -> None:
    _org_id, plot_id = await _make_org_and_plot(db_session)
    repo = SqlAlchemyCropCycleRepository(db_session)
    other_plot_id = (await _make_org_and_plot(db_session, "Otra finca"))[1]

    assert await repo.get_active_for_plot(plot_id) is None

    active = await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )
    await repo.create(
        cycle_id=uuid7(),
        plot_id=other_plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )

    found = await repo.get_active_for_plot(plot_id)

    assert found is not None
    assert found.id == active.id


async def test_a_second_active_cycle_on_the_same_plot_is_rejected_by_the_database(
    db_session: AsyncSession,
) -> None:
    """The partial unique index `uq_crop_cycle_active_per_plot` is the DB-side
    backstop for the one-active-cycle-per-plot rule."""
    _org_id, plot_id = await _make_org_and_plot(db_session)
    repo = SqlAlchemyCropCycleRepository(db_session)
    await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )

    with pytest.raises(IntegrityError):
        await repo.create(
            cycle_id=uuid7(),
            plot_id=plot_id,
            crop_id=_MAIZE_ID,
            sown_on=date(2026, 2, 1),
            expected_harvest_on=date(2026, 5, 1),
            status=CropCycleStatus.ACTIVE,
        )


async def test_a_second_harvested_cycle_on_the_same_plot_is_allowed(
    db_session: AsyncSession,
) -> None:
    """The partial unique index only covers `status = 'active'`: a plot may
    have any number of harvested/lost cycles in its history."""
    _org_id, plot_id = await _make_org_and_plot(db_session)
    repo = SqlAlchemyCropCycleRepository(db_session)
    await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2025, 1, 1),
        expected_harvest_on=date(2025, 4, 1),
        status=CropCycleStatus.HARVESTED,
    )

    second = await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.HARVESTED,
    )

    assert second.status is CropCycleStatus.HARVESTED


async def test_get_for_orgs_hides_cycles_outside_the_given_orgs(db_session: AsyncSession) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    _org_b, _plot_b = await _make_org_and_plot(db_session, "Finca B")
    repo = SqlAlchemyCropCycleRepository(db_session)
    cycle = await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_a,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )

    assert await repo.get_for_orgs(cycle.id, [org_a]) is not None
    assert await repo.get_for_orgs(cycle.id, []) is None
    found_in_other_org = await repo.get_for_orgs(cycle.id, [uuid7()])
    assert found_in_other_org is None


async def test_update_replaces_status_and_expected_harvest_on(db_session: AsyncSession) -> None:
    _org_id, plot_id = await _make_org_and_plot(db_session)
    repo = SqlAlchemyCropCycleRepository(db_session)
    cycle = await repo.create(
        cycle_id=uuid7(),
        plot_id=plot_id,
        crop_id=_MAIZE_ID,
        sown_on=date(2026, 1, 1),
        expected_harvest_on=date(2026, 4, 1),
        status=CropCycleStatus.ACTIVE,
    )

    updated = await repo.update(
        cycle.id, status=CropCycleStatus.HARVESTED, expected_harvest_on=date(2026, 3, 28)
    )

    assert updated.status is CropCycleStatus.HARVESTED
    assert updated.expected_harvest_on == date(2026, 3, 28)
