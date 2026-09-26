"""Plot → weather cell assignment (docs/06-diseno-detallado.md §6; docs/00-glosario.md:40).

Plots are grouped into 0.1° cells so nearby plots cost one Open-Meteo call
between them (docs/09-cuellos-de-botella.md:39). The write path assigns the
cell when a plot is created and when its boundary moves; the data migration
backfills the plots that already existed.
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.farms.domain.models import IrrigationSystem
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.db import engine
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_FARM_POINT = "SRID=4326;POINT(-74.1 10.9)"
# Centroid (-74.09, 10.91): the 0.1° cell (-74.1, 10.9).
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.92, -74.08 10.92, -74.08 10.90, -74.10 10.90))"
)
# Centroid (-74.11, 10.93): a different polygon, the same cell.
_NEARBY_BOUNDARY = (
    "SRID=4326;POLYGON((-74.12 10.92, -74.12 10.94, -74.10 10.94, -74.10 10.92, -74.12 10.92))"
)
# Centroid (-74.19, 10.91): the cell (-74.2, 10.9).
_OTHER_CELL_BOUNDARY = (
    "SRID=4326;POLYGON((-74.20 10.90, -74.20 10.92, -74.18 10.92, -74.18 10.90, -74.20 10.90))"
)
_CELL_LAT = Decimal("10.9")
_CELL_LON = Decimal("-74.1")
_OTHER_CELL_LON = Decimal("-74.2")


async def _make_org(db_session: AsyncSession, name: str = "Finca") -> UUID:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    await db_session.commit()
    return org_id


async def _make_farm(db_session: AsyncSession, org_id: UUID) -> UUID:
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca A",
            municipality_code="47001",
            location=_FARM_POINT,
        )
    )
    await db_session.commit()
    return farm_id


async def _create_plot(
    repository: SqlAlchemyPlotRepository, org_id: UUID, farm_id: UUID, boundary: str
) -> UUID:
    plot = await repository.create(
        plot_id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        name="Lote 1",
        boundary_wkt=boundary,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )
    return plot.id


async def _cell_coordinates(db_session: AsyncSession, cell_id: int) -> tuple[Decimal, Decimal]:
    lat, lon = (
        await db_session.execute(
            text("SELECT lat, lon FROM weather_cell WHERE id = :id"), {"id": cell_id}
        )
    ).one()
    return lat, lon


async def test_creating_a_plot_assigns_the_cell_of_its_centroid(
    db_session: AsyncSession,
) -> None:
    org_id = await _make_org(db_session)
    farm_id = await _make_farm(db_session, org_id)
    repository = SqlAlchemyPlotRepository(db_session)

    plot_id = await _create_plot(repository, org_id, farm_id, _BOUNDARY)

    plot = await repository.get(plot_id, org_id)
    assert plot is not None
    assert plot.weather_cell_id is not None
    assert await _cell_coordinates(db_session, plot.weather_cell_id) == (_CELL_LAT, _CELL_LON)


async def test_two_nearby_plots_share_one_cell(db_session: AsyncSession) -> None:
    """The cell is the Open-Meteo cache (docs/09-cuellos-de-botella.md:39): two
    plots in the same 0.1° square must end up on one row, or the same square
    costs two provider calls."""
    org_id = await _make_org(db_session)
    farm_id = await _make_farm(db_session, org_id)
    repository = SqlAlchemyPlotRepository(db_session)

    first = await _create_plot(repository, org_id, farm_id, _BOUNDARY)
    second = await _create_plot(repository, org_id, farm_id, _NEARBY_BOUNDARY)

    first_plot = await repository.get(first, org_id)
    second_plot = await repository.get(second, org_id)
    assert first_plot is not None and second_plot is not None
    assert first_plot.weather_cell_id == second_plot.weather_cell_id
    cells = await db_session.execute(text("SELECT count(*) FROM weather_cell"))
    assert cells.scalar_one() == 1


async def test_plots_of_two_orgs_share_the_cell_without_sharing_plots(
    db_session: AsyncSession,
) -> None:
    """A cell is shared reference data (docs/09-cuellos-de-botella.md:39), while
    a plot stays invisible to any other organization (docs/04-api.md)."""
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_a = await _make_farm(db_session, org_a)
    farm_b = await _make_farm(db_session, org_b)
    repository = SqlAlchemyPlotRepository(db_session)

    plot_a = await _create_plot(repository, org_a, farm_a, _BOUNDARY)
    plot_b = await _create_plot(repository, org_b, farm_b, _NEARBY_BOUNDARY)

    from_org_a = await repository.get(plot_a, org_a)
    from_org_b = await repository.get(plot_b, org_b)
    assert from_org_a is not None and from_org_b is not None
    assert from_org_a.weather_cell_id is not None
    assert from_org_a.weather_cell_id == from_org_b.weather_cell_id
    assert await repository.get(plot_a, org_b) is None
    assert await repository.get(plot_b, org_a) is None


async def test_moving_a_plot_moves_it_to_the_new_cell(db_session: AsyncSession) -> None:
    org_id = await _make_org(db_session)
    farm_id = await _make_farm(db_session, org_id)
    repository = SqlAlchemyPlotRepository(db_session)
    plot_id = await _create_plot(repository, org_id, farm_id, _BOUNDARY)
    before = await repository.get(plot_id, org_id)
    assert before is not None and before.weather_cell_id is not None

    moved = await repository.update(
        plot_id,
        org_id,
        name="Lote 1",
        boundary_wkt=_OTHER_CELL_BOUNDARY,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )

    assert moved.weather_cell_id != before.weather_cell_id
    assert await _cell_coordinates(db_session, moved.weather_cell_id) == (
        _CELL_LAT,
        _OTHER_CELL_LON,
    )


async def test_renaming_a_plot_keeps_its_cell(db_session: AsyncSession) -> None:
    """A change that leaves the location alone must not move the plot to
    another cell (or to no cell)."""
    org_id = await _make_org(db_session)
    farm_id = await _make_farm(db_session, org_id)
    repository = SqlAlchemyPlotRepository(db_session)
    plot_id = await _create_plot(repository, org_id, farm_id, _BOUNDARY)
    before = await repository.get(plot_id, org_id)
    assert before is not None and before.weather_cell_id is not None

    renamed = await repository.update(
        plot_id,
        org_id,
        name="Lote 1 renamed",
        boundary_wkt=_BOUNDARY,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )

    assert renamed.weather_cell_id == before.weather_cell_id
    cells = await db_session.execute(text("SELECT count(*) FROM weather_cell"))
    assert cells.scalar_one() == 1


async def test_a_rejected_plot_is_never_stored_without_its_cell(
    db_session: AsyncSession,
) -> None:
    """The plot row and the cell it points at are written by one statement, so a
    plot the database rejects leaves nothing behind: no stored plot whose
    `weather_cell_id` is null, waiting for a repair nobody runs."""
    org_id = await _make_org(db_session)
    farm_id = await _make_farm(db_session, org_id)
    repository = SqlAlchemyPlotRepository(db_session)
    plot_id = uuid7()

    with pytest.raises(IntegrityError):
        await repository.create(
            plot_id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary_wkt=_BOUNDARY,
            # A rainfed plot may not carry efficiency (ADR-0023): the insert is
            # rejected by `ck_plot_rainfed_has_no_irrigation`.
            irrigation_system=IrrigationSystem.NONE,
            irrigation_efficiency=0.9,
            system_flow_lph=None,
        )
    await db_session.rollback()

    assert await repository.get(plot_id, org_id) is None


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/b7e2c9a41d38_backfill_plot_weather_cells.py"
    )
    spec = importlib.util.spec_from_file_location("plot_weather_cell_backfill", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_backfill_assigns_cells_to_plots_that_predate_the_weather_module(
    db_session: AsyncSession,
) -> None:
    """`plot.weather_cell_id` has existed since E3 with no value, so the plots
    already stored need their cell from the same centroid rule (E5 migration
    `b7e2c9a41d38`)."""
    org_a = await _make_org(db_session, "Finca A")
    org_b = await _make_org(db_session, "Finca B")
    farm_a = await _make_farm(db_session, org_a)
    farm_b = await _make_farm(db_session, org_b)
    # Raw rows: the write path is exactly what the backfill replaces, and going
    # through it would assign the cell before the migration runs.
    legacy: list[UUID] = []
    for org_id, farm_id, boundary in (
        (org_a, farm_a, _BOUNDARY),
        (org_b, farm_b, _NEARBY_BOUNDARY),
    ):
        plot_id = uuid7()
        legacy.append(plot_id)
        db_session.add(
            PlotRow(
                id=plot_id,
                org_id=org_id,
                farm_id=farm_id,
                name="Lote 1",
                boundary=boundary,
                irrigation_system="none",
            )
        )
    await db_session.commit()

    statements = _load_migration().BACKFILL_STATEMENTS
    assert statements, "the migration must expose the statements it runs"
    # Twice: the backfill is replay-safe (no row duplicated, no plot re-pointed).
    for _ in range(2):
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))

    assigned = (
        await db_session.execute(
            text(
                "SELECT p.id, p.weather_cell_id FROM plot p WHERE p.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": [str(plot_id) for plot_id in legacy]},
        )
    ).all()
    cell_ids = {plot_id: cell_id for plot_id, cell_id in assigned}
    assert set(cell_ids.values()) == {cell_ids[legacy[0]]}
    assert None not in cell_ids.values()
    assert await _cell_coordinates(db_session, cell_ids[legacy[0]]) == (_CELL_LAT, _CELL_LON)
    cells = await db_session.execute(text("SELECT count(*) FROM weather_cell"))
    assert cells.scalar_one() == 1
