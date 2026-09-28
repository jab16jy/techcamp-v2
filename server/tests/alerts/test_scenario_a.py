"""Scenario A through the real ingest path (docs/06 §10 "Aritmética del
escenario A", §3 "Reglas de fábrica"; docs/10:70's E7 exit; docs/09:47).

The unit-level tests each prove one half: the domain arithmetic at sample
granularity (`test_domain.py::test_scenario_a_soil_moisture_linear_fall_and_resolution`),
the reading rule over stored readings (`test_evaluate_readings.py`), the outbox and
the dispatcher (`test_lifecycle.py`, `tests/notifications/`). This file is the one
place where the readings arrive the way a node's arrive — `ingest_uplinks`, with
the alert evaluator injected exactly as `techcamp.ingestor` injects it (D9) — and
the two alerts of the scenario are read back off the rows that path wrote.

Real Postgres, no doubles: the claim is about what the ingest path stores, and a
double would store whatever the test told it to.

**The instants below are the DOC's, hardcoded, not read back from the fixture.**
`water_stress` crosses θ_estrés = 15,3 % at `t = 14 × (22 − 15,3) / (22 − 13) ≈
10,4` days and opens 6 h later (día ≈ 10,7); on the scenario's own `interval_s:
900` grid that is sample 1001 and sample 1025, with sample 1024 holding a 5 h
45 min run that is not enough. `heat_stress` is `air_temp > 35 °C` sustained 3 h
over a 26–37 °C day, so it opens at sample 47 and not at 46. Each assertion below
also states the negative half: the sample before the documented one opens nothing.
"""

from __future__ import annotations

import decimal
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.evaluate_readings import build_evaluator
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.irrigation.adapters.orm import WaterBalanceDailyRow
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyPlotEventsNotifier,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import RawUplink, ingest_uplinks

pytestmark = pytest.mark.anyio

_INTERVAL_S = 900
"""docs/06 §10 `el-nino`: `interval_s: 900`."""
_BACKFILL_DAYS = 14
_SAMPLES = _BACKFILL_DAYS * 86400 // _INTERVAL_S + 1

_START = datetime(2026, 9, 1, 15, 0, tzinfo=UTC)
"""10:00 in Bogotá: a daytime start, so the first day's heat peak is a real
part of the day and the replay does not lean on a quiet-hours coincidence."""

_MOISTURE_FROM, _MOISTURE_TO = 22.0, 13.0
"""docs/06 §10: `soil_moisture_30cm: { start: 22, end: 13 }`."""
_STRESS_MOISTURE_PCT = 15.3
"""θ_estrés = 0,23 − 0,55 × (0,23 − 0,09) = 0,153 → 15,3 % (docs/06 §10)."""
_TEMP_MIN, _TEMP_MAX = 26.0, 37.0
"""docs/06 §10: `air_temp: { daily_min: 26, daily_max: 37 }`."""

_CROSSING_SAMPLE = 1001
"""First sample under θ_estrés: 900 900 s = day 10,4167 (the doc's ≈ 10,4)."""
_NO_OPEN_SAMPLE = 1024
"""5 h 45 min of run — one sample short of the rule's 6 h."""
_OPEN_SAMPLE = 1025
"""6 h of run: 922 500 s = day 10,677, the doc's ≈ 10,7."""

_HEAT_NO_OPEN_SAMPLE = 46
_HEAT_OPEN_SAMPLE = 47
"""`heat_stress` is 3 h over a 26–37 °C day: the first sample above 35 °C is
sample 35 (2 h 45 min of run), and 12 samples later the run is 3 h."""

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@dataclass(frozen=True, slots=True)
class Plot:
    """One scenario-A plot: the node, its two sensors and the people who read
    its alerts (a producer and an owner, plus the technician who owns the farm)."""

    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    node_id: UUID
    moisture_sensor: int
    temp_sensor: int


def _moisture_pct(sample: int) -> float:
    """The scenario's linear fall, 22 % → 13 % over the 14-day backfill."""
    return _MOISTURE_FROM - (_MOISTURE_FROM - _MOISTURE_TO) * sample / (_SAMPLES - 1)


def _air_temp_c(sample: int) -> float:
    """One day of `air_temp`: 26 °C at local midnight, 37 °C at local noon, so
    the day spends about 6,8 h above the rule's 35 °C — a 3 h sustained run is
    possible, and the rule has to wait for it."""
    hour = (sample * _INTERVAL_S) % 86400
    return _TEMP_MIN + (_TEMP_MAX - _TEMP_MIN) * (1 - math.cos(2 * math.pi * hour / 86400)) / 2


async def _make_scenario_plot(
    db_session: AsyncSession,
    *,
    moisture_from: float = _MOISTURE_FROM,
    moisture_to: float = _MOISTURE_TO,
) -> Plot:
    """The plot docs/06 §10's `el-nino` describes: drip-irrigated maize on sandy
    loam (θFC 0,23 / θWP 0,09), a node reporting every 900 s with `soil_moisture`
    at 30 cm — Zr/2, the representative depth — and `air_temp`.

    The soil-moisture sensor carries a `field` calibration, so it is a `K > 0`
    representative sensor (docs/06 §5, ADR-0022) and the READING branch of
    `water_stress` owns this plot (D29). The balance branch is therefore never
    walked here, which is the fact the T10 lesson turns on.
    """
    org_id, farm_id, plot_id, node_id = uuid7(), uuid7(), uuid7(), uuid7()
    owner, producer, technician = uuid7(), uuid7(), uuid7()
    for user_id, name in ((owner, "Owner"), (producer, "Producer"), (technician, "Technician")):
        db_session.add(
            AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=name)
        )
    db_session.add(OrganizationRow(id=org_id, name="Scenario A", kind="individual"))
    await db_session.commit()
    for user_id, role in (
        (producer, Role.PRODUCER),
        (owner, Role.OWNER),
        (technician, Role.TECHNICIAN),
    ):
        db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role.value))
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca El Niño",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician,
        )
    )
    await db_session.commit()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            field_capacity_pct=23.0,
            wilting_point_pct=9.0,
            root_depth_cm=60.0,
        )
    )
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=_INTERVAL_S,
            claimed_at=_START,
            status="online",
        )
    )
    moisture = SensorRow(
        node_id=node_id, channel_key="sm_30", metric="soil_moisture", unit="%", depth_cm=30
    )
    temperature = SensorRow(node_id=node_id, channel_key="t_0", metric="air_temp", unit="°C")
    db_session.add_all([moisture, temperature])
    await db_session.flush()
    for sensor in (moisture, temperature):
        db_session.add(
            CalibrationRow(
                id=uuid7(),
                sensor_id=sensor.id,
                version=1,
                method="linear",
                kind="field",
                params={"scale": 1.0, "offset": 0.0},
                valid_from=_START - timedelta(days=30),
            )
        )
    await db_session.commit()
    await db_session.refresh(moisture)
    await db_session.refresh(temperature)
    return Plot(org_id, farm_id, plot_id, node_id, moisture.id, temperature.id)


async def _store_daily_balances(db_session: AsyncSession, plot: Plot) -> None:
    """One `water_balance_daily` row per local day of the backfill, each with
    the plot's θ_estrés, as E6's 04:30 job writes it (docs/06 §5). The reading
    branch reads the newest row for `local_today(at)`, so a single row would stop
    answering the moment the replay walks past its 7-day window."""
    day = local_today(_START)
    last = local_today(_START + timedelta(seconds=(_SAMPLES - 1) * _INTERVAL_S))
    while day <= last:
        db_session.add(
            WaterBalanceDailyRow(
                plot_id=plot.plot_id,
                day=day,
                etc_mm=decimal.Decimal("5.0"),
                effective_rain_mm=decimal.Decimal("0.0"),
                irrigation_mm=decimal.Decimal("0.0"),
                taw_mm=decimal.Decimal("84.0"),
                raw_mm=decimal.Decimal("46.2"),
                depletion_model_mm=decimal.Decimal("10.0"),
                depletion_mm=decimal.Decimal("10.0"),
                soil_moisture_obs_pct=None,
                assimilation_k=decimal.Decimal("1"),
                stress_moisture_pct=decimal.Decimal(str(_STRESS_MOISTURE_PCT)),
            )
        )
        day += timedelta(days=1)
    await db_session.commit()


def _uplink(plot: Plot, sample: int, *, moisture: float, temperature: float) -> RawUplink:
    """One message as a node sends it (docs/06 §1: `{"v", "seq", "ts", "fw", "m"}`),
    with `received_at` equal to its own clock so a 14-day backfill is not read as
    14 days of dead time."""
    at = _START + timedelta(seconds=sample * _INTERVAL_S)
    payload = json.dumps(
        {
            "v": 1,
            "seq": sample,
            "ts": int(at.timestamp()),
            "fw": "1.0.3",
            "m": {"sm_30": moisture, "t_0": temperature},
        }
    ).encode()
    return RawUplink(node_id=plot.node_id, payload=payload, received_at=at)


def _ports(db_session: AsyncSession) -> dict[str, object]:
    return {
        "nodes": SqlAlchemyNodeRepository(db_session),
        "sensors": SqlAlchemySensorRepository(db_session),
        "calibrations": SqlAlchemyCalibrationRepository(db_session),
        "readings": SqlAlchemyReadingRepository(db_session),
        "plots": SqlAlchemyPlotRepository(db_session),
        "events": SqlAlchemyPlotEventsNotifier(db_session),
        # D9: the ingestor entrypoint composes the hook; the evaluator is built
        # on the very session the flush opened, exactly as in production.
        "after_flush": build_evaluator(db_session),
    }


def _schedule() -> list[tuple[int, bool]]:
    """The replay's flush schedule as `(last_sample, one_per_sample)` pairs.

    **Every reading is published at the node's own `interval_s = 900`.** What
    varies is only how many readings share a FLUSH, and the flush is the unit of
    decision: D9 runs the hook once per flush, at the batch's newest reading, and
    the evaluation is stateless over the STORED readings (D1), so a window is read
    back from `reading` and not carried in memory. Two consequences, and both are
    why this is not the same thing as "one flush per reading":

    - Inside a decision window the cadence is one reading per flush, so the
      instant an alert opens is the sample docs/06 §10's arithmetic names. The
      two windows are the ones the doc's own numbers fall in: `heat_stress`'s 3 h
      run on the first afternoon, and the `water_stress` run from the crossing
      of θ_estrés to its 6 h mark.
    - Elsewhere a flush may carry a whole day of readings, because the window
      is read from storage: a coarser flush still sees the full 6 h / 3 h of
      consecutive samples, so it can open an alert a per-reading flush would also
      open. What a coarse flush changes is the `at` it is decided at, so the
      coarse passes sit on the trajectory's own two computable instants — the
      37 °C peak, where the 3 h violation run is complete, and the night, where
      the 60 min clear run is. Those are the only instants docs/06 §3's two
      windows are complete at, so a coarse pass is never the reason a cycle
      went unseen.

    Per-reading flushing for the whole 14 days costs 128 ms a flush — the
    evaluator pass, not the ingest (measured: 200 single-message flushes 25,7 s
    against one flush of 200 messages 0,6 s, hook included). This schedule keeps
    every instant the doc fixes at sample resolution for about a tenth of that.
    """
    per_sample = {
        *range(0, _HEAT_OPEN_SAMPLE + 2),
        *range(_CROSSING_SAMPLE, _OPEN_SAMPLE + 2),
    }
    days = _SAMPLES // 96
    coarse = {
        # 12 h apart, on the trajectory's OWN phase: sample 48 of each 96-sample
        # day is the 37 °C peak, where the 3 h violation run is complete, and
        # sample 0 is local-midnight-relative night, where the 60 min clear run
        # is. Both windows of docs/06 §3 are only computable at one of these two
        # instants, so these are the instants a coarse pass is placed on.
        sample
        for day in range(days + 1)
        for sample in (day * 96, day * 96 + 48)
        if 0 <= sample <= _SAMPLES - 1
    }
    coarse.add(_CROSSING_SAMPLE - 1)
    checkpoints = sorted(per_sample | {s for s in coarse if 0 <= s <= _SAMPLES - 1})
    return [(sample, sample in per_sample) for sample in checkpoints]


def _batches() -> Iterator[list[int]]:
    """The samples each flush of `_schedule` publishes, in order and without
    repeating one: a flush carries the readings since the previous flush."""
    sent = -1
    for last, one_per_sample in _schedule():
        if one_per_sample:
            for stop in range(sent + 1, last + 1):
                yield [stop]
        else:
            yield list(range(sent + 1, last + 1))
        sent = last


async def _replay(
    db_session: AsyncSession,
    plot: Plot,
    *,
    through: int,
    at_each: dict[int, object] | None = None,
    neighbour: Plot | None = None,
) -> None:
    """Publish samples `0..through` on `_schedule()`, and call `at_each`'s
    callback once that sample's flush is committed.

    `neighbour` is a second organization's plot published in the SAME flush on
    the same cadence, which is what the org-isolation assertion needs: one batch,
    two organizations, one evaluation pass each."""
    ports = _ports(db_session)
    for samples in _batches():
        last = samples[-1]
        if last > through:
            break
        batch = [
            _uplink(
                plot,
                sample,
                moisture=_moisture_pct(sample),
                temperature=_air_temp_c(sample),
            )
            for sample in samples
        ]
        if neighbour is not None:
            batch += [
                _uplink(neighbour, sample, moisture=20.0, temperature=_TEMP_MIN)
                for sample in samples
            ]
        await ingest_uplinks(batch, **ports)
        if at_each is not None and (checkpoint := at_each.get(last)) is not None:
            await checkpoint(last)


async def _open_alerts(
    db_session: AsyncSession, plot_id: UUID, *, org_id: UUID | None = None
) -> dict[str, str]:
    """`rule_code -> state` of a plot's alerts that are NOT resolved.

    Non-resolved, because that is what the partial unique index bounds to one
    per rule and plot (docs/06 §3): a rule that cycled all backfill — `heat_stress`
    does, once a day — has several rows and only one open. `org_id` narrows the
    read the way every repository read is narrowed (docs/09:47)."""
    query = (
        select(AlertRuleRow.code, AlertRow.state)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot_id, AlertRow.state != "resolved")
        .order_by(AlertRuleRow.code)
    )
    if org_id is not None:
        query = query.where(AlertRow.org_id == org_id)
    return {code: state for code, state in (await db_session.execute(query)).all()}


async def _severity(db_session: AsyncSession, plot_id: UUID, code: str) -> str | None:
    return (
        await db_session.execute(
            select(AlertRow.severity)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(AlertRow.plot_id == plot_id, AlertRuleRow.code == code)
        )
    ).scalar_one_or_none()


# -- the two alerts of the scenario, at the instants docs/06 §10 fixes --


async def test_scenario_a_through_ingest_opens_both_alerts_at_the_documented_times(
    db_session: AsyncSession,
) -> None:
    """The E7 exit (docs/10:70) with the readings arriving the way a node's
    arrive: `heat_stress` on its 3 h run, `water_stress` 6 h after the crossing
    of θ_estrés, neither of them a minute earlier."""
    plot = await _make_scenario_plot(db_session)
    await _store_daily_balances(db_session, plot)
    seen: dict[int, dict[str, str]] = {}

    async def record(sample: int) -> None:
        seen[sample] = await _open_alerts(db_session, plot.plot_id)

    await _replay(
        db_session,
        plot,
        through=_OPEN_SAMPLE,
        at_each={
            _HEAT_NO_OPEN_SAMPLE: record,
            _HEAT_OPEN_SAMPLE: record,
            _CROSSING_SAMPLE: record,
            _NO_OPEN_SAMPLE: record,
            _OPEN_SAMPLE: record,
        },
    )

    # `heat_stress`: 3 h over 35 °C, and not a minute before. A 26–37 °C day
    # peaks at 37 °C, so the run is possible on the first afternoon.
    assert seen[_HEAT_NO_OPEN_SAMPLE] == {}, "2 h 45 min of heat is not 3 h"
    assert seen[_HEAT_OPEN_SAMPLE] == {"heat_stress": "open"}
    # Ten days of the fall, decided sample by sample, change nothing: the fixed
    # 20 % threshold the doc rejected would have crossed on day 3,1 and opened
    # `water_stress` a week before there was stress according to FAO-56.
    assert "water_stress" not in seen[_CROSSING_SAMPLE], "the crossing opens nothing by itself"
    assert "water_stress" not in seen[_NO_OPEN_SAMPLE], "5 h 45 min of run is not 6 h"
    assert seen[_OPEN_SAMPLE].get("water_stress") == "open"
    assert await _opened_at(db_session, plot.plot_id, "water_stress") == _START + timedelta(
        seconds=_OPEN_SAMPLE * _INTERVAL_S
    )
    # Only the documented alerts: the soil never reaches θFC + 5 = 28 %, so
    # `waterlogging` stays shut.
    assert "waterlogging" not in await _open_alerts(db_session, plot.plot_id)


async def test_scenario_a_ends_critical_with_a_daily_heat_cycle_and_two_organizations_apart(
    db_session: AsyncSession,
) -> None:
    """The whole backfill, and the two things only the whole run shows.

    **`heat_stress` is a daily cycle, not one alert.** A 26–37 °C day is above
    the rule's 35 °C for about 6,8 h and below its 34 °C clear line for the
    night, so the scenario opens one heat alert per afternoon and resolves it an
    hour into the next night — fourteen of them across fourteen days. That is the
    partial unique index doing its job (one NON-resolved alert per rule and
    plot), and it is the half of the rule docs/06 §3's 60-minute resolution
    window exists for.

    **`water_stress` ends critical.** It opened at day ≈ 10,7 and the run is
    still 3 days old at the end of the backfill, so the 48 h upgrade (D5) has
    fired — which is the state the 2 h escalation clock counts from (D12).

    A second organization's plot publishes a healthy day through the same ports
    in the same flushes, and the first organization's fall never reaches it:
    every read keeps `org_id` (docs/09:47).
    """
    plot = await _make_scenario_plot(db_session)
    await _store_daily_balances(db_session, plot)
    neighbour = await _make_scenario_plot(db_session)
    await _store_daily_balances(db_session, neighbour)

    await _replay(db_session, plot, through=_SAMPLES - 1, neighbour=neighbour)

    # The neighbour: 20 % of soil under a 26 °C day is not evidence of anything,
    # and the fall that opened the first organization's alert is not its evidence.
    assert await _open_alerts(db_session, neighbour.plot_id) == {}
    # The negative half of the isolation: reading the neighbour's plot through
    # the first organization's filter answers nothing at all.
    assert await _open_alerts(db_session, neighbour.plot_id, org_id=plot.org_id) == {}

    heat = await _cycles(db_session, plot.plot_id, "heat_stress")
    assert len(heat) == _BACKFILL_DAYS, "one heat alert per afternoon of the backfill"
    # Every one of them resolved: the backfill's last sample is the fourteenth
    # night (the trajectory's phase puts sample 1344 at the 26 °C trough), and
    # the night is the instant the 60 min clear run completes on.
    assert all(state == "resolved" for _, state in heat), "a day of heat that never cooled off"
    assert await _severity(db_session, plot.plot_id, "water_stress") == "critical"
    assert (await _open_alerts(db_session, plot.plot_id)) == {"water_stress": "open"}


async def _cycles(db_session: AsyncSession, plot_id: UUID, code: str) -> list[tuple[datetime, str]]:
    """`(opened_at, state)` of a plot's alerts of one rule, oldest first."""
    return list(
        (
            await db_session.execute(
                select(AlertRow.opened_at, AlertRow.state)
                .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
                .where(AlertRow.plot_id == plot_id, AlertRuleRow.code == code)
                .order_by(AlertRow.opened_at, AlertRow.id)
            )
        ).all()
    )


async def _opened_at(db_session: AsyncSession, plot_id: UUID, code: str) -> datetime | None:
    return (
        await db_session.execute(
            select(AlertRow.opened_at)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(AlertRow.plot_id == plot_id, AlertRuleRow.code == code)
        )
    ).scalar_one_or_none()
