"""Reading-threshold rules decided right after each ingest batch (docs/06 §3,
"Umbral sobre lectura"; D1, D2, D9).

The rule set is data: `plot_rule_metric` (D17) says which of a plot's rules
this source decides, so `heat_stress`, `waterlogging` and an org's own rules are
decided by the same code, and the rules of the other four sources of docs/06 §3
are skipped without a case per rule. `water_stress` is decided here too, on the
plot's own evidence: its threshold is the plot's θ_estrés out of
`water_balance_daily` and its series is the REPRESENTATIVE sensor's alone
(docs/06 §3 "Reglas de fábrica", §5; ADR-0022; D26, D29), both read through
`irrigation.application.water_stress` (docs/05's `alerts --> irrigation`, D25). A
plot without a representative sensor is not decided here at all: its soil-moisture
series is not evidence for this rule, and the daily balance owns it (D29).

The use case supplies inputs and calls the right lifecycle use case (D9); the
domain owns every decision. The whole window is read back from `reading`
statelessly (D1), because the batch that lands the last sample is not
necessarily the batch that crosses `min_duration` (a backfill, a restart, or a
lost window that D16 makes self-healing on the next batch).

Each plot of the batch is evaluated inside its own `try`: the ingestor awaits
this hook inside its flush, so one plot that raises would re-queue the whole
batch, fail the same way on every attempt and silence the alerts of every other
plot sharing it. The failed plot is decided again by its next batch, because the
evaluation is stateless (D1, D16).

The loop keeps going on one shared `AsyncSession`, so a failure that came from the
database leaves that session unusable: `recover` rolls it back before the next
plot runs, or the per-plot isolation would be cosmetic (D24).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.alerts.application.ports import (
    AlertRepository,
    AlertRuleRepository,
    UnitOfWorkRecovery,
)
from techcamp.alerts.application.use_cases import (
    open_alert,
    resolve_automatically,
    upgrade_to_critical,
)
from techcamp.alerts.domain import (
    RESOLUTION_WINDOW,
    AlertAction,
    AlertRule,
    decide_alert,
    plot_rule_metric,
)
from techcamp.farms.application.ports import PlotRepository, SoilProfileRepository
from techcamp.irrigation.application.ports import WaterBalanceRepository
from techcamp.irrigation.application.water_stress import (
    balance_stress_evidence,
    representative_soil_moisture_sensors,
)
from techcamp.shared.dates import local_today
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)
from techcamp.telemetry.domain.models import ReadingEvent, Sensor

if TYPE_CHECKING:
    from techcamp.farms.domain.models import Plot
    from techcamp.telemetry.domain.models import Node

logger = logging.getLogger(__name__)

_MAX_NODES_PER_PLOT = 500
"""ponytail: one `list_for_org` page covers every node on a plot at this
project's scale (the same reasoning and constant as
`telemetry.application.query_readings`)."""

_ONE_SECOND = timedelta(seconds=1)
"""`ReadingRepository.query_raw` is `[start, end)`, so the window has to end just
after the batch's own newest reading: that sample is the evidence the decision
is about."""

_REPRESENTATIVE_LOOKBACK = timedelta(hours=24)
"""docs/06 §5: a representative sensor needs "lectura válida en 24 h", so the
rolling window that proves it is the 24 h ending at the decision instant. The
daily balance proves the same thing with the local day D−1 (its D2/D3 anchor);
the rule is shared, the window is each caller's (D26)."""


def _plot_rules(rules: Sequence[AlertRule]) -> list[tuple[AlertRule, str]]:
    """The `(rule, metric)` pairs this evaluator decides: a plot rule (D17) with
    an operator `decide_alert` can compare, one pair per rule to read readings
    for.

    The rule code, never a per-rule branch: the codes of the other four sources
    of docs/06 §3 are excluded by the domain, and an operator outside `(`<`,
    `>`)` leaves `decide_alert` nothing to compare.
    """
    return [
        (r, m) for r in rules if (m := plot_rule_metric(r)) is not None and r.operator in ("<", ">")
    ]


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
    recover: UnitOfWorkRecovery,
    calibrations: CalibrationRepository,
    balances: WaterBalanceRepository,
) -> None:
    """Decide every reading-threshold rule of the plots the batch just landed.

    `events` are the readings this batch processed (D16: the hook runs after the
    batch commit, and it runs again for a batch a failed flush re-queued), so
    each plot is decided at **its own** newest reading of the batch: a batch
    mixes plots whose readings arrive at different times, and judging one of them
    at another's timestamp would drop its series as stale (`sustained_run`) and
    leave it unevaluated until the next batch.

    A plot that raises is logged and skipped, and the loop continues: the hook is
    awaited inside the ingestor's flush, so an exception here would re-queue the
    whole batch to fail identically on every attempt, and one bad plot would
    silence the alerts of every plot sharing it. The failed plot is not lost —
    the evaluation is stateless over the stored readings, so its next batch
    decides it again.

    `recover` runs after the log and before the next plot: a failure that came
    from the database leaves the shared session in a failed-transaction state,
    and without the rollback every later plot would raise `PendingRollbackError`
    and be swallowed here, which is the silence this isolation exists to prevent.
    """
    org_by_plot: dict[UUID, UUID] = {}
    at_by_plot: dict[UUID, datetime] = {}
    for event in events:
        org_by_plot.setdefault(event.plot_id, event.org_id)
        current = at_by_plot.get(event.plot_id)
        if current is None or event.at > current:
            at_by_plot[event.plot_id] = event.at

    for plot_id, org_id in org_by_plot.items():
        try:
            plot = await plots.get_for_orgs(plot_id, [org_id])
            if plot is None:
                continue
            await _evaluate_plot(
                plot,
                at=at_by_plot[plot_id],
                rules=rules,
                readings=readings,
                sensors=sensors,
                nodes=nodes,
                soils=soils,
                alerts=alerts,
                calibrations=calibrations,
                balances=balances,
            )
        except Exception:
            logger.exception(
                "alerts: plot %s (org %s) not evaluated, its next batch decides it again",
                plot_id,
                org_id,
            )
            await recover()


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
    calibrations: CalibrationRepository,
    balances: WaterBalanceRepository,
) -> None:
    threshold_rules = _plot_rules(await rules.list_for_org(plot.org_id))
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
    sensors_by_metric = await _sensors_by_metric(plot, plot_nodes, sensors=sensors)
    # `water_stress` is the one plot rule whose threshold and series are the
    # plot's own (docs/06 §3, §5; ADR-0022), so it is resolved once here instead
    # of per rule. Read only when the org actually has the rule: a plot whose
    # rules are all plain thresholds pays nothing for it.
    stress_moisture_pct: float | None = None
    representative: set[int] = set()
    if any(rule.code == "water_stress" for rule, _ in threshold_rules):
        evidence = await balance_stress_evidence(
            balances, org_id=plot.org_id, plot_id=plot.id, to_day=local_today(at)
        )
        stress_moisture_pct = None if evidence is None else evidence.stress_moisture_pct
        representative = {
            sensor.sensor_id
            for sensor in await representative_soil_moisture_sensors(
                org_id=plot.org_id,
                plot_id=plot.id,
                root_depth_cm=None if soil is None else soil.root_depth_cm,
                start=at - _REPRESENTATIVE_LOOKBACK,
                end=at + _ONE_SECOND,
                nodes=nodes,
                sensors=sensors,
                calibrations=calibrations,
                readings=readings,
            )
        }

    for rule, metric in threshold_rules:
        # D2: an open alert also resolves on a 60 min clear run, so the window
        # covers the longer of the two runs.
        window = max(rule.min_duration, RESOLUTION_WINDOW)
        sources = sensors_by_metric
        if rule.code == "water_stress":
            # docs/06 §5: "Solo ese sensor alimenta la regla `water_stress` sobre
            # lecturas" (D29). The other soil-moisture sensors of the plot keep
            # feeding the rules that are decided on the plot's series; an empty
            # set leaves this rule with no samples, so it is not decided here.
            sources = {
                **sensors_by_metric,
                metric: _sensors_of(sensors_by_metric, metric, representative),
            }
        samples = await _samples(
            metric,
            org_id=plot.org_id,
            sensors_by_metric=sources,
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
            stress_moisture_pct=stress_moisture_pct,
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


def _sensors_of(
    sensors_by_metric: Mapping[str, Sequence[Sensor]], metric: str, keep: set[int]
) -> list[Sensor]:
    """The plot's sensors of `metric` that are in `keep` (the representative ones)."""
    return [sensor for sensor in sensors_by_metric.get(metric, ()) if sensor.id in keep]


async def _sensors_by_metric(
    plot: Plot,
    plot_nodes: Sequence[Node],
    *,
    sensors: SensorRepository,
) -> dict[str, list[Sensor]]:
    """Every sensor of every node of the plot, grouped by metric.

    Read once per plot evaluation instead of once per rule: every rule of a plot
    asks the same question of the same nodes. The readings are still read per
    rule, because each rule has its own window.
    """
    by_metric: dict[str, list[Sensor]] = {}
    for node in plot_nodes:
        for sensor in await sensors.list_for_node(node.id, plot.org_id):
            by_metric.setdefault(sensor.metric, []).append(sensor)
    return by_metric


async def _samples(
    metric: str,
    *,
    org_id: UUID,
    sensors_by_metric: Mapping[str, Sequence[Sensor]],
    readings: ReadingRepository,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float]]:
    """The `(time, value)` samples of `metric` over the window, one sequence for
    the plot, ordered by time (`sustained_run` walks it backwards).

    `query_valid_raw` is the read that excludes the out-of-range readings
    (docs/06 §1: they "no disparan alertas"), flag by flag and without touching
    what the readings API returns, and it is scoped to `org_id`
    (docs/09:47) like every other read of a rule.
    """
    samples: list[tuple[datetime, float]] = []
    for sensor in sensors_by_metric.get(metric, ()):
        samples.extend(
            (point.time, point.value)
            for point in await readings.query_valid_raw(sensor.id, org_id, start=start, end=end)
        )
    return sorted(samples)
