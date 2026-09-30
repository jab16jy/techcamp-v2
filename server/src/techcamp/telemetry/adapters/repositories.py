"""Postgres repositories for telemetry (docs/09-cuellos-de-botella.md#seguridad).

`sensor` and `calibration` carry no `org_id` column, so their queries join
through `node` to filter by it, same pattern as farms' `crop_cycle`/
`soil_profile` (`server/src/techcamp/farms/adapters/repositories.py`). T3
adds `telemetry/application/ports.py` (the layering-rule abstraction farms'
own T1 docstring anticipated) now that write use cases depend on this
module's repository behavior.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import ColumnElement, CursorResult, Row, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.jobs import enqueue_recalibration
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.domain.errors import CalibrationVersionConflictError
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    Node,
    NodeSeenUpdate,
    NodeStatus,
    NodeStatusEvent,
    NodeTransport,
    ReadingEvent,
    ReadingPoint,
    ReadingQuality,
    ReadingRecord,
    Sensor,
)


def _node_from_row(row: Row[Any]) -> Node:
    return Node(
        id=row.id,
        org_id=row.org_id,
        plot_id=row.plot_id,
        transport=NodeTransport(row.transport),
        dev_eui=row.dev_eui,
        claim_code=row.claim_code,
        credential_hash=row.credential_hash,
        firmware=row.firmware,
        interval_s=row.interval_s,
        claimed_at=row.claimed_at,
        last_seen_at=row.last_seen_at,
        status=NodeStatus(row.status),
    )


def _sensor_from_row(row: Row[Any]) -> Sensor:
    return Sensor(
        id=row.id,
        node_id=row.node_id,
        channel_key=row.channel_key,
        metric=row.metric,
        depth_cm=row.depth_cm,
        unit=row.unit,
    )


_CALIBRATION_VERSION_CONSTRAINT = "uq_calibration_sensor_version"
"""The one constraint whose violation is a lost version race, not a bug
(docs/03-modelo-datos.md:461: calibration is versioned and never edited in
place)."""


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint asyncpg reported for `exc`, or `None` when it named
    none (a not-null or foreign-key failure carries no constraint name).

    The dialect's translated error keeps only `sqlstate`/`pgcode` and chains
    the driver error as `__cause__` (`_handle_exception` in
    `sqlalchemy/dialects/postgresql/asyncpg.py`), so the asyncpg attributes
    are read from there, not from `exc.orig` itself.
    """
    driver_error = getattr(exc.orig, "__cause__", None)
    return cast("str | None", getattr(driver_error, "constraint_name", None))


def _calibration_from_row(row: Row[Any]) -> Calibration:
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


_NODE_COLUMNS = (
    NodeRow.id,
    NodeRow.org_id,
    NodeRow.plot_id,
    NodeRow.transport,
    NodeRow.dev_eui,
    NodeRow.claim_code,
    NodeRow.credential_hash,
    NodeRow.firmware,
    NodeRow.interval_s,
    NodeRow.claimed_at,
    NodeRow.last_seen_at,
    NodeRow.status,
)


class SqlAlchemyNodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, node_id: UUID, org_id: UUID) -> Node | None:
        result = await self._session.execute(
            select(*_NODE_COLUMNS).where(NodeRow.id == node_id, NodeRow.org_id == org_id)
        )
        row = result.one_or_none()
        return _node_from_row(row) if row is not None else None

    async def get_by_claim_code(self, claim_code: str) -> Node | None:
        """Looked up before the caller's org is known (claim, docs/04-api.md):
        `claim_code` is globally unique, so no `org_id` filter applies here."""
        result = await self._session.execute(
            select(*_NODE_COLUMNS).where(NodeRow.claim_code == claim_code)
        )
        row = result.one_or_none()
        return _node_from_row(row) if row is not None else None

    async def get_by_id(self, node_id: UUID) -> Node | None:
        """The ingestor knows only `node_id` from the MQTT topic (T4), before
        it knows which org — same before-org-is-known reasoning as
        `get_by_claim_code`."""
        result = await self._session.execute(select(*_NODE_COLUMNS).where(NodeRow.id == node_id))
        row = result.one_or_none()
        return _node_from_row(row) if row is not None else None

    async def get_for_orgs(self, node_id: UUID, org_ids: Sequence[UUID]) -> Node | None:
        """Look up a node across every org the caller belongs to (a node-id-
        only route has no `org_id` in the path, docs/09-cuellos-de-
        botella.md#seguridad). `NodeRow.org_id.in_(org_ids)` never matches an
        unclaimed node: `org_id` is `NULL` there, and SQL `IN` never matches
        `NULL`."""
        if not org_ids:
            return None
        result = await self._session.execute(
            select(*_NODE_COLUMNS).where(NodeRow.id == node_id, NodeRow.org_id.in_(org_ids))
        )
        row = result.one_or_none()
        return _node_from_row(row) if row is not None else None

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        plot_id: UUID | None = None,
        status: NodeStatus | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[Node]:
        """Cursor page ordered by `id` (uuid7 is time-ordered, docs/04-api.md
        pagination convention, same as `SqlAlchemyFarmRepository.list_for_org`)."""
        stmt = select(*_NODE_COLUMNS).where(NodeRow.org_id == org_id)
        if plot_id is not None:
            stmt = stmt.where(NodeRow.plot_id == plot_id)
        if status is not None:
            stmt = stmt.where(NodeRow.status == status.value)
        if cursor is not None:
            stmt = stmt.where(NodeRow.id > cursor)
        stmt = stmt.order_by(NodeRow.id).limit(limit)
        result = await self._session.execute(stmt)
        return [_node_from_row(row) for row in result]

    async def claim(
        self,
        node_id: UUID,
        *,
        org_id: UUID,
        plot_id: UUID,
        credential_hash: str,
        claimed_at: datetime,
    ) -> Node | None:
        """Assigns org/plot/credentials to a still-unclaimed node
        (docs/06-diseno-detallado.md §2). `None` when the node was claimed by
        a concurrent request between the caller's check and this update: the
        `org_id IS NULL` guard mirrors `ck_node_ownership_all_or_nothing`."""
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(NodeRow)
                .where(NodeRow.id == node_id, NodeRow.org_id.is_(None))
                .values(
                    org_id=org_id,
                    plot_id=plot_id,
                    credential_hash=credential_hash,
                    claimed_at=claimed_at,
                )
            ),
        )
        if result.rowcount == 0:
            await self._session.rollback()
            return None
        await self._session.commit()
        return await self.get(node_id, org_id)

    async def update(
        self, node_id: UUID, org_id: UUID, *, plot_id: UUID | None, status: NodeStatus
    ) -> Node:
        await self._session.execute(
            update(NodeRow)
            .where(NodeRow.id == node_id, NodeRow.org_id == org_id)
            .values(plot_id=plot_id, status=status.value)
        )
        await self._session.commit()
        node = await self.get(node_id, org_id)
        assert node is not None
        return node

    async def set_credential_hash(self, node_id: UUID, org_id: UUID, credential_hash: str) -> None:
        await self._session.execute(
            update(NodeRow)
            .where(NodeRow.id == node_id, NodeRow.org_id == org_id)
            .values(credential_hash=credential_hash)
        )
        await self._session.commit()

    async def count_readings_since(self, node_id: UUID, org_id: UUID, since: datetime) -> int:
        """Distinct uplink timestamps in the window, not raw row count: every
        sensor on the node shares one `time` per uplink (docs/11-
        metricas.md: `lecturas recibidas / esperadas por nodo y día`)."""
        result = await self._session.execute(
            select(func.count(func.distinct(ReadingRow.time)))
            .select_from(ReadingRow)
            .join(SensorRow, SensorRow.id == ReadingRow.sensor_id)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(NodeRow.id == node_id, NodeRow.org_id == org_id, ReadingRow.time >= since)
        )
        return result.scalar_one()

    async def mark_seen_batch(self, updates: Sequence[NodeSeenUpdate]) -> set[UUID]:
        """One `UPDATE` per node touched by an ingest flush (T4), one commit
        for the whole batch (docs/06-diseno-detallado.md §1).

        `last_seen_at` only moves forward: the uplink and status batchers
        flush independently, so an uplink received before a Last Will
        `offline` can reach this method after it. Without the guard the node
        would flip back to `online` with an older timestamp (#36).

        Returns the nodes actually written, so the caller publishes a
        `node.status` event only for a change that landed."""
        updated: set[UUID] = set()
        for u in updates:
            result = await self._session.execute(
                update(NodeRow)
                .where(
                    NodeRow.id == u.node_id,
                    NodeRow.org_id == u.org_id,
                    or_(NodeRow.last_seen_at.is_(None), NodeRow.last_seen_at < u.last_seen_at),
                )
                .values(last_seen_at=u.last_seen_at, status=u.status.value)
            )
            if cast(CursorResult[Any], result).rowcount:
                updated.add(u.node_id)
        await self._session.commit()
        return updated


_SENSOR_COLUMNS = (
    SensorRow.id,
    SensorRow.node_id,
    SensorRow.channel_key,
    SensorRow.metric,
    SensorRow.depth_cm,
    SensorRow.unit,
)


class SqlAlchemySensorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_node(self, node_id: UUID, org_id: UUID) -> list[Sensor]:
        stmt = (
            select(*_SENSOR_COLUMNS)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(SensorRow.node_id == node_id, NodeRow.org_id == org_id)
            .order_by(SensorRow.id)
        )
        result = await self._session.execute(stmt)
        return [_sensor_from_row(row) for row in result]

    async def get_org_id(self, sensor_id: int) -> UUID | None:
        """`sensor` carries no `org_id` column, so this joins through `node`
        (see the module docstring); `None` when `sensor_id` doesn't exist."""
        result = await self._session.execute(
            select(NodeRow.org_id)
            .select_from(SensorRow)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(SensorRow.id == sensor_id)
        )
        return result.scalar_one_or_none()


_CALIBRATION_COLUMNS = (
    CalibrationRow.id,
    CalibrationRow.sensor_id,
    CalibrationRow.version,
    CalibrationRow.method,
    CalibrationRow.kind,
    CalibrationRow.params,
    CalibrationRow.rmse_pct,
    CalibrationRow.valid_from,
)


class SqlAlchemyCalibrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_valid_at(
        self, sensor_id: int, org_id: UUID, at: datetime
    ) -> Calibration | None:
        """The calibration in effect at `at` (docs/03-modelo-datos.md:461):
        the highest `valid_from` that is still `<= at`."""
        stmt = (
            select(*_CALIBRATION_COLUMNS)
            .join(SensorRow, SensorRow.id == CalibrationRow.sensor_id)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(
                CalibrationRow.sensor_id == sensor_id,
                NodeRow.org_id == org_id,
                CalibrationRow.valid_from <= at,
            )
            .order_by(CalibrationRow.valid_from.desc(), CalibrationRow.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        return _calibration_from_row(row) if row is not None else None

    async def next_version(self, sensor_id: int, org_id: UUID) -> int:
        """`MAX(version) + 1`, scoped by org through the sensor→node join
        (docs/03-modelo-datos.md:461: never edited in place, always a new
        version). `1` for a sensor's first calibration."""
        result = await self._session.execute(
            select(func.max(CalibrationRow.version))
            .select_from(CalibrationRow)
            .join(SensorRow, SensorRow.id == CalibrationRow.sensor_id)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(CalibrationRow.sensor_id == sensor_id, NodeRow.org_id == org_id)
        )
        highest = result.scalar_one_or_none()
        return 1 if highest is None else highest + 1

    async def add_version(
        self,
        *,
        org_id: UUID,
        sensor_id: int,
        version: int,
        method: CalibrationMethod,
        kind: CalibrationKind,
        params: dict[str, Any],
        rmse_pct: float | None,
        valid_from: datetime,
    ) -> Calibration | None:
        """Calibration is versioned and never edited in place
        (docs/03-modelo-datos.md:461): always an insert, never an update.
        `None` when `sensor_id` doesn't belong to `org_id` (same convention
        as the read methods above), joined the same way as
        `get_latest_valid_at`."""
        owned = await self._session.execute(
            select(SensorRow.id)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(SensorRow.id == sensor_id, NodeRow.org_id == org_id)
        )
        if owned.scalar_one_or_none() is None:
            return None
        calibration_id = uuid7()
        self._session.add(
            CalibrationRow(
                id=calibration_id,
                sensor_id=sensor_id,
                version=version,
                method=method.value,
                kind=kind.value,
                params=params,
                rmse_pct=rmse_pct,
                valid_from=valid_from,
            )
        )
        # T7 (docs/03-modelo-datos.md:461, ADR-0012): recalibrating always
        # enqueues the job that recomputes `value`/`quality` from
        # `valid_from`, including a sensor's very first calibration (it may
        # retroactively calibrate readings ingested before it existed). Same
        # transaction as the version insert above, same commit below. The job
        # is locked per sensor so two versions of it never run at once.
        await enqueue_recalibration(self._session, calibration_id, sensor_id)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # `MAX(version) + 1` and this insert are separate statements
            # (add_calibration), so two concurrent POSTs can pick the same
            # version. That unique violation is the only retryable conflict:
            # the `procrastinate_jobs` insert above shares this commit, and
            # reporting one of its failures as a 409 would send the client
            # into a futile retry instead of surfacing a server error (#61).
            await self._session.rollback()
            if _violated_constraint(exc) != _CALIBRATION_VERSION_CONSTRAINT:
                raise
            raise CalibrationVersionConflictError(sensor_id) from exc
        result = await self._session.execute(
            select(*_CALIBRATION_COLUMNS).where(CalibrationRow.id == calibration_id)
        )
        return _calibration_from_row(result.one())


class SqlAlchemyReadingRepository:
    """T4: `reading` has no domain model of its own beyond `ReadingRecord`
    (see `telemetry/domain/models.py`'s module docstring), only this batched
    insert."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_batch(self, records: Sequence[ReadingRecord]) -> set[tuple[int, datetime]]:
        """`INSERT ... ON CONFLICT (time, sensor_id) DO NOTHING`
        (docs/06-diseno-detallado.md §1): idempotent under QoS-1 redelivery,
        `reading`'s composite primary key is the conflict target. `RETURNING`
        reports only the rows that were actually inserted, which is what the
        caller needs to skip `reading` events for skipped rows."""
        if not records:
            return set()
        stmt = (
            pg_insert(ReadingRow)
            .values(
                [
                    {
                        "time": r.time,
                        "sensor_id": r.sensor_id,
                        "raw_value": r.raw_value,
                        "value": r.value,
                        "received_at": r.received_at,
                        "quality": int(r.quality),
                    }
                    for r in records
                ]
            )
            .on_conflict_do_nothing(index_elements=["time", "sensor_id"])
            .returning(ReadingRow.sensor_id, ReadingRow.time)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return {(row.sensor_id, row.time) for row in result}

    async def _raw(
        self,
        sensor_id: int,
        *,
        start: datetime,
        end: datetime,
        org_id: UUID | None = None,
        extra: Sequence[ColumnElement[bool]] = (),
    ) -> list[ReadingPoint]:
        """`reading` rows in `[start, end)` with a calibrated `value`, ordered by
        time, narrowed by `extra` (the two public reads below differ only in the
        quality flags they accept).

        `org_id` adds the `sensor`->`node` join that scopes the read to one org
        (docs/09-cuellos-de-botella.md:47: every repository filters by `org_id`;
        neither `reading` nor `sensor` carries the column itself). It is a
        parameter of `_raw`, not a separate query, so the two reads cannot drift
        apart on the window or the `value IS NULL` filter."""
        query = select(ReadingRow.time, ReadingRow.value)
        conditions: list[ColumnElement[bool]] = [
            ReadingRow.sensor_id == sensor_id,
            ReadingRow.time >= start,
            ReadingRow.time < end,
            ReadingRow.value.is_not(None),
            *extra,
        ]
        if org_id is not None:
            query = query.join(SensorRow, SensorRow.id == ReadingRow.sensor_id).join(
                NodeRow, NodeRow.id == SensorRow.node_id
            )
            conditions.append(NodeRow.org_id == org_id)
        result = await self._session.execute(query.where(*conditions).order_by(ReadingRow.time))
        return [ReadingPoint(time=row.time, value=row.value) for row in result]

    async def query_raw(
        self, sensor_id: int, *, start: datetime, end: datetime
    ) -> list[ReadingPoint]:
        return await self._raw(sensor_id, start=start, end=end)

    async def query_valid_raw(
        self, sensor_id: int, org_id: UUID, *, start: datetime, end: datetime
    ) -> list[ReadingPoint]:
        """`query_raw` without the out-of-range readings (docs/06 §1: a value
        outside the physical range "no dispara alertas"), scoped to `org_id`.

        Only bit 2 of `quality` is filtered: a `ts` corrected by `received_at`
        (bit 1) is still evidence a sustained run may build on, so the flags are
        read one by one as docs/06 §1 keeps them.
        """
        return await self._raw(
            sensor_id,
            start=start,
            end=end,
            org_id=org_id,
            extra=(ReadingRow.quality.bitwise_and(int(ReadingQuality.OUT_OF_RANGE)) == 0,),
        )

    async def _query_aggregate(
        self, view: str, sensor_id: int, *, start: datetime, end: datetime
    ) -> list[ReadingPoint]:
        """`view` is always one of the two continuous-aggregate names below,
        never caller input (docs/03-modelo-datos.md:372: `reading_hourly`/
        `reading_daily`), so interpolating it into the query text carries no
        injection risk; neither view is mapped as an ORM `Base` subclass
        (`adapters/orm.py`'s module docstring), so `text()` is the plain way
        to read them."""
        result = await self._session.execute(
            text(
                f"SELECT bucket, avg_value FROM {view} "
                "WHERE sensor_id = :sensor_id AND bucket >= :start AND bucket < :end "
                "AND reading_count > 0 ORDER BY bucket"
            ),
            {"sensor_id": sensor_id, "start": start, "end": end},
        )
        return [ReadingPoint(time=row.bucket, value=row.avg_value) for row in result]

    async def query_hourly(
        self, sensor_id: int, *, start: datetime, end: datetime
    ) -> list[ReadingPoint]:
        return await self._query_aggregate("reading_hourly", sensor_id, start=start, end=end)

    async def query_daily(
        self, sensor_id: int, *, start: datetime, end: datetime
    ) -> list[ReadingPoint]:
        return await self._query_aggregate("reading_daily", sensor_id, start=start, end=end)

    async def query_latest_valid_by_metric(
        self,
        plot_id: UUID,
        org_id: UUID,
        *,
        metrics: Sequence[str],
        now: datetime,
    ) -> dict[str, ReadingPoint]:
        if not metrics:
            return {}
        start = now - timedelta(hours=24)
        stmt = (
            select(SensorRow.metric, ReadingRow.time, ReadingRow.value)
            .distinct(SensorRow.metric)
            .join(SensorRow, SensorRow.id == ReadingRow.sensor_id)
            .join(NodeRow, NodeRow.id == SensorRow.node_id)
            .where(
                NodeRow.plot_id == plot_id,
                NodeRow.org_id == org_id,
                SensorRow.metric.in_(metrics),
                ReadingRow.time >= start,
                ReadingRow.time <= now,
                ReadingRow.value.is_not(None),
                ReadingRow.quality.bitwise_and(int(ReadingQuality.OUT_OF_RANGE)) == 0,
            )
            .order_by(SensorRow.metric, ReadingRow.time.desc(), SensorRow.id)
        )
        result = await self._session.execute(stmt)
        return {row.metric: ReadingPoint(time=row.time, value=row.value) for row in result}


class SqlAlchemyPlotEventsNotifier:
    """`NOTIFY plot_events` for T6's SSE fan-out (docs/04-api.md:180-189,
    ADR-0015). `pg_notify()` (not a literal `NOTIFY channel, 'payload'`)
    because the payload is dynamic and needs bind-parameter escaping."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def publish(
        self, *, readings: Sequence[ReadingEvent], statuses: Sequence[NodeStatusEvent]
    ) -> None:
        if not readings and not statuses:
            return
        for reading in readings:
            payload = json.dumps(
                {
                    "type": "reading",
                    "org_id": str(reading.org_id),
                    "farm_id": str(reading.farm_id),
                    "plot_id": str(reading.plot_id),
                    "metric": reading.metric,
                    "value": reading.value,
                    "at": reading.at.isoformat(),
                }
            )
            await self._session.execute(
                text("SELECT pg_notify('plot_events', :payload)"), {"payload": payload}
            )
        for status in statuses:
            payload = json.dumps(
                {
                    "type": "node.status",
                    "org_id": str(status.org_id),
                    "farm_id": str(status.farm_id),
                    "node_id": str(status.node_id),
                    "status": status.status.value,
                    "at": status.at.isoformat(),
                }
            )
            await self._session.execute(
                text("SELECT pg_notify('plot_events', :payload)"), {"payload": payload}
            )
        await self._session.commit()
