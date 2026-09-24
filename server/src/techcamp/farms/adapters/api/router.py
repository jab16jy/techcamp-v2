"""Farm and plot endpoints (docs/04-api.md:43-49; ADR-0023).

`Idempotency-Key` (docs/04-api.md conventions): deliberately deferred here —
storing and replaying keyed responses is not a small addition, and no task
in this epic depends on it yet (noted in odd/tasks/techcamp-v2-e3-farms.md).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator

from techcamp.farms.adapters.api.deps import FarmRepoDep, PlotRepoDep
from techcamp.farms.adapters.geojson import (
    Position,
    point_to_wkt,
    polygon_to_wkt,
    wkt_to_point,
    wkt_to_polygon,
)
from techcamp.farms.application.manage_farms import create_farm, resolve_farm_access, update_farm
from techcamp.farms.application.manage_plots import create_plot, update_plot
from techcamp.farms.domain.errors import (
    FarmNotFoundError,
    InsufficientRoleError,
    PlotNotFoundError,
    RainfedPlotHasIrrigationError,
)
from techcamp.farms.domain.models import Farm, IrrigationSystem, Plot
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["farms"])


class GeoJSONPoint(BaseModel):
    type: str = Field(pattern="^Point$")
    coordinates: Position


class GeoJSONPolygon(BaseModel):
    type: str = Field(pattern="^Polygon$")
    coordinates: list[list[Position]]

    @field_validator("coordinates")
    @classmethod
    def _rings_are_closed(cls, rings: list[list[Position]]) -> list[list[Position]]:
        if not rings:
            raise ValueError("a polygon needs at least one ring")
        for ring in rings:
            if len(ring) < 4:
                raise ValueError("a polygon ring needs at least 4 positions")
            if ring[0] != ring[-1]:
                raise ValueError("a polygon ring must be closed (first position = last)")
        return rings


class FarmCreateRequest(BaseModel):
    org_id: UUID
    name: str
    municipality_code: str
    location: GeoJSONPoint
    technician_id: UUID | None = None


class FarmPatchRequest(BaseModel):
    name: str | None = None
    technician_id: UUID | None = None


class FarmView(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    municipality_code: str
    location: GeoJSONPoint
    technician_id: UUID | None


class FarmPage(BaseModel):
    items: list[FarmView]
    next_cursor: str | None


class PlotCreateRequest(BaseModel):
    name: str
    boundary: GeoJSONPolygon
    irrigation_system: IrrigationSystem
    irrigation_efficiency: float | None = Field(default=None, gt=0, le=1)
    system_flow_lph: float | None = Field(default=None, gt=0)


class PlotPatchRequest(BaseModel):
    name: str | None = None
    boundary: GeoJSONPolygon | None = None
    irrigation_system: IrrigationSystem | None = None
    irrigation_efficiency: float | None = Field(default=None, gt=0, le=1)
    system_flow_lph: float | None = Field(default=None, gt=0)


class PlotView(BaseModel):
    id: UUID
    org_id: UUID
    farm_id: UUID
    name: str
    boundary: GeoJSONPolygon
    area_ha: float
    weather_cell_id: int | None
    irrigation_system: IrrigationSystem
    irrigation_efficiency: float | None
    system_flow_lph: float | None


def _farm_view(farm: Farm) -> FarmView:
    x, y = wkt_to_point(farm.location)
    return FarmView(
        id=farm.id,
        org_id=farm.org_id,
        name=farm.name,
        municipality_code=farm.municipality_code,
        location=GeoJSONPoint(type="Point", coordinates=(x, y)),
        technician_id=farm.technician_id,
    )


def _plot_view(plot: Plot) -> PlotView:
    return PlotView(
        id=plot.id,
        org_id=plot.org_id,
        farm_id=plot.farm_id,
        name=plot.name,
        boundary=GeoJSONPolygon(type="Polygon", coordinates=wkt_to_polygon(plot.boundary)),
        area_ha=plot.area_ha,
        weather_cell_id=plot.weather_cell_id,
        irrigation_system=plot.irrigation_system,
        irrigation_efficiency=plot.irrigation_efficiency,
        system_flow_lph=plot.system_flow_lph,
    )


@router.get("/farms", response_model=FarmPage)
async def list_farms(
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    memberships: MembershipRepoDep,
    org_id: Annotated[UUID, Query()],
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> FarmPage:
    try:
        await resolve_org_membership(user_id=user_id, org_id=org_id, memberships=memberships)
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    page = await farms.list_for_org(org_id, limit=limit, cursor=cursor)
    next_cursor = str(page[-1].id) if len(page) == limit else None
    return FarmPage(items=[_farm_view(f) for f in page], next_cursor=next_cursor)


@router.post("/farms", response_model=FarmView, status_code=201)
async def post_farm(
    payload: FarmCreateRequest,
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    memberships: MembershipRepoDep,
) -> FarmView:
    try:
        farm = await create_farm(
            user_id=user_id,
            org_id=payload.org_id,
            name=payload.name,
            municipality_code=payload.municipality_code,
            location_wkt=point_to_wkt(payload.location.coordinates),
            technician_id=payload.technician_id,
            farms=farms,
            memberships=memberships,
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot create farms") from exc
    return _farm_view(farm)


@router.patch("/farms/{farm_id}", response_model=FarmView)
async def patch_farm(
    farm_id: UUID,
    payload: FarmPatchRequest,
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    memberships: MembershipRepoDep,
) -> FarmView:
    changes = payload.model_dump(exclude_unset=True)
    try:
        farm = await update_farm(
            user_id=user_id, farm_id=farm_id, changes=changes, farms=farms, memberships=memberships
        )
    except FarmNotFoundError as exc:
        raise ProblemError(status=404, title="Farm not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot update this farm") from exc
    return _farm_view(farm)


@router.get("/farms/{farm_id}/plots", response_model=list[PlotView])
async def list_plots(
    farm_id: UUID,
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
) -> list[PlotView]:
    try:
        farm, _role = await resolve_farm_access(
            user_id=user_id, farm_id=farm_id, farms=farms, memberships=memberships
        )
    except FarmNotFoundError as exc:
        raise ProblemError(status=404, title="Farm not found") from exc
    plot_list = await plots.list_for_farm(farm.id, farm.org_id)
    return [_plot_view(p) for p in plot_list]


@router.post("/farms/{farm_id}/plots", response_model=PlotView, status_code=201)
async def post_plot(
    farm_id: UUID,
    payload: PlotCreateRequest,
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
) -> PlotView:
    try:
        plot = await create_plot(
            user_id=user_id,
            farm_id=farm_id,
            name=payload.name,
            boundary_wkt=polygon_to_wkt(payload.boundary.coordinates),
            irrigation_system=payload.irrigation_system,
            irrigation_efficiency=payload.irrigation_efficiency,
            system_flow_lph=payload.system_flow_lph,
            farms=farms,
            plots=plots,
            memberships=memberships,
        )
    except FarmNotFoundError as exc:
        raise ProblemError(status=404, title="Farm not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot create plots") from exc
    except RainfedPlotHasIrrigationError as exc:
        raise ProblemError(status=422, title="Rainfed plot cannot have efficiency or flow") from exc
    return _plot_view(plot)


@router.patch("/plots/{plot_id}", response_model=PlotView)
async def patch_plot(
    plot_id: UUID,
    payload: PlotPatchRequest,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
) -> PlotView:
    raw = payload.model_dump(exclude_unset=True)
    changes: dict[str, object] = {
        key: (polygon_to_wkt(value["coordinates"]) if key == "boundary" else value)
        for key, value in raw.items()
    }
    try:
        plot = await update_plot(
            user_id=user_id, plot_id=plot_id, changes=changes, plots=plots, memberships=memberships
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot update this plot") from exc
    except RainfedPlotHasIrrigationError as exc:
        raise ProblemError(status=422, title="Rainfed plot cannot have efficiency or flow") from exc
    return _plot_view(plot)
