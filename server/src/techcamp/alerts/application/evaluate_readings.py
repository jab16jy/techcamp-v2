"""Reading-threshold rules decided right after each ingest batch (docs/06 §3,
"Umbral sobre lectura"; D1, D2, D9).

The rule set is data: a rule with a metric and a `(`<`, `>`)` operator is a
reading threshold, so `heat_stress`, `waterlogging` and an org's own rules are
decided by the same code, and the seeded worker rules (no metric) are skipped
without a case per rule. `water_stress` is inert here by construction: its
threshold is `None` until T10 supplies the plot's `stress_moisture_pct`, so
`decide_alert` answers `NO_ACTION`.

The use case supplies inputs and calls the right lifecycle use case (D9); the
domain owns every decision. The whole window is read back from `reading`
statelessly (D1), because the batch that lands the last sample is not
necessarily the batch that crosses `min_duration` (a backfill, a restart, or a
lost window that D16 makes self-healing on the next batch).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.alerts.application.use_cases import (
    open_alert,
    resolve_automatically,
    upgrade_to_critical,
)
from techcamp.alerts.domain import RESOLUTION_WINDOW, AlertAction, AlertRule, decide_alert
from techcamp.farms.application.ports import PlotRepository, SoilProfileRepository
from techcamp.telemetry.application.ports import NodeRepository, ReadingRepository, SensorRepository
from techcamp.telemetry.domain.models import ReadingEvent

if TYPE_CHECKING:
    from techcamp.farms.domain.models import Plot
    from techcamp.telemetry.domain.models import Node

_MAX_NODES_PER_PLOT = 500
"""ponytail: one `list_for_org` page covers every node on a plot at this
project's scale (the same reasoning and constant as
`telemetry.application.query_readings`)."""

_ONE_SECOND = timedelta(seconds=1)
"""`ReadingRepository.query_raw` is `[start, end)`, so the window has to end just
after the batch's own newest reading: that sample is the evidence the decision
is about."""


def _reading_threshold_rules(rules: Sequence[AlertRule]) -> list[tuple[AlertRule, str]]:
    """The `(rule, metric)` pairs this evaluator decides.

    docs/06 §3 "Umbral sobre lecturas" as data: a metric plus a `(`<`, `>`)`
    operator is a reading threshold, everything else (node health, forecast,
    model) is decided by its own source.
    """
    return [(r, m) for r in rules if (m := r.metric) is not None and r.operator in ("<", ">")]


async def evaluate_landed_readings(
    *,
    events: Sequence[ReadingEvent],
    rules: AlertRuleRepository,
    readings: ReadingRepository,
    sensors: SensorRepository,
    nodes: NodeRepository,
    plots: PlotRepository,
    soils: SoilProfileRepository,
    alerts: AlertRepository,
) -> None:
    """Decide every reading-threshold rule of the plots the batch just landed.

    `events` are the readings that actually landed (D16: the hook runs after the
    batch commit and is not called when nothing landed), so `at` is the batch's
    latest reading time and only those plots are re-evaluated.
    """
    at = max(event.at for event in events)
    org_by_plot: dict[UUID, UUID] = {}
    for event in events:
        org_by_plot.setdefault(event.plot_id, event.org_id)

    for plot_id, org_id in org_by_plot.items():
        plot = await plots.get_for_orgs(plot_id, [org_id])
        if plot is None:
            continue
        await _evaluate_plot(
            plot,
            at=at,
            rules=rules,
            readings=readings,
            sensors=sensors,
            nodes=nodes,
            soils=soils,
            alerts=alerts,
        )


async def _evaluate_plot(
    plot: Plot,
    *,
    at: datetime,
    rules: AlertRuleRepository,
    readings: ReadingRepository,
    sensors: SensorRepository,
    nodes: NodeRepository,
    soils: SoilProfileRepository,
    alerts: AlertRepository,
) -> None:
    threshold_rules = _reading_threshold_rules(await rules.list_for_org(plot.org_id))
    if not threshold_rules:
        return
    plot_nodes = await nodes.list_for_org(plot.org_id, plot_id=plot.id, limit=_MAX_NODES_PER_PLOT)
    if not plot_nodes:
        return
    # docs/06 §3: `max_gap` is 3 × `interval_s`, the same margin that defines
    # `node_offline`. The plot's series is one sequence, so the widest interval
    # of its nodes is the margin that keeps the samples consecutive evidence.
    max_gap = timedelta(seconds=3 * max(node.interval_s for node in plot_nodes))
    soil = await soils.get_for_plot(plot.id)

    for rule, metric in threshold_rules:
        # D2: an open alert also resolves on a 60 min clear run, so the window
        # covers the longer of the two runs.
        window = max(rule.min_duration, RESOLUTION_WINDOW)
        samples = await _samples(
            metric,
            plot=plot,
            plot_nodes=plot_nodes,
            sensors=sensors,
            readings=readings,
            start=at - window,
            end=at + _ONE_SECOND,
        )
        if not samples:
            continue
        current = await alerts.get_non_resolved_for_target(
            rule_id=rule.id, org_id=plot.org_id, plot_id=plot.id, node_id=None
        )
        decision = decide_alert(
            rule,
            samples,
            at,
            max_gap=max_gap,
            current_alert=current,
            field_capacity_pct=soil.field_capacity_pct if soil is not None else None,
        )
        match decision.action:
            case AlertAction.OPEN:
                await open_alert(
                    rule=rule,
                    at=at,
                    alerts=alerts,
                    plot_id=plot.id,
                    evidence={"samples": len(samples), "at": at.isoformat()},
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


async def _samples(
    metric: str,
    *,
    plot: Plot,
    plot_nodes: Sequence[Node],
    sensors: SensorRepository,
    readings: ReadingRepository,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float]]:
    """The `(time, value)` samples of `metric` over the window, one sequence for
    the plot, ordered by time (`sustained_run` walks it backwards).

    `query_valid_raw` is the read that excludes the out-of-range readings
    (docs/06 §1: they "no disparan alertas"), flag by flag and without touching
    what the readings API returns.
    """
    samples: list[tuple[datetime, float]] = []
    for node in plot_nodes:
        for sensor in await sensors.list_for_node(node.id, plot.org_id):
            if sensor.metric != metric:
                continue
            samples.extend(
                (point.time, point.value)
                for point in await readings.query_valid_raw(sensor.id, start=start, end=end)
            )
    return sorted(samples)
