"""`GET /plots/{plot_id}/readings` (docs/04-api.md:92-97).

Reuses farms' `resolve_plot_access` for the org check (task instruction):
the same "a plot in another organization is 404" rule farms already
enforces, not a telemetry-specific reimplementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.telemetry.application.ports import NodeRepository, ReadingRepository, SensorRepository
from techcamp.telemetry.domain.models import ReadingPoint, ReadingResolution, validate_reading_range

# ponytail: one `list_for_org` page covers every node on a plot at this
# project's scale (docs/02-estimaciones.md; docs/04-api.md documents the same
# 500-node cap); revisit with a dedicated unbounded repository method if a
# plot ever nears this many nodes.
_MAX_NODES_PER_PLOT = 500


@dataclass(frozen=True, slots=True)
class ReadingSeries:
    sensor_id: int
    depth_cm: int | None
    points: list[ReadingPoint]


async def query_plot_readings(
    *,
    user_id: UUID,
    plot_id: UUID,
    metric: str,
    start: datetime,
    end: datetime,
    resolution: ReadingResolution,
    plots: PlotRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
    memberships: MembershipRepository,
) -> list[ReadingSeries]:
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    validate_reading_range(resolution, start, end)
    node_list = await nodes.list_for_org(plot.org_id, plot_id=plot.id, limit=_MAX_NODES_PER_PLOT)
    series: list[ReadingSeries] = []
    for node in node_list:
        for sensor in await sensors.list_for_node(node.id, plot.org_id):
            if sensor.metric != metric:
                continue
            if resolution is ReadingResolution.RAW:
                points = await readings.query_raw(sensor.id, start=start, end=end)
            elif resolution is ReadingResolution.HOUR:
                points = await readings.query_hourly(sensor.id, start=start, end=end)
            else:
                points = await readings.query_daily(sensor.id, start=start, end=end)
            series.append(
                ReadingSeries(sensor_id=sensor.id, depth_cm=sensor.depth_cm, points=points)
            )
    return series
