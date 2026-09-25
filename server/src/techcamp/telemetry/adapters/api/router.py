"""Node, sensor and calibration endpoints (docs/04-api.md:79-93;
docs/06-diseno-detallado.md §2).

`Idempotency-Key` (docs/04-api.md conventions): deliberately deferred here,
same decision and reason as farms (`farms/adapters/api/router.py`) — no task
in this epic depends on it yet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from techcamp.farms.adapters.api.deps import FarmRepoDep, PlotRepoDep
from techcamp.farms.application.manage_farms import resolve_farm_access
from techcamp.farms.domain.errors import FarmNotFoundError, PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.shared.errors import ProblemError
from techcamp.telemetry.adapters.api.deps import (
    CalibrationRepoDep,
    NodeRepoDep,
    PlotEventsHubDep,
    ReadingRepoDep,
    SensorRepoDep,
)
from techcamp.telemetry.adapters.api.stream import stream_plot_events
from techcamp.telemetry.application.add_calibration import add_calibration
from techcamp.telemetry.application.get_node_health import get_node_health
from techcamp.telemetry.application.manage_nodes import (
    claim_node,
    resolve_node_access,
    rotate_credentials,
    update_node,
)
from techcamp.telemetry.application.query_readings import ReadingSeries, query_plot_readings
from techcamp.telemetry.domain.errors import (
    ClaimCodeNotFoundError,
    InsufficientRoleError,
    InvalidCalibrationParamsError,
    InvalidPlotError,
    InvalidReadingRangeError,
    NodeAlreadyClaimedError,
    NodeNotFoundError,
    SensorNotFoundError,
)
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    Node,
    NodeStatus,
    NodeTransport,
    ReadingResolution,
    Sensor,
)

router = APIRouter(tags=["telemetry"])


class MqttCredentials(BaseModel):
    username: str
    password: str


class NodeView(BaseModel):
    id: UUID
    org_id: UUID | None
    plot_id: UUID | None
    transport: NodeTransport
    dev_eui: str | None
    firmware: str | None
    interval_s: int
    claimed_at: datetime | None
    last_seen_at: datetime | None
    status: NodeStatus


class NodeClaimResponse(NodeView):
    mqtt: MqttCredentials


class NodePage(BaseModel):
    items: list[NodeView]
    next_cursor: str | None


class NodeClaimRequest(BaseModel):
    claim_code: str
    plot_id: UUID


class NodePatchRequest(BaseModel):
    plot_id: UUID | None = None
    status: NodeStatus | None = None


class PasswordResponse(BaseModel):
    password: str


class NodeHealthView(BaseModel):
    last_seen_at: datetime | None
    battery_v: float | None
    rssi: int | None
    completeness_24h: float | None


class SensorView(BaseModel):
    id: int
    node_id: UUID
    channel_key: str
    metric: str
    depth_cm: int | None
    unit: str


class CalibrationView(BaseModel):
    id: UUID
    sensor_id: int
    version: int
    method: CalibrationMethod
    kind: CalibrationKind
    params: dict[str, Any]
    rmse_pct: float | None
    valid_from: datetime


class CalibrationCreateRequest(BaseModel):
    method: CalibrationMethod
    kind: CalibrationKind
    params: dict[str, Any]
    rmse_pct: float | None = None
    valid_from: datetime


class ReadingSeriesView(BaseModel):
    sensor_id: int
    depth_cm: int | None
    points: list[tuple[datetime, float]]
    """`[t, value]` pairs (docs/04-api.md:93): a tuple serializes as a JSON
    array, matching the documented shape."""


class ReadingsResponse(BaseModel):
    series: list[ReadingSeriesView]


def _node_view(node: Node) -> NodeView:
    return NodeView(
        id=node.id,
        org_id=node.org_id,
        plot_id=node.plot_id,
        transport=node.transport,
        dev_eui=node.dev_eui,
        firmware=node.firmware,
        interval_s=node.interval_s,
        claimed_at=node.claimed_at,
        last_seen_at=node.last_seen_at,
        status=node.status,
    )


def _sensor_view(sensor: Sensor) -> SensorView:
    return SensorView(
        id=sensor.id,
        node_id=sensor.node_id,
        channel_key=sensor.channel_key,
        metric=sensor.metric,
        depth_cm=sensor.depth_cm,
        unit=sensor.unit,
    )


def _reading_series_view(series: ReadingSeries) -> ReadingSeriesView:
    return ReadingSeriesView(
        sensor_id=series.sensor_id,
        depth_cm=series.depth_cm,
        points=[(p.time, p.value) for p in series.points],
    )


def _calibration_view(calibration: Calibration) -> CalibrationView:
    return CalibrationView(
        id=calibration.id,
        sensor_id=calibration.sensor_id,
        version=calibration.version,
        method=calibration.method,
        kind=calibration.kind,
        params=calibration.params,
        rmse_pct=calibration.rmse_pct,
        valid_from=calibration.valid_from,
    )


def _reject_explicit_null(raw: dict[str, Any]) -> None:
    """422, not the DB `ck_node_ownership_all_or_nothing` `IntegrityError`
    (same convention as farms' `_reject_explicit_null`, T2b): every `PATCH
    /nodes/{node_id}` field is non-nullable, so any explicit JSON `null` here
    is a client error."""
    nulled = sorted(key for key, value in raw.items() if value is None)
    if nulled:
        raise ProblemError(status=422, title=f"{', '.join(nulled)} cannot be null")


@router.post("/nodes:claim", response_model=NodeClaimResponse, status_code=201)
async def post_claim_node(
    payload: NodeClaimRequest,
    user_id: CurrentUserId,
    nodes: NodeRepoDep,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
) -> NodeClaimResponse:
    try:
        node, password = await claim_node(
            user_id=user_id,
            claim_code=payload.claim_code,
            plot_id=payload.plot_id,
            nodes=nodes,
            plots=plots,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot claim nodes") from exc
    except ClaimCodeNotFoundError as exc:
        raise ProblemError(status=404, title="Unknown claim code") from exc
    except NodeAlreadyClaimedError as exc:
        raise ProblemError(status=409, title="Node is already claimed") from exc
    return NodeClaimResponse(
        id=node.id,
        org_id=node.org_id,
        plot_id=node.plot_id,
        transport=node.transport,
        dev_eui=node.dev_eui,
        firmware=node.firmware,
        interval_s=node.interval_s,
        claimed_at=node.claimed_at,
        last_seen_at=node.last_seen_at,
        status=node.status,
        # MQTT username = node_id (docs/04-api.md MQTT section; ADR-0004's ACL
        # `tc/v1/%u/#` matches the topic's node_id segment against it).
        mqtt=MqttCredentials(username=str(node.id), password=password),
    )


@router.get("/nodes", response_model=NodePage)
async def list_nodes(
    user_id: CurrentUserId,
    nodes: NodeRepoDep,
    memberships: MembershipRepoDep,
    org_id: Annotated[UUID, Query()],
    plot_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[NodeStatus | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> NodePage:
    try:
        await resolve_org_membership(user_id=user_id, org_id=org_id, memberships=memberships)
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    node_list = await nodes.list_for_org(
        org_id, plot_id=plot_id, status=status, limit=limit, cursor=cursor
    )
    next_cursor = str(node_list[-1].id) if len(node_list) == limit else None
    return NodePage(items=[_node_view(n) for n in node_list], next_cursor=next_cursor)


@router.patch("/nodes/{node_id}", response_model=NodeView)
async def patch_node(
    node_id: UUID,
    payload: NodePatchRequest,
    user_id: CurrentUserId,
    nodes: NodeRepoDep,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
) -> NodeView:
    changes = payload.model_dump(exclude_unset=True)
    _reject_explicit_null(changes)
    try:
        node = await update_node(
            user_id=user_id,
            node_id=node_id,
            changes=changes,
            nodes=nodes,
            plots=plots,
            memberships=memberships,
        )
    except NodeNotFoundError as exc:
        raise ProblemError(status=404, title="Node not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot update this node") from exc
    except InvalidPlotError as exc:
        raise ProblemError(status=422, title="plot_id is not in this organization") from exc
    return _node_view(node)


@router.post("/nodes/{node_id}/credentials:rotate", response_model=PasswordResponse)
async def rotate_node_credentials(
    node_id: UUID, user_id: CurrentUserId, nodes: NodeRepoDep, memberships: MembershipRepoDep
) -> PasswordResponse:
    try:
        password = await rotate_credentials(
            user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
        )
    except NodeNotFoundError as exc:
        raise ProblemError(status=404, title="Node not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot rotate this node's credentials") from exc
    return PasswordResponse(password=password)


@router.get("/nodes/{node_id}/health", response_model=NodeHealthView)
async def get_health(
    node_id: UUID, user_id: CurrentUserId, nodes: NodeRepoDep, memberships: MembershipRepoDep
) -> NodeHealthView:
    try:
        health = await get_node_health(
            user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
        )
    except NodeNotFoundError as exc:
        raise ProblemError(status=404, title="Node not found") from exc
    return NodeHealthView(
        last_seen_at=health.last_seen_at,
        battery_v=health.battery_v,
        rssi=health.rssi,
        completeness_24h=health.completeness_24h,
    )


@router.get("/nodes/{node_id}/sensors", response_model=list[SensorView])
async def get_sensors(
    node_id: UUID,
    user_id: CurrentUserId,
    nodes: NodeRepoDep,
    sensors: SensorRepoDep,
    memberships: MembershipRepoDep,
) -> list[SensorView]:
    try:
        node, _role = await resolve_node_access(
            user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
        )
    except NodeNotFoundError as exc:
        raise ProblemError(status=404, title="Node not found") from exc
    assert node.org_id is not None  # resolve_node_access only returns claimed nodes
    sensor_list = await sensors.list_for_node(node.id, node.org_id)
    return [_sensor_view(s) for s in sensor_list]


@router.post("/sensors/{sensor_id}/calibrations", response_model=CalibrationView, status_code=201)
async def post_calibration(
    sensor_id: int,
    payload: CalibrationCreateRequest,
    user_id: CurrentUserId,
    sensors: SensorRepoDep,
    calibrations: CalibrationRepoDep,
    memberships: MembershipRepoDep,
) -> CalibrationView:
    try:
        calibration = await add_calibration(
            user_id=user_id,
            sensor_id=sensor_id,
            method=payload.method,
            kind=payload.kind,
            params=payload.params,
            rmse_pct=payload.rmse_pct,
            valid_from=payload.valid_from,
            sensors=sensors,
            calibrations=calibrations,
            memberships=memberships,
        )
    except SensorNotFoundError as exc:
        raise ProblemError(status=404, title="Sensor not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot calibrate this sensor") from exc
    except InvalidCalibrationParamsError as exc:
        raise ProblemError(status=422, title="Invalid calibration params", detail=str(exc)) from exc
    return _calibration_view(calibration)


@router.get("/plots/{plot_id}/readings", response_model=ReadingsResponse)
async def get_plot_readings(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    nodes: NodeRepoDep,
    sensors: SensorRepoDep,
    readings: ReadingRepoDep,
    memberships: MembershipRepoDep,
    metric: Annotated[str, Query()],
    from_: Annotated[datetime, Query(alias="from")],
    to: Annotated[datetime, Query()],
    resolution: Annotated[str, Query()],
) -> ReadingsResponse:
    """docs/04-api.md:92-97: `raw` (≤ 2 days), `hour` (≤ 60 days) or `day`
    (no documented upper limit)."""
    try:
        resolved_resolution = ReadingResolution(resolution)
    except ValueError as exc:
        raise ProblemError(status=422, title="resolution must be raw, hour or day") from exc
    try:
        series = await query_plot_readings(
            user_id=user_id,
            plot_id=plot_id,
            metric=metric,
            start=from_,
            end=to,
            resolution=resolved_resolution,
            plots=plots,
            nodes=nodes,
            sensors=sensors,
            readings=readings,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except InvalidReadingRangeError as exc:
        raise ProblemError(status=422, title="Invalid reading range", detail=str(exc)) from exc
    return ReadingsResponse(series=[_reading_series_view(s) for s in series])


@router.get("/stream")
async def stream_events(
    user_id: CurrentUserId,
    farms: FarmRepoDep,
    memberships: MembershipRepoDep,
    hub: PlotEventsHubDep,
    farm_id: Annotated[UUID, Query()],
) -> StreamingResponse:
    """docs/04-api.md:180-189, ADR-0015: one `LISTEN plot_events` fan-out per
    api process, filtered by `farm_id`.

    `Last-Event-ID` (E4 T6 gap, docs are silent on replay): the browser sends
    it automatically on reconnect; it is accepted and simply ignored here,
    no replay is built.
    """
    try:
        await resolve_farm_access(
            user_id=user_id, farm_id=farm_id, farms=farms, memberships=memberships
        )
    except FarmNotFoundError as exc:
        raise ProblemError(status=404, title="Farm not found") from exc
    client_id, queue = hub.subscribe(farm_id)
    return StreamingResponse(
        stream_plot_events(hub, client_id, queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
