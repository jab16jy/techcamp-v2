"""The balance branch of `water_stress`, decided by the `alerts` job that runs
after the daily balance (docs/06 §3 "Balance hídrico", §5; ADR-0022, Q2; D25,
D27–D29).

A plot with a representative sensor is NOT decided here: docs/06 §5 gives
`water_stress` one evidence source per plot, the reading rule on that sensor, and
this source only the plots without one. Deciding both would open the same alert
from two sources with two clocks on `opened_at`, and the 48 h upgrade would
depend on which one ran last (D29).

The evidence itself is E6's: `irrigation.application.water_stress` reads the daily
balances and answers the `K > 0` sensor question, so this module never imports
`irrigation.domain` (docs/05's `alerts --> irrigation`, D25). The decision itself
is the same `decide_alert` the reading rules use, over the daily balances as their
series: the 60-minute resolution run and the 48 h upgrade are measured on days
there, which is what D27 states and what the tests pin.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.alerts.application.use_cases import (
    open_alert,
    resolve_automatically,
    upgrade_to_critical,
)
from techcamp.alerts.domain import (
    BALANCE_STRESS_MAX_GAP,
    AlertAction,
    balance_rule_for_stress,
    decide_alert,
)
from techcamp.farms.application.ports import FarmRepository, PlotRepository, SoilProfileRepository
from techcamp.irrigation.application.ports import WaterBalanceRepository
from techcamp.irrigation.application.water_stress import (
    balance_stress_evidence,
    representative_soil_moisture_sensors,
)
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)

if TYPE_CHECKING:
    from techcamp.alerts.domain import AlertRule
    from techcamp.farms.domain.models import Plot

logger = logging.getLogger(__name__)

_MAX_FARMS_PER_ORG = 500
"""ponytail: one `list_for_org` page covers every farm of an org at this
project's scale (the same constant as `alerts.application.evaluate_weather_rules`)."""

_REPRESENTATIVE_LOOKBACK = timedelta(hours=24)
"""docs/06 §5: the representative sensor needs "lectura válida en 24 h". The same
window and the same rule as the reading branch (D26); the two run at different
times, so a plot can move between them and the branch that owns it moves with it."""


async def evaluate_balance_rules(
    *,
    org_id: UUID,
    at: datetime,
    day: date,
    rules: AlertRuleRepository,
    farms: FarmRepository,
    plots: PlotRepository,
    soils: SoilProfileRepository,
    balances: WaterBalanceRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    """Decide the daily-balance branch of `water_stress` for one organization's
    plots (D28, Q2).

    `day` is the day the 04:50 run decided, so the newest balance it reads is the
    one for D−1 (docs/06 §5: the run for day D writes the row for D−1). All of the
    org's plots are decided at the same `at`, this job's own decision time.

    `org_id` is a parameter, not a filter a caller can leave out, and every
    repository read below keeps it (D21, docs/09 org isolation).
    """
    rule = next(
        (
            candidate
            for candidate in await rules.list_for_org(org_id)
            if candidate.code == "water_stress"
        ),
        None,
    )
    if rule is None:
        return
    for farm in await farms.list_for_org(org_id, limit=_MAX_FARMS_PER_ORG):
        for plot in await plots.list_for_farm(farm.id, org_id):
            await _evaluate_plot(
                plot,
                rule=rule,
                at=at,
                day=day,
                soils=soils,
                balances=balances,
                nodes=nodes,
                sensors=sensors,
                calibrations=calibrations,
                readings=readings,
                alerts=alerts,
            )


async def _evaluate_plot(
    plot: Plot,
    *,
    rule: AlertRule,
    at: datetime,
    day: date,
    soils: SoilProfileRepository,
    balances: WaterBalanceRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    soil = await soils.get_for_plot(plot.id)
    representative = await representative_soil_moisture_sensors(
        org_id=plot.org_id,
        plot_id=plot.id,
        root_depth_cm=None if soil is None else soil.root_depth_cm,
        start=at - _REPRESENTATIVE_LOOKBACK,
        end=at,
        nodes=nodes,
        sensors=sensors,
        calibrations=calibrations,
        readings=readings,
    )
    if representative:
        # D29: the reading rule owns this plot, and it decides over the sensor.
        return
    evidence = await balance_stress_evidence(
        balances, org_id=plot.org_id, plot_id=plot.id, to_day=day
    )
    if evidence is None or not evidence.margin_samples:
        # No balance row for this plot (the 04:30 job skips one with incomplete
        # soil data, docs/06 §5), or no day with a positive `RAW` to compare the
        # depletion against (E6's D5).
        return
    current = await alerts.get_non_resolved_for_target(
        rule_id=rule.id, org_id=plot.org_id, plot_id=plot.id, node_id=None
    )
    decision = decide_alert(
        balance_rule_for_stress(rule),
        evidence.margin_samples,
        at,
        max_gap=BALANCE_STRESS_MAX_GAP,
        threshold=0.0,
        current_alert=current,
    )
    match decision.action:
        case AlertAction.OPEN:
            await open_alert(
                rule=rule,
                at=at,
                alerts=alerts,
                plot_id=plot.id,
                evidence={"day": day.isoformat(), "at": at.isoformat(), "source": "balance"},
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
        case AlertAction.UPGRADE:
            assert decision.alert is not None, "an upgrade decision carries the alert"
            await upgrade_to_critical(
                alert_id=decision.alert.id, org_id=plot.org_id, at=at, alerts=alerts
            )
        case AlertAction.NO_ACTION:
            pass
