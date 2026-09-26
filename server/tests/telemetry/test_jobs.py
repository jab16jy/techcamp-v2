"""Recalibration job (T7, docs/03-modelo-datos.md:461, ADR-0012).

`reading_hourly`/`reading_daily` refresh reasoning: see
`test_reading_repository.py`'s module docstring (`CALL
refresh_continuous_aggregate(...)` needs an AUTOCOMMIT connection).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.telemetry.adapters.jobs import recalibrate_readings
from techcamp.telemetry.adapters.orm import SensorRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyCalibrationRepository
from techcamp.telemetry.domain.models import CalibrationKind, CalibrationMethod

from .test_reading_repository import _insert_reading, _refresh_aggregate
from .test_repositories import _make_node, _make_org_and_plot, _make_sensor

pytestmark = pytest.mark.anyio


async def _make_percentage_sensor(
    db_session: AsyncSession, node_id: object, *, channel_key: str = "sm_pct"
) -> int:
    """Same as `_make_sensor`, but with `unit="%"` — `classify_reading_range`
    (T2) only has a documented range for that unit."""
    sensor = SensorRow(
        node_id=node_id, channel_key=channel_key, metric="soil_moisture", depth_cm=10, unit="%"
    )
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return sensor.id


@pytest.fixture(autouse=True)
async def _clear_continuous_aggregates(db_session: AsyncSession):
    """`reading_hourly`/`reading_daily` are materialized (docs/03-modelo-
    datos.md:372) and TimescaleDB's own background policy can refresh them
    from committed `reading` rows independently of this file's explicit
    `_refresh_aggregate` calls (observed during development). `db_session`'s
    own cleanup only truncates `organization` and its FK-cascaded tables, not
    these materializations, so a bucket this file's tests touch can outlive
    them and leak into another test's aggregate query (e.g. by a later test
    reusing the same identity-reset `sensor_id`). Deleting every reading and
    re-refreshing after each test keeps that from happening."""
    yield
    await db_session.execute(text("DELETE FROM reading"))
    await db_session.commit()
    await _refresh_aggregate("reading_hourly")
    await _refresh_aggregate("reading_daily")


async def _reading_state(db_session: AsyncSession, sensor_id: int, at: datetime) -> tuple:
    row = (
        await db_session.execute(
            text("SELECT value, quality FROM reading WHERE sensor_id = :sid AND time = :t"),
            {"sid": sensor_id, "t": at},
        )
    ).one()
    return row.value, row.quality


async def _aggregate_avg(db_session: AsyncSession, view: str, sensor_id: int, at: datetime):
    bucket = timedelta(hours=1) if view == "reading_hourly" else timedelta(days=1)
    return (
        await db_session.execute(
            text(
                f"SELECT avg_value FROM {view} WHERE sensor_id = :sid "
                "AND bucket = time_bucket(CAST(:bucket AS interval), CAST(:t AS timestamptz))"
            ),
            {"sid": sensor_id, "bucket": bucket, "t": at},
        )
    ).scalar_one()


async def test_recalibrate_readings_succeeds_on_a_sub_bucket_window_and_refreshes_both_views(
    db_session: AsyncSession,
) -> None:
    """D5: a non-latest version is bounded by the *next* version's
    `valid_from`, so a back-dated calibration can own a window shorter than one
    bucket. TimescaleDB rejected `refresh_continuous_aggregate` for it — but
    only after the recompute had already committed, so procrastinate recorded
    the job as `failed` while the readings were rewritten. Each view must be
    refreshed over its own whole buckets, and the aggregates are asserted so a
    fix that merely swallows the refresh error cannot pass."""
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_percentage_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    # Two versions 12 minutes apart, so v2's window is [12:30, 12:42): shorter
    # than the 1-hour and the 1-day bucket of the two views.
    target_valid_from = datetime(2026, 3, 1, 12, 30, tzinfo=UTC)
    target_id = None
    for version, valid_from, scale in (
        (1, datetime(2026, 2, 1, tzinfo=UTC), 1.0),
        (2, target_valid_from, 5.0),
        (3, datetime(2026, 3, 1, 12, 42, tzinfo=UTC), 1.0),
    ):
        created = await repo.add_version(
            org_id=org_id,
            sensor_id=sensor_id,
            version=version,
            method=CalibrationMethod.LINEAR,
            kind=CalibrationKind.FIELD,
            params={"scale": scale, "offset": 0.0},
            rmse_pct=None,
            valid_from=valid_from,
        )
        assert created is not None
        if version == 2:
            target_id = created.id

    at = datetime(2026, 3, 1, 12, 35, tzinfo=UTC)
    await _insert_reading(db_session, sensor_id, at=at, raw_value=10.0, value=10.0, quality=0)
    await _refresh_aggregate("reading_hourly")
    await _refresh_aggregate("reading_daily")
    assert await _aggregate_avg(db_session, "reading_hourly", sensor_id, at) == 10.0

    # Raises nothing: this is what procrastinate records as `succeeded`.
    await recalibrate_readings(calibration_id=str(target_id))

    # The committed recompute, and both aggregates agreeing with it.
    assert await _reading_state(db_session, sensor_id, at) == (50.0, 0)
    assert await _aggregate_avg(db_session, "reading_hourly", sensor_id, at) == 50.0
    assert await _aggregate_avg(db_session, "reading_daily", sensor_id, at) == 50.0


async def test_add_version_enqueues_the_recalibration_job(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    created = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert created is not None

    job = (
        await db_session.execute(
            text(
                "SELECT queue_name, task_name, args, status FROM procrastinate_jobs "
                "WHERE queueing_lock = :lock"
            ),
            {"lock": f"recalibrate:{created.id}"},
        )
    ).one()

    assert job.queue_name == "telemetry"
    assert job.task_name == "telemetry.recalibrate_readings"
    assert job.args == {"calibration_id": str(created.id)}
    assert job.status == "todo"


async def test_recalibrate_readings_recomputes_within_bounds_and_reclassifies_quality(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_percentage_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    v1 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert v1 is not None

    before_range = datetime(2026, 1, 15, tzinfo=UTC)
    await _insert_reading(
        db_session, sensor_id, at=before_range, raw_value=60.0, value=60.0, quality=0
    )

    v2 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=2,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 2.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 2, 1, tzinfo=UTC),
    )
    assert v2 is not None

    in_range_becomes_out = datetime(2026, 2, 10, tzinfo=UTC)
    await _insert_reading(
        db_session, sensor_id, at=in_range_becomes_out, raw_value=60.0, value=60.0, quality=0
    )
    stays_in_range = datetime(2026, 3, 1, tzinfo=UTC)
    await _insert_reading(
        db_session, sensor_id, at=stays_in_range, raw_value=30.0, value=30.0, quality=0
    )

    await recalibrate_readings(calibration_id=str(v2.id))

    # Before v2's valid_from: untouched (still v1's calibration in force).
    value, quality = await _reading_state(db_session, sensor_id, before_range)
    assert value == 60.0
    assert quality == 0

    # scale=2 on raw=60 -> 120, out of the documented [0, 100] % range.
    value, quality = await _reading_state(db_session, sensor_id, in_range_becomes_out)
    assert value == 120.0
    assert quality == 2

    # scale=2 on raw=30 -> 60, stays in range.
    value, quality = await _reading_state(db_session, sensor_id, stays_in_range)
    assert value == 60.0
    assert quality == 0


async def test_recalibrate_readings_is_idempotent(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_percentage_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    v1 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.5, "offset": 1.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert v1 is not None

    at = datetime(2026, 1, 10, tzinfo=UTC)
    await _insert_reading(db_session, sensor_id, at=at, raw_value=20.0, value=None, quality=1)

    await recalibrate_readings(calibration_id=str(v1.id))
    first_run = await _reading_state(db_session, sensor_id, at)

    await recalibrate_readings(calibration_id=str(v1.id))
    second_run = await _reading_state(db_session, sensor_id, at)

    assert first_run == second_run == (31.0, 1)


async def test_recalibrate_readings_does_not_touch_another_sensors_readings(
    db_session: AsyncSession,
) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, plot_b = await _make_org_and_plot(db_session, "Finca B")
    node_a = await _make_node(db_session, org_a, plot_a, claim_code="JOBS-A")
    node_b = await _make_node(db_session, org_b, plot_b, claim_code="JOBS-B")
    sensor_a = await _make_percentage_sensor(db_session, node_a, channel_key="sm_a")
    sensor_b = await _make_percentage_sensor(db_session, node_b, channel_key="sm_b")

    repo = SqlAlchemyCalibrationRepository(db_session)
    v_a = await repo.add_version(
        org_id=org_a,
        sensor_id=sensor_a,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 3.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert v_a is not None

    at = datetime(2026, 1, 10, tzinfo=UTC)
    await _insert_reading(db_session, sensor_a, at=at, raw_value=10.0, value=10.0, quality=0)
    await _insert_reading(db_session, sensor_b, at=at, raw_value=10.0, value=10.0, quality=0)

    await recalibrate_readings(calibration_id=str(v_a.id))

    value_a, _ = await _reading_state(db_session, sensor_a, at)
    value_b, _ = await _reading_state(db_session, sensor_b, at)
    assert value_a == 30.0
    assert value_b == 10.0  # untouched: a different sensor, different org


async def test_recalibrate_readings_refreshes_the_continuous_aggregates(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_percentage_sensor(db_session, node_id)
    repo = SqlAlchemyCalibrationRepository(db_session)

    v1 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.LAB,
        params={"scale": 1.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert v1 is not None
    at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    await _insert_reading(db_session, sensor_id, at=at, raw_value=10.0, value=10.0, quality=0)
    await _refresh_aggregate("reading_hourly")

    before = (
        await db_session.execute(
            text(
                "SELECT avg_value FROM reading_hourly WHERE sensor_id = :sid "
                "AND bucket = date_trunc('hour', CAST(:t AS timestamptz))"
            ),
            {"sid": sensor_id, "t": at},
        )
    ).scalar_one()
    assert before == 10.0

    v2 = await repo.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=2,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 5.0, "offset": 0.0},
        rmse_pct=None,
        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert v2 is not None

    await recalibrate_readings(calibration_id=str(v2.id))

    after = (
        await db_session.execute(
            text(
                "SELECT avg_value FROM reading_hourly WHERE sensor_id = :sid "
                "AND bucket = date_trunc('hour', CAST(:t AS timestamptz))"
            ),
            {"sid": sensor_id, "t": at},
        )
    ).scalar_one()
    assert after == 50.0
