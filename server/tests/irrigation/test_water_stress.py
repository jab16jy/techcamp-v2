"""The evidence `water_stress` is decided on, read through the irrigation
application package (docs/06 §3 "Reglas de fábrica" and §5, ADR-0022; D25–D27).

`run_daily_balance`'s own tests cover the representative-sensor rule through the
balance that assimilates it (one sensor near Zr/2, two averaged, `lab`
calibration, three or more, NaN, out of range, the D−1 window edges). What is new
here is what the ALERTS caller asks of the same two functions: a plot whose soil
is incomplete, and the balance evidence the daily rule reads (D27).
"""

from __future__ import annotations

import decimal
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import (
    FarmRow,
    PlotRow,
    SoilProfileRow,
)
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.orm import WaterBalanceDailyRow
from techcamp.irrigation.adapters.repositories import SqlAlchemyWaterBalanceRepository
from techcamp.irrigation.application.water_stress import (
    BALANCE_WINDOW_DAYS,
    balance_stress_evidence,
    representative_soil_moisture_sensors,
)
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_DAY = date(2026, 9, 26)
"""The day a 04:50 run decides; the newest balance it reads is the one for D−1."""


async def _make_plot(db_session: AsyncSession, *, root_depth_cm: float | None) -> tuple[UUID, UUID]:
    org_id, farm_id, plot_id = uuid7(), uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Org", kind="individual"))
    await db_session.flush()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Farm", municipality_code="47001", location=_POINT)
    )
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    if root_depth_cm is not None:
        db_session.add(
            SoilProfileRow(
                plot_id=plot_id,
                source="lab",
                field_capacity_pct=23.0,
                wilting_point_pct=9.0,
                root_depth_cm=decimal.Decimal(str(root_depth_cm)),
            )
        )
    await db_session.commit()
    return org_id, plot_id


async def _add_representative_sensor(
    db_session: AsyncSession, org_id: UUID, plot_id: UUID, *, depth_cm: int
) -> int:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid4().hex[:8]}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=datetime(2026, 8, 1, tzinfo=UTC),
            status="online",
        )
    )
    sensor = SensorRow(
        node_id=node_id,
        channel_key=f"soil_moisture_{depth_cm}",
        metric="soil_moisture",
        unit="pct",
        depth_cm=depth_cm,
    )
    db_session.add(sensor)
    await db_session.flush()
    db_session.add(
        CalibrationRow(
            id=uuid7(),
            sensor_id=sensor.id,
            version=1,
            method="linear",
            kind="field",
            params={"scale": 1.0, "offset": 0.0},
            valid_from=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    db_session.add(
        ReadingRow(
            time=datetime(2026, 9, 25, 18, 0, tzinfo=UTC),
            sensor_id=sensor.id,
            raw_value=18.0,
            value=18.0,
            received_at=datetime(2026, 9, 25, 18, 0, tzinfo=UTC),
            quality=0,
        )
    )
    await db_session.commit()
    return sensor.id


def _store_balance(
    db_session: AsyncSession,
    plot_id: UUID,
    day: date,
    *,
    depletion_mm: float,
    raw_mm: float = 50.0,
    stress_moisture_pct: float = 15.3,
) -> None:
    db_session.add(
        WaterBalanceDailyRow(
            plot_id=plot_id,
            day=day,
            etc_mm=decimal.Decimal("5.0"),
            effective_rain_mm=decimal.Decimal("0.0"),
            irrigation_mm=decimal.Decimal("0.0"),
            taw_mm=decimal.Decimal("100.0"),
            raw_mm=decimal.Decimal(str(raw_mm)),
            depletion_model_mm=decimal.Decimal(str(depletion_mm)),
            depletion_mm=decimal.Decimal(str(depletion_mm)),
            soil_moisture_obs_pct=None,
            assimilation_k=decimal.Decimal("0"),
            stress_moisture_pct=decimal.Decimal(str(stress_moisture_pct)),
        )
    )


# -- the representative sensor, as the alert caller asks for it --


async def test_a_plot_without_a_root_depth_has_no_representative_sensor(
    db_session: AsyncSession,
) -> None:
    """docs/06 §5: without Zr there is no root zone, so no depth can be
    representative. The 04:30 job skips such a plot for the same reason, and the
    balance branch of `water_stress` owns it instead (D29)."""
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=None)
    sensor_id = await _add_representative_sensor(db_session, org_id, plot_id, depth_cm=50)

    sensors = await representative_soil_moisture_sensors(
        org_id=org_id,
        plot_id=plot_id,
        root_depth_cm=None,
        start=datetime(2026, 9, 25, tzinfo=UTC),
        end=datetime(2026, 9, 26, tzinfo=UTC),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
    )

    assert sensors == []
    assert sensor_id > 0  # the sensor exists; the rule is what rejects it


async def test_a_representative_sensor_carries_its_id_depth_and_mean(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=100.0)
    sensor_id = await _add_representative_sensor(db_session, org_id, plot_id, depth_cm=50)
    db_session.add(
        ReadingRow(
            time=datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
            sensor_id=sensor_id,
            raw_value=14.0,
            value=14.0,
            received_at=datetime(2026, 9, 25, 20, 0, tzinfo=UTC),
            quality=0,
        )
    )
    await db_session.commit()

    sensors = await representative_soil_moisture_sensors(
        org_id=org_id,
        plot_id=plot_id,
        root_depth_cm=100.0,
        start=datetime(2026, 9, 25, tzinfo=UTC),
        end=datetime(2026, 9, 26, tzinfo=UTC),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
    )

    assert [(s.sensor_id, s.depth_cm, s.mean_moisture_pct) for s in sensors] == [
        (sensor_id, 50.0, 16.0)
    ]


# -- the balance evidence (D27) --


async def test_a_plot_without_a_balance_row_has_no_evidence(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=100.0)

    evidence = await balance_stress_evidence(
        SqlAlchemyWaterBalanceRepository(db_session),
        org_id=org_id,
        plot_id=plot_id,
        to_day=_DAY,
    )

    assert evidence is None


async def test_the_newest_balance_day_is_the_threshold_and_its_row_the_last_sample(
    db_session: AsyncSession,
) -> None:
    """The threshold is the θ_estrés of the NEWEST balance day (docs/06 §5: the
    irrigation job recalculates it every day), and the series ends with that same
    day."""
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=100.0)
    _store_balance(
        db_session, plot_id, _DAY - timedelta(days=2), depletion_mm=10.0, stress_moisture_pct=20.0
    )
    _store_balance(
        db_session, plot_id, _DAY - timedelta(days=1), depletion_mm=60.0, stress_moisture_pct=15.3
    )
    await db_session.commit()

    evidence = await balance_stress_evidence(
        SqlAlchemyWaterBalanceRepository(db_session),
        org_id=org_id,
        plot_id=plot_id,
        to_day=_DAY,
    )

    assert evidence is not None
    assert evidence.stress_moisture_pct == 15.3
    assert [margin for _, margin in evidence.margin_samples] == pytest.approx([-0.8, 0.2])


async def test_a_day_with_no_positive_raw_contributes_no_sample(
    db_session: AsyncSession,
) -> None:
    """E6's D5: `RAW <= 0` has no positive threshold to compare the depletion
    against, so that day is not evidence of stress in either direction."""
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=100.0)
    _store_balance(db_session, plot_id, _DAY - timedelta(days=1), depletion_mm=60.0)
    _store_balance(db_session, plot_id, _DAY - timedelta(days=2), depletion_mm=60.0, raw_mm=0.0)
    await db_session.commit()

    evidence = await balance_stress_evidence(
        SqlAlchemyWaterBalanceRepository(db_session),
        org_id=org_id,
        plot_id=plot_id,
        to_day=_DAY,
    )

    assert evidence is not None
    assert len(evidence.margin_samples) == 1


async def test_a_balance_older_than_the_window_is_not_evidence(
    db_session: AsyncSession,
) -> None:
    """D27's window: the 48 h horizon plus the clear run, with margin. A θ_estrés
    older than that belongs to a crop stage the plot has left (docs/06 §5
    recalculates it every day)."""
    org_id, plot_id = await _make_plot(db_session, root_depth_cm=100.0)
    _store_balance(
        db_session,
        plot_id,
        _DAY - timedelta(days=BALANCE_WINDOW_DAYS + 1),
        depletion_mm=60.0,
    )
    await db_session.commit()

    evidence = await balance_stress_evidence(
        SqlAlchemyWaterBalanceRepository(db_session),
        org_id=org_id,
        plot_id=plot_id,
        to_day=_DAY,
    )

    assert evidence is None
