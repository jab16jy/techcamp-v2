"""Technician tray use case (E9 T3; docs/04 §Visitas de extensión y bandeja del técnico; D-T0.8).

Composes farms assigned to the caller across all memberships, their open
alerts (critical first, then newest), and the last visit date. Ordered:
1. Open critical alerts desc
2. Open alerts desc
3. last_visit_on asc with null first (never-visited before visited; older before newer)
4. Farm name asc (tiebreak)
5. Farm id asc (tiebreak)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from techcamp.alerts.application import list_open_alerts_for_plots
from techcamp.alerts.application.ports import AlertRepository
from techcamp.farms.application import list_farms_for_technician
from techcamp.farms.application.ports import FarmRepository, PlotRepository
from techcamp.home.application.plot_status import OpenAlert
from techcamp.identity.application.ports import MembershipRepository
from techcamp.logbook.application import get_latest_visit_dates
from techcamp.logbook.application.ports import ExtensionVisitRepository


@dataclass(frozen=True, slots=True)
class FarmSummary:
    """The docs/04 `farm` field in `/me/tray`: compact summary with no geometry.

    Same reasoning as T2's `PlotSummary`: one request on 3G.
    """

    id: UUID
    org_id: UUID
    name: str
    municipality_code: str


@dataclass(frozen=True, slots=True)
class TechnicianTrayItem:
    """One item in the technician's tray (docs/04 §Visitas; D-T0.8)."""

    farm: FarmSummary
    open_alerts: list[OpenAlert]
    last_visit_on: date | None


def _alert_sort_key(alert: OpenAlert) -> tuple[int, float, UUID]:
    """Sort key for each farm's open alerts (docs/04 §Visitas; D-T0.8; Refs #211):

    1. Critical first (0 before 1)
    2. Newest opened_at first (-timestamp)
    3. Alert id asc (tiebreaker)
    """
    severity_rank = 0 if alert.severity == "critical" else 1
    return (severity_rank, -alert.opened_at.timestamp(), alert.id)


def _tray_sort_key(item: TechnicianTrayItem) -> tuple[int, int, int, date, str, UUID]:
    """Sort key for D-T0.8 order:

    1. open critical alerts desc
    2. open alerts desc
    3. last_visit_on asc with null first
    4. farm name asc
    5. farm id asc
    """
    critical_count = sum(1 for a in item.open_alerts if a.severity == "critical")
    total_count = len(item.open_alerts)
    has_visit = 0 if item.last_visit_on is None else 1
    visit_date = date.min if item.last_visit_on is None else item.last_visit_on
    return (
        -critical_count,
        -total_count,
        has_visit,
        visit_date,
        item.farm.name,
        item.farm.id,
    )


async def build_technician_tray(
    *,
    user_id: UUID,
    memberships: MembershipRepository,
    farms: FarmRepository,
    plots: PlotRepository,
    alerts: AlertRepository,
    visits: ExtensionVisitRepository,
) -> list[TechnicianTrayItem]:
    """Compose the technician tray for the caller (D-T0.8).

    Farms where `technician_id` matches the caller across all the caller's
    memberships. Any user without assigned farms receives `[]` (200, never 403).
    """
    user_memberships = await memberships.list_for_user(user_id)
    org_ids = [m.org_id for m in user_memberships]
    if not org_ids:
        return []

    technician_farms = await list_farms_for_technician(
        technician_id=user_id,
        org_ids=org_ids,
        farms=farms,
    )
    if not technician_farms:
        return []

    all_plot_ids: list[UUID] = []
    plot_to_farm: dict[UUID, UUID] = {}
    for farm in technician_farms:
        farm_plots = await plots.list_for_farm(farm.id, farm.org_id)
        for p in farm_plots:
            all_plot_ids.append(p.id)
            plot_to_farm[p.id] = farm.id

    # ONE call for all plots of all farms (D-T0.8: no N+1 for alerts)
    alerts_by_farm: dict[UUID, list[OpenAlert]] = {farm.id: [] for farm in technician_farms}
    if all_plot_ids:
        raw_alerts = await list_open_alerts_for_plots(
            plot_ids=all_plot_ids,
            org_ids=org_ids,
            alerts=alerts,
        )
        for alert in raw_alerts:
            if alert.plot_id is not None and alert.plot_id in plot_to_farm:
                target_farm_id = plot_to_farm[alert.plot_id]
                alerts_by_farm[target_farm_id].append(
                    OpenAlert(
                        id=alert.id,
                        org_id=alert.org_id,
                        rule_id=alert.rule_id,
                        rule_code=alert.rule_code,
                        plot_id=alert.plot_id,
                        node_id=alert.node_id,
                        state=alert.state.value,
                        severity=alert.severity.value,
                        evidence=alert.evidence,
                        opened_at=alert.opened_at,
                        acknowledged_at=alert.acknowledged_at,
                        resolved_at=alert.resolved_at,
                        escalated_at=alert.escalated_at,
                        resolution_note=alert.resolution_note,
                    )
                )

    for farm_alerts in alerts_by_farm.values():
        farm_alerts.sort(key=_alert_sort_key)

    # ONE call for last visit dates (D-T0.8)
    farm_ids = [farm.id for farm in technician_farms]
    latest_visits = await get_latest_visit_dates(
        farm_ids=farm_ids,
        org_ids=org_ids,
        visits=visits,
    )

    items = [
        TechnicianTrayItem(
            farm=FarmSummary(
                id=farm.id,
                org_id=farm.org_id,
                name=farm.name,
                municipality_code=farm.municipality_code,
            ),
            open_alerts=alerts_by_farm[farm.id],
            last_visit_on=latest_visits.get(farm.id),
        )
        for farm in technician_farms
    ]

    items.sort(key=_tray_sort_key)
    return items
