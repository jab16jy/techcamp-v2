"""Node health every 5 minutes in the worker (docs/06 §3, "Salud del nodo";
docs/10 §3 `m[cada 5 min: salud de nodos]`; D4, D18, D21).

`node_offline` is decided on the absence of evidence, so it has its own use
case instead of the reading-threshold evaluator: one bounded page of one
organization's nodes, each decided at the sweep's own `at` (the decision time of
the job, not of any other node's clock), and acted on through the same
lifecycle use cases the ingestor uses.

D4 needs nothing here: `open_alert` resolves the recipients, and the technician
falls back to the org owners when the farm has none.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.alerts.application.use_cases import open_alert, resolve_automatically
from techcamp.alerts.domain import (
    RESOLUTION_WINDOW,
    AlertAction,
    AlertRule,
    decide_node_health,
    heard_from_run,
    node_silence_window,
)
from techcamp.telemetry.application.ports import NodeRepository, ReadingRepository, SensorRepository

if TYPE_CHECKING:
    from techcamp.telemetry.domain.models import Node

_MAX_NODES_PER_ORG = 500
"""ponytail: one `list_for_org` page covers every node of one organization at
this project's scale (the same reasoning and constant as
`evaluate_readings._MAX_NODES_PER_PLOT`)."""

_ONE_SECOND = timedelta(seconds=1)
"""`ReadingRepository.query_raw` is `[start, end)`, so the window has to end
just after the sweep's own decision time."""


async def evaluate_node_health(
    *,
    org_id: UUID,
    at: datetime,
    rules: AlertRuleRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    """Decide `node_offline` for one organization's nodes (D21).

    The fan-out is per organization precisely so this use case never reads
    across orgs: `NodeRepository.list_for_org` requires `org_id` (docs/09
    org isolation) and the org is a parameter here, not a filter a caller could
    leave out.
    """
    rule = _node_offline(await rules.list_for_org(org_id))
    if rule is None:
        # The factory rule is seeded by the migration, so an org without it is
        # a broken deployment and this sweep has nothing to decide. Same as the
        # reading-threshold evaluator, which decides nothing without rules.
        return
    for node in await nodes.list_for_org(org_id, limit=_MAX_NODES_PER_ORG):
        await _evaluate_node(node, rule, at=at, sensors=sensors, readings=readings, alerts=alerts)


def _node_offline(rules: list[AlertRule]) -> AlertRule | None:
    for rule in rules:
        if rule.code == "node_offline":
            return rule
    return None


async def _evaluate_node(
    node: Node,
    rule: AlertRule,
    *,
    at: datetime,
    sensors: SensorRepository,
    readings: ReadingRepository,
    alerts: AlertRepository,
) -> None:
    assert node.org_id is not None, "a node listed for an org belongs to it"
    # `get_non_resolved_for_target` never returns a resolved alert (the partial
    # unique index's own scope), so it is the "current" alert of the pair.
    current = await alerts.get_non_resolved_for_target(
        rule_id=rule.id, org_id=node.org_id, plot_id=None, node_id=node.id
    )
    # The clear run is only read for a node that already has an open alert: a
    # silent node needs the absence of evidence, which `last_seen_at` already is.
    decision = decide_node_health(
        last_seen_at=node.last_seen_at,
        at=at,
        interval_s=node.interval_s,
        current_alert=current,
        heard_run=(
            await _heard_run(node, org_id=node.org_id, at=at, sensors=sensors, readings=readings)
            if current is not None
            else None
        ),
    )
    match decision.action:
        case AlertAction.OPEN:
            await open_alert(
                rule=rule,
                at=at,
                alerts=alerts,
                node_id=node.id,
                evidence={
                    "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
                    "interval_s": node.interval_s,
                },
            )
        case AlertAction.RESOLVE:
            assert decision.alert is not None, "a resolve decision carries the alert"
            target = await alerts.get_target_context(plot_id=None, node_id=node.id)
            await resolve_automatically(
                alert_id=decision.alert.id,
                org_id=node.org_id,
                farm_id=target.farm_id,
                at=at,
                alerts=alerts,
            )
        case AlertAction.UPGRADE | AlertAction.NO_ACTION:
            # A node-health rule has no upgrade (docs/06 §3 lists none) and a
            # healthy node changes nothing.
            pass


async def _heard_run(
    node: Node,
    *,
    org_id: UUID,
    at: datetime,
    sensors: SensorRepository,
    readings: ReadingRepository,
) -> timedelta | None:
    """How long this node has been heard from, read back from its own readings.

    `query_raw`, not `query_valid_raw`: a value outside the physical range is
    still the node speaking, and `node_offline` is about the silence, not about
    the reading. The margin is the node's own 3 × `interval_s`, so a node on a
    longer interval is judged on its own clock, and the window reaches one
    margin past the resolution window because the run has to be able to reach
    `RESOLUTION_WINDOW` while the read is cut at its start.
    """
    max_gap = node_silence_window(node.interval_s)
    start = at - RESOLUTION_WINDOW - max_gap
    samples: list[tuple[datetime, float]] = []
    for sensor in await sensors.list_for_node(node.id, org_id):
        samples.extend(
            (point.time, point.value)
            for point in await readings.query_raw(sensor.id, start=start, end=at + _ONE_SECOND)
        )
    return heard_from_run(sorted(samples), at, max_gap=max_gap)
