import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
)
from techcamp.farms.domain.models import IrrigationSystem
from techcamp.identity.adapters.orm import OrganizationRow
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
