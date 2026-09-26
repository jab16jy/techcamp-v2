"""Recalibration job (docs/03-modelo-datos.md:461, ADR-0012): recomputes
`value`/`quality` for a sensor's readings under a (re)calibration version,
then refreshes the continuous aggregates over the affected range. The task
is registered on the shared procrastinate `app` (`shared/jobs.py`) and run
by the `worker` process (`techcamp/worker.py`, docs/05-arquitectura.md:78-83,
:142: "adapters/jobs: tareas del worker").

`enqueue_recalibration` is a separate, SQLAlchemy-session-based helper, not
a call through this module's `app` — see `shared/jobs.py`'s module
docstring for why (asyncpg vs. psycopg). `SqlAlchemyCalibrationRepository
.add_version` (`adapters/repositories.py`) calls it right before its own
commit, so the enqueue is part of the same Postgres transaction as the new
calibration version (ADR-0012: "los jobs se encolan en la misma transacción
que los datos que los originan").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import Table, bindparam, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from techcamp.shared.db import async_session_factory, engine
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters.orm import CalibrationRow, ReadingRow, SensorRow
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    ReadingQuality,
    recalibrate_reading,
)

QUEUE_NAME = "telemetry"
TASK_NAME = "telemetry.recalibrate_readings"

# ponytail: one page per query, not a tuned number — this project's scale
# (docs/02-estimaciones.md) never puts millions of readings behind a single
# calibration window; revisit if a range this large shows up in practice.
_RECOMPUTE_BATCH_SIZE = 1000

_VIEW_BUCKETS = {
    "reading_hourly": timedelta(hours=1),
    "reading_daily": timedelta(days=1),
}
"""The `time_bucket` width of each continuous aggregate, as declared in
migration `8c3983dc2dfd`. TimescaleDB requires a refresh window of at least one
bucket, so each view is refreshed over whole buckets of its own width."""


async def enqueue_recalibration(session: AsyncSession, calibration_id: UUID) -> None:
    """Defers `recalibrate_readings` by calling procrastinate's own
    `procrastinate_defer_jobs_v1` SQL function over `session` directly
    (verified via ctx7 against procrastinate's `schema.sql`) instead of
    through the `app`'s `PsycopgConnector` — a different DBAPI driver from
    the asyncpg one `session` uses, so it can't share this transaction.
    `procrastinate_jobs`'s own triggers (job-logging, worker wake-up) are
    server-side and fire the same way regardless of which client inserted
    the row, so the worker picks this job up exactly as if procrastinate's
    own Python API had deferred it. `queueing_lock` keys the job identity to
    this calibration version (idempotent re-enqueue, ADR-0012)."""
    await session.execute(
        text(
            "SELECT procrastinate_defer_jobs_v1("
            "ARRAY[ROW(:queue_name, :task_name, :priority, :lock, :queueing_lock, :args, "
            "NULL::timestamptz)]::procrastinate_job_to_defer_v1[])"
        ),
        {
            "queue_name": QUEUE_NAME,
            "task_name": TASK_NAME,
            "priority": 0,
            "lock": None,
            "queueing_lock": f"recalibrate:{calibration_id}",
            "args": json.dumps({"calibration_id": str(calibration_id)}),
        },
    )


async def _load_calibration(session: AsyncSession, calibration_id: UUID) -> Calibration | None:
    # Mirrors `repositories.py`'s `_calibration_from_row`, duplicated rather
    # than imported: importing from `adapters/repositories.py` here would
    # create a cycle, since that module calls `enqueue_recalibration` above.
    result = await session.execute(
        select(
            CalibrationRow.id,
            CalibrationRow.sensor_id,
            CalibrationRow.version,
            CalibrationRow.method,
            CalibrationRow.kind,
            CalibrationRow.params,
            CalibrationRow.rmse_pct,
            CalibrationRow.valid_from,
        ).where(CalibrationRow.id == calibration_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    return Calibration(
        id=row.id,
        sensor_id=row.sensor_id,
        version=row.version,
        method=CalibrationMethod(row.method),
        kind=CalibrationKind(row.kind),
        params=row.params,
        rmse_pct=float(row.rmse_pct) if row.rmse_pct is not None else None,
        valid_from=row.valid_from,
    )


async def _sensor_unit(session: AsyncSession, sensor_id: int) -> str | None:
    result = await session.execute(select(SensorRow.unit).where(SensorRow.id == sensor_id))
    return result.scalar_one_or_none()


async def _next_valid_from(
    session: AsyncSession, sensor_id: int, valid_from: datetime
) -> datetime | None:
    """The next calibration version's `valid_from` after this one, if any —
    bounds the range this version is in force for (docs/03-modelo-
    datos.md:461). `None` when this is the sensor's most recent version."""
    result = await session.execute(
        select(CalibrationRow.valid_from)
        .where(CalibrationRow.sensor_id == sensor_id, CalibrationRow.valid_from > valid_from)
        .order_by(CalibrationRow.valid_from.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _recompute_range(
    session: AsyncSession, *, calibration: Calibration, unit: str, range_end: datetime | None
) -> bool:
    """Recomputes `value`/`quality` from `raw_value` for every reading of
    `calibration.sensor_id` in `[calibration.valid_from, range_end)`
    (`range_end=None` means open-ended), batched (bulk `UPDATE`, not
    row-by-row ORM). Idempotent: re-running over the same rows with the same
    calibration recomputes the same `(value, quality)` (`recalibrate_reading`
    is pure). Returns whether any reading was touched."""
    # `update(ReadingRow.__table__)` (Core, not the ORM entity): a plain
    # bulk `UPDATE ... WHERE` executed per batch via a list of param dicts,
    # not row-by-row ORM — `update(ReadingRow)` triggers SQLAlchemy's ORM
    # bulk-update-by-primary-key path instead, which rejects this shape.
    update_stmt = (
        update(cast(Table, ReadingRow.__table__))
        .where(ReadingRow.sensor_id == calibration.sensor_id, ReadingRow.time == bindparam("t"))
        .values(value=bindparam("v"), quality=bindparam("q"))
    )
    touched = False
    last_time: datetime | None = None
    while True:
        select_stmt = (
            select(ReadingRow.time, ReadingRow.raw_value, ReadingRow.quality)
            .where(
                ReadingRow.sensor_id == calibration.sensor_id,
                ReadingRow.time >= calibration.valid_from,
            )
            .order_by(ReadingRow.time)
            .limit(_RECOMPUTE_BATCH_SIZE)
        )
        if range_end is not None:
            select_stmt = select_stmt.where(ReadingRow.time < range_end)
        if last_time is not None:
            select_stmt = select_stmt.where(ReadingRow.time > last_time)
        rows = (await session.execute(select_stmt)).all()
        if not rows:
            break
        params = []
        for row in rows:
            value, quality = recalibrate_reading(
                calibration, row.raw_value, unit, ReadingQuality(row.quality)
            )
            params.append({"t": row.time, "v": value, "q": int(quality)})
        await session.execute(update_stmt, params)
        touched = True
        last_time = rows[-1].time
        if len(rows) < _RECOMPUTE_BATCH_SIZE:
            break
    return touched


async def _bucket_window(
    conn: AsyncConnection, start: datetime, end: datetime, bucket: timedelta
) -> tuple[datetime, datetime]:
    """Snaps `start` down and `end` up to whole `bucket` boundaries with the
    database's own `time_bucket`, so the alignment is the view's and cannot drift
    from it (the two are created in the same migration, `8c3983dc2dfd`)."""
    row = (
        await conn.execute(
            text(
                "SELECT time_bucket(CAST(:bucket AS interval), CAST(:start AS timestamptz)),"
                " time_bucket(CAST(:bucket AS interval), CAST(:end AS timestamptz))"
                " + CAST(:bucket AS interval)"
            ),
            {"bucket": bucket, "start": start, "end": end},
        )
    ).one()
    return row[0], row[1]


async def _refresh_aggregates(start: datetime, end: datetime | None) -> None:
    """`CALL refresh_continuous_aggregate(...)` cannot run inside a
    transaction block (verified via ctx7 against timescale/timescaledb's own
    docs, same reasoning as `tests/telemetry/test_reading_repository.py`),
    so this runs over an AUTOCOMMIT connection, separate from the recompute
    session's transaction.

    TimescaleDB rejects a refresh window shorter than one bucket of the view, and
    a non-latest calibration version's window is bounded by the *next* version's
    `valid_from` — so a back-dated version can own a window of minutes, far less
    than a day. Each view is therefore refreshed over its own whole buckets.
    Failing here used to land after the recompute had committed, so procrastinate
    recorded a `failed` job for work that was already durable."""
    window_end = end if end is not None else datetime.now(UTC)
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        for view, bucket in _VIEW_BUCKETS.items():
            window_start, window_end_aligned = await _bucket_window(conn, start, window_end, bucket)
            await conn.execute(
                text(
                    f"CALL refresh_continuous_aggregate("
                    f"'{view}', CAST(:start AS timestamptz), CAST(:end AS timestamptz))"
                ),
                {"start": window_start, "end": window_end_aligned},
            )


@app.task(name=TASK_NAME, queue=QUEUE_NAME)
async def recalibrate_readings(calibration_id: str) -> None:
    """The worker task deferred by `enqueue_recalibration`."""
    cal_uuid = UUID(calibration_id)
    async with async_session_factory() as session:
        calibration = await _load_calibration(session, cal_uuid)
        if calibration is None:
            return  # gone by the time the worker ran; nothing to recompute
        unit = await _sensor_unit(session, calibration.sensor_id)
        if unit is None:
            return  # sensor gone too
        range_end = await _next_valid_from(session, calibration.sensor_id, calibration.valid_from)
        touched = await _recompute_range(
            session, calibration=calibration, unit=unit, range_end=range_end
        )
        await session.commit()
    if touched:
        await _refresh_aggregates(calibration.valid_from, range_end)
