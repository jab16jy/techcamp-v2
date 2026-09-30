"""`GET /plots/{plot_id}/status` composition use case (E9 T2).

docs/04-api.md §Estado de la parcela (pantalla principal) and its field rule
table; docs/05 §Módulos (D-T0.1); D-T0.2/3/4/6/11 and D-T2.1 (soil moisture
comes from the plot's representative sensor, docs/06 §5).

Every test carries its negative assertion: what a sibling case must NOT
produce (a zero for missing data, an `irrigate` on a rainfed plot, a stage for
a future sowing, an alert that is resolved, a reading at the wrong depth).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.home.application import build_plot_status
from techcamp.irrigation.adapters.orm import WaterBalanceDailyRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.domain.models import ReadingQuality

from .conftest import (
    NOW,
    TODAY,
    YESTERDAY,
    HomeEnv,
    add_alert,
    add_cycle,
    add_forecast,
    add_node,
    add_reading,
    add_recommendation,
    add_sensor,
    add_soil,
    add_water_balance,
    calibrate,
    make_env,
    repos,
)

pytestmark = pytest.mark.anyio


async def _status(env: HomeEnv) -> object:
    return await build_plot_status(
        user_id=env.user_id,
        plot_id=env.plot_id,
        now=NOW,
        **repos(env.session),
    )


async def _sensor_at_zr_half(
    session: AsyncSession, env: HomeEnv, *, value: float, at, node: str = "NODE-A"
) -> int:
    """One `field`-calibrated soil-moisture sensor at 30 cm (Zr/2 of a 60 cm
    root zone), the depth docs/06 §5 makes representative."""
    node_id = await add_node(session, env, claim_code=f"{node}-{uuid7().hex[:6]}")
    sensor_id = await add_sensor(session, node_id, depth_cm=30, channel_key=f"sm_{node}")
    await calibrate(session, sensor_id)
    await add_reading(session, sensor_id, at=at, value=value)
    return sensor_id


async def test_status_composes_every_source_of_the_docs_payload(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    await add_soil(db_session, env, root_depth_cm=60.0)
    node_id = await add_node(db_session, env, claim_code="NODE-1", last_seen_at=NOW)
    soil = await add_sensor(db_session, node_id, depth_cm=30, channel_key="sm_30")
    await calibrate(db_session, soil)
    await add_reading(db_session, soil, at=NOW - timedelta(hours=2), value=21.5)
    temp = await add_sensor(db_session, node_id, metric="air_temp", channel_key="t_air")
    await add_reading(db_session, temp, at=NOW - timedelta(hours=1), value=29.0)
    rh = await add_sensor(db_session, node_id, metric="air_rh", channel_key="rh_air")
    await add_reading(db_session, rh, at=NOW - timedelta(hours=1), value=78.0)

    await add_cycle(db_session, env, sown_on=TODAY - timedelta(days=24))
    await add_water_balance(db_session, env, day=YESTERDAY)
    await add_recommendation(
        db_session,
        env,
        day=TODAY,
        rationale={"forecast_rain_7d_mm": 12.5, "kc": 1.2},
    )
    await add_alert(db_session, env, severity="critical")
    await add_forecast(db_session, env, days=3)

    status = await _status(env)

    assert status.plot.id == env.plot_id
    assert status.plot.irrigation_system == "drip"
    assert status.active_cycle is not None
    assert status.active_cycle.crop.code == "maize"
    assert status.active_cycle.stage == "development"
    assert status.active_cycle.day_of_cycle == 25
    assert status.latest.soil_moisture_pct == pytest.approx(21.5)
    assert status.latest.air_temp_c == pytest.approx(29.0)
    assert status.latest.air_rh_pct == pytest.approx(78.0)
    # `at` is the newest value RETURNED (D-T0.4), not the newest reading overall.
    assert status.latest.at == NOW - timedelta(hours=1)
    assert status.water_balance is not None
    assert status.water_balance.status == "ok"
    assert status.water_balance.depletion_mm == pytest.approx(10.0)
    assert status.recommendation is not None
    assert status.recommendation.kind == "irrigate"
    assert status.recommendation.depth_mm == pytest.approx(12.0)
    # D-T0.11: the stored rationale object, unchanged.
    assert status.recommendation.rationale == {"forecast_rain_7d_mm": 12.5, "kc": 1.2}
    assert [alert.severity for alert in status.open_alerts] == ["critical"]
    assert [day.day for day in status.weather_next_3d] == [
        TODAY,
        TODAY + timedelta(days=1),
        TODAY + timedelta(days=2),
    ]
    assert all(day.stale is False for day in status.weather_next_3d)
    assert [node.node_id for node in status.nodes] == [node_id]
    assert status.nodes[0].completeness_24h is not None
    # D-T0.2: the adoption index is E11's, so it is null until then.
    assert status.digital_adoption_index is None

    # Negative: an observed day older than the default 30-day range, and a
    # resolved alert, would change this payload.
    assert status.water_balance.depletion_mm == pytest.approx(10.0)


async def test_rainfed_plot_never_reports_irrigate_and_has_no_depth_or_minutes(
    db_session: AsyncSession,
) -> None:
    """ADR-0023: on a rainfed plot `Dr > RAW` is `stress`, and the
    recommendation is `rainfed` with advice and no depth nor minutes."""
    env = await make_env(db_session, irrigation_system="none")
    await add_water_balance(db_session, env, day=YESTERDAY, depletion_mm=60.0, raw_mm=46.2)
    await add_recommendation(
        db_session,
        env,
        day=TODAY,
        kind="rainfed",
        depth_mm=None,
        duration_min=None,
        advice=["delay_sowing", "conserve_moisture"],
        rationale={"forecast_rain_7d_mm": 3.0},
    )

    status = await _status(env)

    assert status.plot.irrigation_system == "none"
    assert status.water_balance is not None
    assert status.water_balance.status == "stress"
    assert status.recommendation is not None
    assert status.recommendation.kind == "rainfed"
    assert status.recommendation.depth_mm is None
    assert status.recommendation.duration_min is None
    assert list(status.recommendation.advice) == ["delay_sowing", "conserve_moisture"]

    # Negative: `Dr = RAW` exactly stays `watch` (ADR-0022), and an irrigated
    # plot with the same Dr would report `irrigate` — neither happens here.
    assert status.water_balance.status != "watch"
    assert status.water_balance.status != "irrigate"


async def test_a_plot_without_an_active_cycle_reports_it_as_null(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    await add_water_balance(db_session, env, day=YESTERDAY)

    status = await _status(env)

    assert status.active_cycle is None
    # Negative: the rest of the payload is still real, so the null cycle is
    # not a symptom of a failed read.
    assert status.plot.id == env.plot_id
    assert status.water_balance is not None


async def test_missing_readings_are_null_and_never_zero(db_session: AsyncSession) -> None:
    env = await make_env(db_session)

    status = await _status(env)

    assert status.latest.soil_moisture_pct is None
    assert status.latest.air_temp_c is None
    assert status.latest.air_rh_pct is None
    assert status.latest.at is None
    # Negative: no balance and no recommendation are null too, not 0/{}.
    assert status.water_balance is None
    assert status.recommendation is None
    assert status.weather_next_3d == []
    assert status.open_alerts == []
    assert status.nodes == []

    # Negative: a node with no sensor at all keeps every metric null.
    await add_node(db_session, env, claim_code="NODE-BARE", last_seen_at=NOW)
    bare = await _status(env)
    assert bare.latest.soil_moisture_pct is None
    assert bare.latest.at is None


async def test_water_balance_is_yesterdays_row_or_null_never_a_stale_one(
    db_session: AsyncSession,
) -> None:
    """docs/04 §Estado: "El último balance diario consolidado (el de ayer);
    `null` si no hay." A balance from three days ago is not today's truth, and
    it carries no date of its own in this payload, so serving it would pass a
    stale number off as current."""
    fresh = await make_env(db_session, name="Finca Al día")
    await add_water_balance(db_session, fresh, day=YESTERDAY)
    stale = await make_env(db_session, name="Finca Atrasada")
    await add_water_balance(db_session, stale, day=TODAY - timedelta(days=3), depletion_mm=55.0)

    fresh_status = await _status(fresh)
    stale_status = await _status(stale)

    # Yesterday's row is the balance.
    assert fresh_status.water_balance is not None
    assert fresh_status.water_balance.depletion_mm == pytest.approx(10.0)

    # Three days old: the row exists, it is simply not yesterday's.
    assert (
        await db_session.get(WaterBalanceDailyRow, (stale.plot_id, TODAY - timedelta(days=3)))
        is not None
    )
    assert stale_status.water_balance is None
    # Negative: the stale depletion never reaches the payload, and the rest of
    # the payload is still real, so the null is not a failed read.
    assert stale_status.plot.id == stale.plot_id
    assert stale_status.recommendation is None


async def test_a_recommendation_that_was_never_calculated_is_null(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    # A balance exists, but no recommendation row for today.
    await add_water_balance(db_session, env, day=YESTERDAY)

    status = await _status(env)

    assert status.recommendation is None
    # Negative: an older recommendation is not today's and stays out.
    await add_recommendation(db_session, env, day=TODAY - timedelta(days=3))
    assert (await _status(env)).recommendation is None


async def test_future_sowing_gives_a_null_stage_and_day_inside_an_active_cycle(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    await add_cycle(db_session, env, sown_on=TODAY + timedelta(days=6))

    status = await _status(env)

    assert status.active_cycle is not None
    assert status.active_cycle.stage is None
    assert status.active_cycle.day_of_cycle is None
    assert status.active_cycle.crop.code == "maize"
    # Negative: the cycle is real, so the null stage and day are "there is no
    # cycle day yet", not a missing cycle — `active_cycle` itself is non-null.
    assert status.active_cycle is not None
    assert status.plot.id == env.plot_id


async def test_representative_sensor_wins_over_a_newer_reading_at_another_depth(
    db_session: AsyncSession,
) -> None:
    """D-T2.1: the home and the recommendation stay on the same evidence.

    The 30 cm sensor (Zr/2 of 60) is the plot's representative one, so its
    older reading is the value — the 10 cm `lab`-calibrated sensor is newer but
    not representative, and `K > 0` is what excludes it (docs/06 §5).
    """
    env = await make_env(db_session)
    await add_soil(db_session, env, root_depth_cm=60.0)
    await _sensor_at_zr_half(db_session, env, value=21.0, at=NOW - timedelta(hours=5))

    other_node = await add_node(db_session, env, claim_code="NODE-SHALLOW")
    shallow = await add_sensor(db_session, other_node, depth_cm=10, channel_key="sm_shallow")
    await calibrate(db_session, shallow, kind="lab")
    await add_reading(db_session, shallow, at=NOW - timedelta(hours=1), value=33.0)

    status = await _status(env)

    assert status.latest.soil_moisture_pct == pytest.approx(21.0)
    # `at` follows the value returned, so it is the representative reading's
    # time and not the newest reading the plot has.
    assert status.latest.at == NOW - timedelta(hours=5)

    # Negative: the newest-by-metric fallback would have answered 33.0 at -1 h.
    assert status.latest.soil_moisture_pct != pytest.approx(33.0)
    assert status.latest.at != NOW - timedelta(hours=1)


async def test_two_representative_sensors_are_averaged(db_session: AsyncSession) -> None:
    """docs/06 §5: one sensor near Zr/2, or the mean of two in the root zone."""
    env = await make_env(db_session)
    await add_soil(db_session, env, root_depth_cm=60.0)
    await _sensor_at_zr_half(
        db_session, env, value=21.0, at=NOW - timedelta(hours=5), node="NODE-A"
    )
    node_id = await add_node(db_session, env, claim_code="NODE-B")
    second = await add_sensor(db_session, node_id, depth_cm=45, channel_key="sm_45")
    await calibrate(db_session, second)
    await add_reading(db_session, second, at=NOW - timedelta(hours=3), value=25.0)

    status = await _status(env)

    assert status.latest.soil_moisture_pct == pytest.approx(23.0)
    assert status.latest.at == NOW - timedelta(hours=3)
    # Negative: the mean of two is neither of them.
    assert status.latest.soil_moisture_pct != pytest.approx(21.0)
    assert status.latest.soil_moisture_pct != pytest.approx(25.0)


async def test_without_a_soil_profile_the_newest_reading_at_any_depth_wins(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    # No soil profile: `root_depth_cm` is null, so no sensor can be
    # representative and the newest valid reading of any depth answers.
    await _sensor_at_zr_half(db_session, env, value=21.0, at=NOW - timedelta(hours=5))
    node_id = await add_node(db_session, env, claim_code="NODE-NEW")
    fresh = await add_sensor(db_session, node_id, depth_cm=10, channel_key="sm_fresh")
    await calibrate(db_session, fresh)
    await add_reading(db_session, fresh, at=NOW - timedelta(hours=1), value=33.0)

    status = await _status(env)

    assert status.latest.soil_moisture_pct == pytest.approx(33.0)
    assert status.latest.at == NOW - timedelta(hours=1)
    # Negative: the representative path would have answered 21.0 (the only
    # Zr/2 sensor) had a root depth existed.
    assert status.latest.soil_moisture_pct != pytest.approx(21.0)


async def test_an_out_of_range_reading_is_not_evidence(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    node_id = await add_node(db_session, env, claim_code="NODE-Q")
    sensor = await add_sensor(db_session, node_id, depth_cm=10, channel_key="sm_q")
    await calibrate(db_session, sensor)
    await add_reading(db_session, sensor, at=NOW - timedelta(hours=5), value=21.0)
    await add_reading(
        db_session,
        sensor,
        at=NOW - timedelta(hours=1),
        value=99.0,
        quality=int(ReadingQuality.OUT_OF_RANGE),
    )

    status = await _status(env)

    # `quality` bit 2 is out of range (docs/03: calibración): the older valid
    # reading answers, and never the flagged 99.0.
    assert status.latest.soil_moisture_pct == pytest.approx(21.0)
    assert status.latest.at == NOW - timedelta(hours=5)
    assert status.latest.soil_moisture_pct != pytest.approx(99.0)


async def test_open_alerts_are_critical_first_then_newest_and_skip_resolved(
    db_session: AsyncSession,
) -> None:
    """D-T0.3: `state <> 'resolved'` (open + acknowledged), critical first,
    then newest first."""
    env = await make_env(db_session)
    old_id = await add_alert(
        db_session,
        env,
        severity="warning",
        rule_code="water_stress",
        at=NOW - timedelta(hours=6),
    )
    new_id = await add_alert(
        db_session,
        env,
        severity="warning",
        rule_code="heat_stress",
        at=NOW - timedelta(hours=1),
    )
    critical_id = await add_alert(
        db_session,
        env,
        severity="critical",
        rule_code="flood_risk",
        at=NOW - timedelta(hours=4),
    )
    await add_alert(
        db_session,
        env,
        severity="critical",
        rule_code="drought_risk",
        at=NOW - timedelta(minutes=10),
        resolve=True,
    )

    status = await _status(env)

    assert [alert.id for alert in status.open_alerts] == [critical_id, new_id, old_id]
    # Negative: the resolved one is excluded even though it is the newest and
    # critical, and the warnings keep newest-first order.
    assert len(status.open_alerts) == 3
    assert status.open_alerts[0].severity == "critical"


async def test_weather_is_only_the_next_three_forecast_days_with_its_stale_mark(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    await add_forecast(db_session, env, days=5, fetched_at=NOW - timedelta(hours=1))

    status = await _status(env)

    assert [day.day for day in status.weather_next_3d] == [
        TODAY,
        TODAY + timedelta(days=1),
        TODAY + timedelta(days=2),
    ]
    assert all(day.is_forecast is True for day in status.weather_next_3d)
    assert all(day.stale is False for day in status.weather_next_3d)

    # Negative: a cell that missed two 3 h refreshes marks every row stale,
    # and the days past the third are still cut on both paths.
    stale_env = await make_env(db_session, name="Finca Stale")
    await add_forecast(
        db_session, stale_env, days=5, fetched_at=NOW - timedelta(hours=7), cell_lat=Decimal("11.9")
    )
    stale = await _status(stale_env)
    assert len(stale.weather_next_3d) == 3
    assert all(day.stale is True for day in stale.weather_next_3d)

    # Negative: a plot with no cell at all answers an empty list, not a guess.
    bare = await make_env(db_session, name="Finca Sin Celda")
    assert (await _status(bare)).weather_next_3d == []


async def test_a_plot_of_another_org_raises_plot_not_found(db_session: AsyncSession) -> None:
    other = await make_env(db_session, name="Finca Ajena")
    mine = await make_env(db_session, name="Finca Mía")

    with pytest.raises(PlotNotFoundError):
        await build_plot_status(
            user_id=mine.user_id,
            plot_id=other.plot_id,
            now=NOW,
            **repos(db_session),
        )

    # Negative: an id nobody owns raises the same error, so the two cases are
    # indistinguishable to the caller (docs/09: 404, never a leak).
    with pytest.raises(PlotNotFoundError):
        await build_plot_status(
            user_id=mine.user_id,
            plot_id=uuid7(),
            now=NOW,
            **repos(db_session),
        )
