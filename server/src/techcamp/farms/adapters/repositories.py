"""Postgres repositories for farms (docs/09-cuellos-de-botella.md#seguridad).

Every query filters by `org_id`, so a farm or plot from another organization
is simply not found (docs/04-api.md: cross-org access responds 404, never
403). No application-layer port exists yet: T1 has no use case that depends
on repository behavior through an abstraction (ponytail: a port only for
external I/O or two real implementations, docs/adr per `farms` T1 task).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import (
    CropCycleRow,
    CropRow,
    CropStageRow,
    FarmRow,
    PlotRow,
    SoilProfileRow,
)
from techcamp.farms.domain.errors import ActiveCropCycleExistsError
from techcamp.farms.domain.models import (
    CROP_STAGES,
    Crop,
    CropCycle,
    CropCycleStatus,
    CropStage,
    Farm,
    IrrigationSystem,
    KcSource,
    Plot,
    SoilProfile,
    SoilProfileSource,
)
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository
from techcamp.weather.domain.models import cell_for


def _farm_from_row(row: Row[Any]) -> Farm:
    return Farm(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        municipality_code=row.municipality_code,
        location=row.location,
        technician_id=row.technician_id,
    )


def _plot_from_row(row: Row[Any]) -> Plot:
    return Plot(
        id=row.id,
        org_id=row.org_id,
        farm_id=row.farm_id,
        name=row.name,
        boundary=row.boundary,
        area_ha=float(row.area_ha),
        weather_cell_id=row.weather_cell_id,
        irrigation_system=IrrigationSystem(row.irrigation_system),
        irrigation_efficiency=(
            float(row.irrigation_efficiency) if row.irrigation_efficiency is not None else None
        ),
        system_flow_lph=(float(row.system_flow_lph) if row.system_flow_lph is not None else None),
    )


_FARM_COLUMNS = (
    FarmRow.id,
    FarmRow.org_id,
    FarmRow.name,
    FarmRow.municipality_code,
    func.ST_AsText(FarmRow.location).label("location"),
    FarmRow.technician_id,
)

_PLOT_COLUMNS = (
    PlotRow.id,
    PlotRow.org_id,
    PlotRow.farm_id,
    PlotRow.name,
    func.ST_AsText(PlotRow.boundary).label("boundary"),
    PlotRow.area_ha,
    PlotRow.weather_cell_id,
    PlotRow.irrigation_system,
    PlotRow.irrigation_efficiency,
    PlotRow.system_flow_lph,
)


class SqlAlchemyFarmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, farm_id: UUID, org_id: UUID) -> Farm | None:
        result = await self._session.execute(
            select(*_FARM_COLUMNS).where(FarmRow.id == farm_id, FarmRow.org_id == org_id)
        )
        row = result.one_or_none()
        return _farm_from_row(row) if row is not None else None

    async def get_for_orgs(self, farm_id: UUID, org_ids: Sequence[UUID]) -> Farm | None:
        """Look up a farm across every org the caller belongs to (docs/04-api.md:
        a farm-id-only route has no org_id in the path, but the query still
        filters by org_id, docs/09-cuellos-de-botella.md#seguridad)."""
        if not org_ids:
            return None
        result = await self._session.execute(
            select(*_FARM_COLUMNS).where(FarmRow.id == farm_id, FarmRow.org_id.in_(org_ids))
        )
        row = result.one_or_none()
        return _farm_from_row(row) if row is not None else None

    async def list_for_org(
        self, org_id: UUID, *, limit: int = 50, cursor: UUID | None = None
    ) -> list[Farm]:
        """Cursor page ordered by `id` (uuid7 is time-ordered, docs/04-api.md
        pagination convention)."""
        stmt = select(*_FARM_COLUMNS).where(FarmRow.org_id == org_id)
        if cursor is not None:
            stmt = stmt.where(FarmRow.id > cursor)
        stmt = stmt.order_by(FarmRow.id).limit(limit)
        result = await self._session.execute(stmt)
        return [_farm_from_row(row) for row in result]

    async def create(
        self,
        *,
        farm_id: UUID,
        org_id: UUID,
        name: str,
        municipality_code: str,
        location_wkt: str,
        technician_id: UUID | None,
    ) -> Farm:
        self._session.add(
            FarmRow(
                id=farm_id,
                org_id=org_id,
                name=name,
                municipality_code=municipality_code,
                location=location_wkt,
                technician_id=technician_id,
            )
        )
        await self._session.commit()
        farm = await self.get(farm_id, org_id)
        assert farm is not None
        return farm

    async def update(
        self, farm_id: UUID, org_id: UUID, *, name: str, technician_id: UUID | None
    ) -> Farm:
        await self._session.execute(
            update(FarmRow)
            .where(FarmRow.id == farm_id, FarmRow.org_id == org_id)
            .values(name=name, technician_id=technician_id)
        )
        await self._session.commit()
        farm = await self.get(farm_id, org_id)
        assert farm is not None
        return farm


class SqlAlchemyPlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Same session, so the cell row and the plot row are written in one
        # place. E5 owns the `weather_cell` table; farms only reads it through
        # this repository (the ORM column stays a plain Integer, docs/03:100).
        self._cells = SqlAlchemyWeatherRepository(session)

    async def get(self, plot_id: UUID, org_id: UUID) -> Plot | None:
        result = await self._session.execute(
            select(*_PLOT_COLUMNS).where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
        )
        row = result.one_or_none()
        return _plot_from_row(row) if row is not None else None

    async def get_for_orgs(self, plot_id: UUID, org_ids: Sequence[UUID]) -> Plot | None:
        """Look up a plot across every org the caller belongs to (see
        `SqlAlchemyFarmRepository.get_for_orgs`)."""
        if not org_ids:
            return None
        result = await self._session.execute(
            select(*_PLOT_COLUMNS).where(PlotRow.id == plot_id, PlotRow.org_id.in_(org_ids))
        )
        row = result.one_or_none()
        return _plot_from_row(row) if row is not None else None

    async def list_for_farm(self, farm_id: UUID, org_id: UUID) -> list[Plot]:
        result = await self._session.execute(
            select(*_PLOT_COLUMNS)
            .where(PlotRow.farm_id == farm_id, PlotRow.org_id == org_id)
            .order_by(PlotRow.id)
        )
        return [_plot_from_row(row) for row in result]

    async def create(
        self,
        *,
        plot_id: UUID,
        org_id: UUID,
        farm_id: UUID,
        name: str,
        boundary_wkt: str,
        irrigation_system: IrrigationSystem,
        irrigation_efficiency: float | None,
        system_flow_lph: float | None,
    ) -> Plot:
        self._session.add(
            PlotRow(
                id=plot_id,
                org_id=org_id,
                farm_id=farm_id,
                name=name,
                boundary=boundary_wkt,
                irrigation_system=irrigation_system.value,
                irrigation_efficiency=irrigation_efficiency,
                system_flow_lph=system_flow_lph,
            )
        )
        await self._session.commit()
        await self._assign_cell(plot_id, org_id)
        plot = await self.get(plot_id, org_id)
        assert plot is not None
        return plot

    async def update(
        self,
        plot_id: UUID,
        org_id: UUID,
        *,
        name: str,
        boundary_wkt: str,
        irrigation_system: IrrigationSystem,
        irrigation_efficiency: float | None,
        system_flow_lph: float | None,
    ) -> Plot:
        await self._session.execute(
            update(PlotRow)
            .where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
            .values(
                name=name,
                boundary=boundary_wkt,
                irrigation_system=irrigation_system.value,
                irrigation_efficiency=irrigation_efficiency,
                system_flow_lph=system_flow_lph,
            )
        )
        await self._session.commit()
        # Assigned on every update, not only when the boundary moved: resolving
        # the same coordinates again is the same row, so an untouched location
        # costs one idempotent upsert instead of a `boundary changed` flag
        # threaded through the port.
        await self._assign_cell(plot_id, org_id)
        plot = await self.get(plot_id, org_id)
        assert plot is not None
        return plot

    async def _assign_cell(self, plot_id: UUID, org_id: UUID) -> None:
        """Point the plot at the 0.1° cell its location falls in
        (docs/06-diseno-detallado.md §6): plots are grouped so the cell, not
        the plot, is what the provider is queried for
        (docs/09-cuellos-de-botella.md:39).

        The representative point is the polygon centroid, the same point
        `get_centroid` already treats as the plot's location (the SoilGrids
        query point), so the cell E5 assigns is the cell the rest of the code
        reads. The grid rule itself stays in one place, `cell_for` (weather
        domain, T1b).
        """
        lon, lat = await self.get_centroid(plot_id, org_id)
        cell_id = await self._cells.get_or_create_cell(*cell_for(lat, lon))
        await self._session.execute(
            update(PlotRow)
            .where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
            .values(weather_cell_id=cell_id)
        )
        await self._session.commit()

    async def get_centroid(self, plot_id: UUID, org_id: UUID) -> tuple[float, float]:
        """(lon, lat) of `ST_Centroid(boundary)` (T5: SoilGrids query point).
        Callers resolve org-scoped access before calling this, so a missing
        row here would be a caller bug, not a normal 404 path."""
        result = await self._session.execute(
            select(
                func.ST_X(func.ST_Centroid(PlotRow.boundary)),
                func.ST_Y(func.ST_Centroid(PlotRow.boundary)),
            ).where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
        )
        row = result.one()
        return float(row[0]), float(row[1])


_STAGE_ORDER = {stage: index for index, stage in enumerate(CROP_STAGES)}


def _crop_stage_from_row(row: CropStageRow) -> CropStage:
    return CropStage(
        stage=row.stage,
        length_days=row.length_days,
        kc=float(row.kc),
        depletion_fraction_p=float(row.depletion_fraction_p),
    )


def _crop_from_rows(row: CropRow, stage_rows: list[CropStageRow]) -> Crop:
    stages = sorted(
        (_crop_stage_from_row(s) for s in stage_rows), key=lambda s: _STAGE_ORDER[s.stage]
    )
    return Crop(
        id=row.id,
        code=row.code,
        name_es=row.name_es,
        kc_source=KcSource(row.kc_source),
        stages=tuple(stages),
    )


class SqlAlchemyCropRepository:
    """Global crop catalog (docs/03-modelo-datos.md:115-127): no `org_id`,
    the same rows for every organization."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[Crop]:
        crop_rows = (
            (await self._session.execute(select(CropRow).order_by(CropRow.id))).scalars().all()
        )
        stage_rows = (await self._session.execute(select(CropStageRow))).scalars().all()
        stages_by_crop: dict[int, list[CropStageRow]] = defaultdict(list)
        for stage_row in stage_rows:
            stages_by_crop[stage_row.crop_id].append(stage_row)
        return [_crop_from_rows(row, stages_by_crop[row.id]) for row in crop_rows]

    async def get(self, crop_id: int) -> Crop | None:
        """One crop with its stages (T6: `create_cycle` validates `crop_id`
        and derives `expected_harvest_on` from it)."""
        crop_row = await self._session.get(CropRow, crop_id)
        if crop_row is None:
            return None
        stmt = select(CropStageRow).where(CropStageRow.crop_id == crop_id)
        stage_rows = (await self._session.execute(stmt)).scalars().all()
        return _crop_from_rows(crop_row, list(stage_rows))


def _crop_cycle_from_row(row: CropCycleRow) -> CropCycle:
    return CropCycle(
        id=row.id,
        plot_id=row.plot_id,
        crop_id=row.crop_id,
        sown_on=row.sown_on,
        expected_harvest_on=row.expected_harvest_on,
        status=CropCycleStatus(row.status),
    )


class SqlAlchemyCropCycleRepository:
    """docs/03-modelo-datos.md:128-135. No `org_id` column: access is gated
    through the plot, same reasoning as `SqlAlchemySoilProfileRepository`
    (T4)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get(self, cycle_id: UUID) -> CropCycle | None:
        result = await self._session.execute(
            select(CropCycleRow).where(CropCycleRow.id == cycle_id)
        )
        row = result.scalar_one_or_none()
        return _crop_cycle_from_row(row) if row is not None else None

    async def create(
        self,
        *,
        cycle_id: UUID,
        plot_id: UUID,
        crop_id: int,
        sown_on: date,
        expected_harvest_on: date | None,
        status: CropCycleStatus,
    ) -> CropCycle:
        self._session.add(
            CropCycleRow(
                id=cycle_id,
                plot_id=plot_id,
                crop_id=crop_id,
                sown_on=sown_on,
                expected_harvest_on=expected_harvest_on,
                status=status.value,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            # GitHub issue #21 round 8: `create_cycle`'s precheck
            # (`get_active_for_plot`) still leaves a race window between two
            # concurrent creates on the same plot; the losing insert hits
            # `uq_crop_cycle_active_per_plot` here and must surface as the
            # same domain conflict the precheck raises, never a raw 500.
            # asyncpg's driver-level exception carries no `constraint_name`
            # attribute by the time SQLAlchemy's asyncpg dialect re-wraps it
            # (only `sqlstate`/`pgcode` survive) — `23505` is the generic
            # `unique_violation` SQLSTATE, so the constraint name is matched
            # in the message text to scope the translation to this one
            # constraint. Any other integrity error (e.g. a bad
            # `crop_id`/`plot_id` FK) still propagates unchanged.
            orig = exc.orig
            is_active_cycle_conflict = getattr(
                orig, "sqlstate", None
            ) == "23505" and "uq_crop_cycle_active_per_plot" in str(orig)
            if is_active_cycle_conflict:
                raise ActiveCropCycleExistsError(plot_id) from exc
            raise
        cycle = await self._get(cycle_id)
        assert cycle is not None
        return cycle

    async def get_active_for_plot(self, plot_id: UUID) -> CropCycle | None:
        result = await self._session.execute(
            select(CropCycleRow).where(
                CropCycleRow.plot_id == plot_id,
                CropCycleRow.status == CropCycleStatus.ACTIVE.value,
            )
        )
        row = result.scalar_one_or_none()
        return _crop_cycle_from_row(row) if row is not None else None

    async def get_for_orgs(self, cycle_id: UUID, org_ids: Sequence[UUID]) -> CropCycle | None:
        if not org_ids:
            return None
        result = await self._session.execute(
            select(CropCycleRow)
            .join(PlotRow, PlotRow.id == CropCycleRow.plot_id)
            .where(CropCycleRow.id == cycle_id, PlotRow.org_id.in_(org_ids))
        )
        row = result.scalar_one_or_none()
        return _crop_cycle_from_row(row) if row is not None else None

    async def update(
        self, cycle_id: UUID, *, status: CropCycleStatus, expected_harvest_on: date | None
    ) -> CropCycle:
        await self._session.execute(
            update(CropCycleRow)
            .where(CropCycleRow.id == cycle_id)
            .values(status=status.value, expected_harvest_on=expected_harvest_on)
        )
        await self._session.commit()
        cycle = await self._get(cycle_id)
        assert cycle is not None
        return cycle


def _soil_profile_from_row(row: SoilProfileRow) -> SoilProfile:
    return SoilProfile(
        plot_id=row.plot_id,
        source=SoilProfileSource(row.source) if row.source is not None else None,
        ph=float(row.ph) if row.ph is not None else None,
        organic_matter_pct=(
            float(row.organic_matter_pct) if row.organic_matter_pct is not None else None
        ),
        texture=row.texture,
        field_capacity_pct=(
            float(row.field_capacity_pct) if row.field_capacity_pct is not None else None
        ),
        wilting_point_pct=(
            float(row.wilting_point_pct) if row.wilting_point_pct is not None else None
        ),
        root_depth_cm=float(row.root_depth_cm) if row.root_depth_cm is not None else None,
    )


class SqlAlchemySoilProfileRepository:
    """docs/03-modelo-datos.md:106-113. `put` is an upsert keyed by `plot_id`:
    `PUT /plots/{plot_id}/soil` is a full-document write (docs/04-api.md:49),
    not a partial `PATCH`, so there is no separate create/update split."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, profile: SoilProfile) -> SoilProfile:
        values = {
            "source": profile.source.value if profile.source is not None else None,
            "ph": profile.ph,
            "organic_matter_pct": profile.organic_matter_pct,
            "texture": profile.texture,
            "field_capacity_pct": profile.field_capacity_pct,
            "wilting_point_pct": profile.wilting_point_pct,
            "root_depth_cm": profile.root_depth_cm,
        }
        stmt = insert(SoilProfileRow).values(plot_id=profile.plot_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[SoilProfileRow.plot_id], set_=values)
        await self._session.execute(stmt)
        await self._session.commit()
        result = await self._session.execute(
            select(SoilProfileRow).where(SoilProfileRow.plot_id == profile.plot_id)
        )
        row = result.scalar_one()
        return _soil_profile_from_row(row)
