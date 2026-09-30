"""Builders for the `home` module's tests (E9 T2; docs/04 §Estado de la parcela).

One org/user/plot per environment, plus the optional rows each payload field
reads. A builder that a test doesn't call leaves that source empty, which is how
the "missing data is null" cases are set up: absence, not a zero.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import open_alert, resolve_automatically
from techcamp.alerts.domain import AlertRule, Severity
from techcamp.farms.adapters.orm import (
    CropCycleRow,
    CropRow,
    FarmRow,
    PlotRow,
    SoilProfileRow,
)
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyCropRepository,
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.irrigation.adapters.orm import (
    IrrigationRecommendationRow,
    WaterBalanceDailyRow,
)
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.logbook.adapters.orm import ExtensionVisitRow
from techcamp.logbook.adapters.repositories import SqlAlchemyExtensionVisitRepository
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

POINT = "SRID=4326;POINT(-74.1 10.9)"
BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
"""10:00 in Bogotá: `local_today` and every default range in this module are
then read against a fixed day (2026-09-30) instead of the test's clock."""

NOW = datetime(2026, 9, 30, 15, 0, tzinfo=UTC)
TODAY = date(2026, 9, 30)
YESTERDAY = TODAY - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class HomeEnv:
    """One org with a single member, farm and plot."""

    session: AsyncSession
    org_id: UUID
    user_id: UUID
    farm_id: UUID
    plot_id: UUID


async def make_env(
    session: AsyncSession,
    *,
    name: str = "Finca Home",
    irrigation_system: str = "drip",
    role: str = "producer",
    technician_id: UUID | None = None,
) -> HomeEnv:
    """An org, a member, a farm and one plot. `irrigation_system='none'` is
    the rainfed plot of ADR-0023 (and carries no efficiency nor flow)."""
    org_id, farm_id, plot_id, user_id = uuid7(), uuid7(), uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Ana"))
    session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    # `identity`, `farms` and `telemetry` map onto separate `Base`s, and a
    # flush cannot order rows across them, so each step lands on its own: the
    # same order `tests/alerts/test_api.py` builds its org in.
    await session.commit()
    session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await session.commit()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name=name,
            municipality_code="47001",
            location=POINT,
            technician_id=technician_id,
        )
    )
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=BOUNDARY,
            irrigation_system=irrigation_system,
        )
    )
    await session.commit()
    return HomeEnv(
        session=session, org_id=org_id, user_id=user_id, farm_id=farm_id, plot_id=plot_id
    )


def repos(session: AsyncSession) -> dict[str, Any]:
    """The repositories `build_plot_status` takes, built on one session.

    Real adapters, not test doubles: the home module composes real queries
    (AGENTS.md §Testing doubles exist only at ports for external I/O).
    """
    return {
        "plots": SqlAlchemyPlotRepository(session),
        "memberships": SqlAlchemyMembershipRepository(session),
        "cycles": SqlAlchemyCropCycleRepository(session),
        "crops": SqlAlchemyCropRepository(session),
        "soil": SqlAlchemySoilProfileRepository(session),
        "nodes": SqlAlchemyNodeRepository(session),
        "sensors": SqlAlchemySensorRepository(session),
        "calibrations": SqlAlchemyCalibrationRepository(session),
        "readings": SqlAlchemyReadingRepository(session),
        "water_balances": SqlAlchemyWaterBalanceRepository(session),
        "recommendations": SqlAlchemyIrrigationRecommendationRepository(session),
        "weather": SqlAlchemyWeatherRepository(session),
        "alerts": SqlAlchemyAlertRepository(session),
    }


def tray_repos(session: AsyncSession) -> dict[str, Any]:
    """The repositories `build_technician_tray` takes, built on one session."""
    return {
        "memberships": SqlAlchemyMembershipRepository(session),
        "farms": SqlAlchemyFarmRepository(session),
        "plots": SqlAlchemyPlotRepository(session),
        "alerts": SqlAlchemyAlertRepository(session),
        "visits": SqlAlchemyExtensionVisitRepository(session),
    }


async def add_farm(
    session: AsyncSession,
    *,
    org_id: UUID,
    name: str,
    technician_id: UUID | None = None,
    municipality_code: str = "47001",
) -> UUID:
    farm_id = uuid7()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name=name,
            municipality_code=municipality_code,
            location=POINT,
            technician_id=technician_id,
        )
    )
    await session.commit()
    return farm_id


async def add_plot(
    session: AsyncSession,
    *,
    org_id: UUID,
    farm_id: UUID,
    name: str = "Lote Extra",
    irrigation_system: str = "drip",
) -> UUID:
    plot_id = uuid7()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name=name,
            boundary=BOUNDARY,
            irrigation_system=irrigation_system,
        )
    )
    await session.commit()
    return plot_id


async def add_visit(
    session: AsyncSession,
    *,
    org_id: UUID,
    farm_id: UUID,
    technician_id: UUID,
    visited_on: date,
    deleted: bool = False,
) -> UUID:
    visit_id = uuid7()
    session.add(
        ExtensionVisitRow(
            id=visit_id,
            org_id=org_id,
            farm_id=farm_id,
            plot_id=None,
            technician_id=technician_id,
            visited_on=visited_on,
            topics=["natural_resources"],
            client_updated_at=NOW,
            deleted_at=NOW if deleted else None,
        )
    )
    await session.commit()
    return visit_id


async def add_node(
    session: AsyncSession,
    env: HomeEnv,
    *,
    claim_code: str,
    last_seen_at: datetime | None = None,
    interval_s: int = 300,
) -> UUID:
    node_id = uuid7()
    session.add(
        NodeRow(
            id=node_id,
            org_id=env.org_id,
            plot_id=env.plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code=claim_code,
            credential_hash="hash",
            firmware="v1",
            interval_s=interval_s,
            claimed_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_seen_at=last_seen_at,
            status="online" if last_seen_at is not None else "provisioned",
        )
    )
    await session.commit()
    return node_id


async def add_sensor(
    session: AsyncSession,
    node_id: UUID,
    *,
    metric: str = "soil_moisture",
    depth_cm: int | None = None,
    channel_key: str,
) -> int:
    session.add(
        SensorRow(
            node_id=node_id,
            channel_key=channel_key,
            metric=metric,
            depth_cm=depth_cm,
            unit="pct" if metric in ("soil_moisture", "air_rh") else "c",
        )
    )
    await session.commit()
    sensor_id = (
        await session.execute(select(SensorRow.id).where(SensorRow.channel_key == channel_key))
    ).scalar_one()
    return sensor_id


async def calibrate(
    session: AsyncSession,
    sensor_id: int,
    *,
    kind: str = "field",
    at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
) -> None:
    """A calibration in effect from `at`. docs/06 §5: only a `field` one can
    make a sensor representative, and an out-of-range `reading` is not valid."""
    session.add(
        CalibrationRow(
            id=uuid7(),
            sensor_id=sensor_id,
            version=1,
            method="linear",
            kind=kind,
            params={"a": 1.0, "b": 0.0},
            valid_from=at,
        )
    )
    await session.commit()


async def add_reading(
    session: AsyncSession,
    sensor_id: int,
    *,
    at: datetime,
    value: float,
    quality: int = 0,
) -> None:
    session.add(
        ReadingRow(
            time=at,
            sensor_id=sensor_id,
            raw_value=value,
            value=value,
            received_at=at,
            quality=quality,
        )
    )
    await session.commit()


async def add_soil(session: AsyncSession, env: HomeEnv, *, root_depth_cm: float | None) -> None:
    """A soil profile. `root_depth_cm` is what D-T2.1 needs for the
    representative sensor (Zr/2), so `None` is the "no profile" case."""
    session.add(
        SoilProfileRow(
            plot_id=env.plot_id,
            source="lab",
            ph=Decimal("6.5"),
            organic_matter_pct=Decimal("2.0"),
            texture="loam",
            field_capacity_pct=Decimal("32.0"),
            wilting_point_pct=Decimal("14.0"),
            root_depth_cm=Decimal(str(root_depth_cm)) if root_depth_cm is not None else None,
        )
    )
    await session.commit()


async def add_cycle(session: AsyncSession, env: HomeEnv, *, sown_on: date) -> CropRow:
    """An active cycle on maize (catalog id 1), whose FAO-56 stages the crop
    migration already seeds (18/27/31/14 days), so this only writes the cycle."""
    crop_id = 1
    crop = await session.get(CropRow, crop_id)
    assert crop is not None, "the catalog seed must provide the maize crop"
    session.add(
        CropCycleRow(
            id=uuid7(),
            plot_id=env.plot_id,
            crop_id=crop_id,
            sown_on=sown_on,
            expected_harvest_on=sown_on + timedelta(days=90),
            status="active",
        )
    )
    await session.commit()
    return crop


async def add_water_balance(
    session: AsyncSession,
    env: HomeEnv,
    *,
    day: date,
    depletion_mm: float = 10.0,
    taw_mm: float = 84.0,
    raw_mm: float = 46.2,
    stress_moisture_pct: float = 18.0,
) -> None:
    session.add(
        WaterBalanceDailyRow(
            plot_id=env.plot_id,
            day=day,
            etc_mm=Decimal("4.0"),
            effective_rain_mm=Decimal("0.0"),
            irrigation_mm=Decimal("0.0"),
            taw_mm=Decimal(str(taw_mm)),
            raw_mm=Decimal(str(raw_mm)),
            depletion_model_mm=Decimal(str(depletion_mm)),
            depletion_mm=Decimal(str(depletion_mm)),
            soil_moisture_obs_pct=Decimal("21.0"),
            assimilation_k=Decimal("0.5"),
            stress_moisture_pct=Decimal(str(stress_moisture_pct)),
        )
    )
    await session.commit()


async def add_recommendation(
    session: AsyncSession,
    env: HomeEnv,
    *,
    day: date,
    kind: str = "irrigate",
    depth_mm: float | None = 12.0,
    duration_min: int | None = 45,
    advice: Sequence[str] = (),
    rationale: dict[str, Any] | None = None,
) -> None:
    session.add(
        IrrigationRecommendationRow(
            id=uuid7(),
            plot_id=env.plot_id,
            day=day,
            kind=kind,
            depth_mm=Decimal(str(depth_mm)) if depth_mm is not None else None,
            duration_min=duration_min,
            advice=list(advice),
            rationale=rationale if rationale is not None else {"forecast_rain_7d_mm": 0.0},
        )
    )
    await session.commit()


async def add_forecast(
    session: AsyncSession,
    env: HomeEnv,
    *,
    days: int = 3,
    fetched_at: datetime | None = None,
    cell_lat: str = "-74.1",
) -> int:
    """`days` forecast rows for the plot's own cell, one per day from today.

    `cell_lat` is a 0.1° grid coordinate, so a second environment gets its own
    cell instead of colliding on `uq_weather_cell_lat_lon`.
    """
    cell = WeatherCellRow(
        id=uuid7().int % 100_000,
        lat=Decimal(cell_lat),
        lon=Decimal("10.9"),
    )
    session.add(cell)
    plot = await session.get(PlotRow, env.plot_id)
    assert plot is not None
    plot.weather_cell_id = cell.id
    for offset in range(days):
        session.add(
            WeatherDailyRow(
                cell_id=cell.id,
                day=TODAY + timedelta(days=offset),
                is_forecast=True,
                et0_mm=Decimal("4.5"),
                rain_mm=Decimal("1.0"),
                tmin_c=Decimal("24.0"),
                tmax_c=Decimal("31.0"),
                rh_mean_pct=Decimal("78.0"),
                fetched_at=fetched_at if fetched_at is not None else NOW - timedelta(hours=1),
            )
        )
    await session.commit()
    return cell.id


async def add_alert(
    session: AsyncSession,
    env: HomeEnv,
    *,
    severity: str = "warning",
    rule_code: str = "water_stress",
    resolve: bool = False,
    at: datetime | None = None,
    plot_id: UUID | None = None,
    farm_id: UUID | None = None,
    org_id: UUID | None = None,
) -> UUID:
    """One alert through the real `open_alert` use case, so `rule_id` always
    references a seeded rule (`list_open_for_plots` joins `alert_rule`).

    One alert per `rule_code`: `open_alert` is idempotent per
    (`rule_id`, target) (docs/06 §3), so a test needing two alerts names two
    different rules. `resolve=True` closes it, for the D-T0.3 negative.
    """
    row = (
        await session.execute(select(AlertRuleRow).where(AlertRuleRow.code == rule_code))
    ).scalar_one()
    rule = AlertRule(
        id=row.id,
        org_id=None,
        code=row.code,
        metric=row.metric,
        operator=row.operator,
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
        crop_id=row.crop_id,
    )
    opened_at = at if at is not None else NOW - timedelta(hours=2)
    target_plot_id = plot_id if plot_id is not None else env.plot_id
    target_farm_id = farm_id if farm_id is not None else env.farm_id
    target_org_id = org_id if org_id is not None else env.org_id
    alerts = SqlAlchemyAlertRepository(session)
    alert = await open_alert(
        rule=rule,
        at=opened_at,
        alerts=alerts,
        plot_id=target_plot_id,
        evidence={"depletion_mm": 50.0},
        severity=Severity(severity),
    )
    if resolve:
        await resolve_automatically(
            alert_id=alert.id,
            org_id=target_org_id,
            farm_id=target_farm_id,
            at=opened_at + timedelta(minutes=5),
            alerts=alerts,
        )
    return alert.id
