"""Forecast and daily rules decided in the worker (docs/06 §3, "Pronóstico";
docs/10 §3 `k --> l`; D10, D19, D20, D22, D23).

`heavy_rain_forecast` reads the forecast day's `rain_mm` and `fungal_risk` the
previous cell-day's humidity, both from the same `weather_daily` row, so the use
case supplies the evidence and the domain decides — there is no branch per rule
here, exactly as the reading-threshold evaluator has none (D17).

The two rules run on their OWN alerts periodics (D10: docs/05 has no
`weather -> alerts` edge, so the weather job may not call this), and each fans
out per organization (D21) so no read crosses one (docs/09 org isolation). A
weather cell is shared by design and carries no `org_id`, so the cell-days are
read once per run and cached by cell id (D23), while the plots they are decided
for are always the caller's own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.alerts.application.use_cases import open_alert, resolve_automatically
from techcamp.alerts.domain import (
    AlertAction,
    AlertRule,
    CellDay,
    decide_worker_rule,
    worker_rule_evidence,
    worker_rule_opening_severity,
)
from techcamp.farms.application.ports import (
    FarmRepository,
    PlotRepository,
    SoilProfileRepository,
)
from techcamp.telemetry.application.ports import NodeRepository, ReadingRepository, SensorRepository
from techcamp.weather.application.ports import WeatherRepository

if TYPE_CHECKING:
    from techcamp.farms.domain.models import Plot
    from techcamp.weather.domain.models import WeatherDay

_MAX_FARMS_PER_ORG = 500
"""ponytail: one `list_for_org` page covers every farm of one organization at
this project's scale (the same reasoning and constant as the two evaluators
above it)."""

_MAX_NODES_PER_PLOT = 500
"""The same bound, for the nodes a plot's soil moisture is read from."""

_AGGREGATE_MAX_GAP = timedelta(days=1)
"""The freshness margin a day aggregate is decided with.

A `weather_daily` row carries no time of day, only the `fetched_at` of the
provider call that produced it, so the staleness rule of docs/06 §3 cannot use
the 3 × `interval_s` of a sensor series: the newest row of the 3 h refresh is
ten minutes old and the consolidated cell-day of yesterday is under two. A day
is wide enough that a real aggregate is never dropped and narrow enough that a
cell nobody refreshes stops being evidence.
"""

_SATURATION_LOOKBACK = timedelta(days=1)
"""How old the plot's newest soil-moisture reading may be to call the soil
saturated. A plot whose node has been silent for longer is not observed, and an
unobserved plot opens the alert as a warning, not as a critical one."""

_ONE_SECOND = timedelta(seconds=1)
"""`ReadingRepository.query_valid_raw` is `[start, end)`, so the window ends just
after the decision time: the newest reading is the evidence."""


async def evaluate_weather_rules(
    *,
    org_id: UUID,
    at: datetime,
    day: date,
    rules: AlertRuleRepository,
    farms: FarmRepository,
    plots: PlotRepository,
    soils: SoilProfileRepository,
    weather: WeatherRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    """Decide the weather rules of one organization's plots for `day` (D10).

    `day` is the weather day this run decides and the caller picks it: the
    forecast rule is decided on the forecast day the warning is about, the daily
    one on the cell-day the weather job just consolidated. Every plot is decided
    at its OWN cell's evidence — its `fetched_at` — and never at a time another
    cell's row happens to carry, which is the mistake that made T5's first pass
    drop a plot's evidence as stale.

    `org_id` is a parameter, not a filter a caller can leave out, and every
    repository read below keeps it (D21, docs/09 org isolation).
    """
    org_rules = await rules.list_for_org(org_id)
    cells: dict[int, tuple[CellDay | None, CellDay | None]] = {}
    for farm in await farms.list_for_org(org_id, limit=_MAX_FARMS_PER_ORG):
        for plot in await plots.list_for_farm(farm.id, org_id):
            await _evaluate_plot(
                plot,
                at=at,
                day=day,
                org_rules=org_rules,
                cells=cells,
                weather=weather,
                soils=soils,
                nodes=nodes,
                sensors=sensors,
                readings=readings,
                alerts=alerts,
            )


async def _evaluate_plot(
    plot: Plot,
    *,
    at: datetime,
    day: date,
    org_rules: list[AlertRule],
    cells: dict[int, tuple[CellDay | None, CellDay | None]],
    weather: WeatherRepository,
    soils: SoilProfileRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    if plot.weather_cell_id is None:
        # No cell means no forecast and no cell-day: there is nothing to decide.
        return
    observed, forecast = await _cell_day(cells, weather, plot.weather_cell_id, day)

    for rule in org_rules:
        evidence = worker_rule_evidence(rule, observed=observed, forecast=forecast)
        if evidence is None:
            continue
        # `get_non_resolved_for_target` never returns a resolved alert (the
        # partial unique index's own scope), so it is the "current" alert.
        current = await alerts.get_non_resolved_for_target(
            rule_id=rule.id, org_id=plot.org_id, plot_id=plot.id, node_id=None
        )
        decision = decide_worker_rule(
            rule,
            [(evidence.observed_at, evidence.value)],
            at,
            max_gap=_AGGREGATE_MAX_GAP,
            current_alert=current,
            mildness=evidence.mildness,
        )
        match decision.action:
            case AlertAction.OPEN:
                severity = worker_rule_opening_severity(
                    rule,
                    saturated=await _is_saturated(
                        plot, at=at, soils=soils, nodes=nodes, sensors=sensors, readings=readings
                    ),
                )
                await open_alert(
                    rule=rule,
                    at=at,
                    alerts=alerts,
                    plot_id=plot.id,
                    evidence={"day": day.isoformat(), "at": at.isoformat()},
                    severity=severity,
                )
            case AlertAction.RESOLVE:
                assert decision.alert is not None, "a resolve decision carries the alert"
                await resolve_automatically(
                    alert_id=decision.alert.id,
                    org_id=plot.org_id,
                    farm_id=plot.farm_id,
                    at=at,
                    alerts=alerts,
                )
            case AlertAction.UPGRADE | AlertAction.NO_ACTION:
                # Neither rule of this source has an upgrade (docs/06 §3 lists
                # none, and D20 decides the severity when the alert opens), and
                # an aggregate that still violates the condition changes nothing.
                pass


async def _cell_day(
    cells: dict[int, tuple[CellDay | None, CellDay | None]],
    weather: WeatherRepository,
    cell_id: int,
    day: date,
) -> tuple[CellDay | None, CellDay | None]:
    """The `(observed, forecast)` rows of one cell-day, read once per run (D23).

    Neighbouring plots share a cell by design (docs/00 glosario), so the two rows
    are cached by cell id and every plot of the cell is decided on the same read.
    A cell with no row for the day yields no evidence at all, which is the honest
    answer: the provider has not said anything about that day.
    """
    if cell_id not in cells:
        rows = await weather.list_daily(cell_id, day, day)
        cells[cell_id] = (
            _as_cell_day(next((row for row in rows if not row.is_forecast), None)),
            _as_cell_day(next((row for row in rows if row.is_forecast), None)),
        )
    return cells[cell_id]


def _as_cell_day(row: WeatherDay | None) -> CellDay | None:
    if row is None:
        return None
    return CellDay(
        fetched_at=row.fetched_at,
        rain_mm=row.rain_mm,
        rh_mean_pct=row.rh_mean_pct,
        tmin_c=row.tmin_c,
        tmax_c=row.tmax_c,
    )


async def _is_saturated(
    plot: Plot,
    *,
    at: datetime,
    soils: SoilProfileRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
) -> bool:
    """Whether the plot's soil is at field capacity right now (D20).

    θFC is the plot's own (`farms.SoilProfileRepository`, the same θFC
    `waterlogging` is decided on) and the moisture is the plot's newest VALID
    `soil_moisture` reading, an out-of-range value excluded as everywhere else
    (docs/06 §1).

    It is read at that latest sample, not at the representative depth E6 selects
    for the water balance: `irrigation.is_sensor_depth_representative` is a pure
    function, but the sensor set it judges is assembled by `run_daily_balance`
    (nodes, sensors, a valid `field` calibration and a daily mean per sensor), so
    reusing it here would mean either importing another module's domain
    (`docs/05` grants `alerts` the `irrigation` application package, not its
    domain) or duplicating that agronomic rule. The depth-aware version belongs
    with `water_stress`, which is the rule E6's representative sensor is decided
    on (ADR-0022, T10).
    """
    soil = await soils.get_for_plot(plot.id)
    if soil is None or soil.field_capacity_pct is None:
        return False
    latest = await _latest_soil_moisture(
        plot, at=at, nodes=nodes, sensors=sensors, readings=readings
    )
    return latest is not None and latest >= float(soil.field_capacity_pct)


async def _latest_soil_moisture(
    plot: Plot,
    *,
    at: datetime,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
) -> float | None:
    """The newest valid `soil_moisture` value the plot reported in the last day."""
    newest: tuple[datetime, float] | None = None
    for node in await nodes.list_for_org(plot.org_id, plot_id=plot.id, limit=_MAX_NODES_PER_PLOT):
        for sensor in await sensors.list_for_node(node.id, plot.org_id):
            if sensor.metric != "soil_moisture":
                continue
            for point in await readings.query_valid_raw(
                sensor.id, plot.org_id, start=at - _SATURATION_LOOKBACK, end=at + _ONE_SECOND
            ):
                if newest is None or point.time > newest[0]:
                    newest = (point.time, point.value)
    return None if newest is None else newest[1]
