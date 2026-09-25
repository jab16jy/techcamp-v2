"""Postgres repositories for telemetry (docs/09-cuellos-de-botella.md#seguridad).

`sensor` and `calibration` carry no `org_id` column, so their queries join
through `node` to filter by it, same pattern as farms' `crop_cycle`/
`soil_profile` (`server/src/techcamp/farms/adapters/repositories.py`). No
application-layer port exists yet: T1 has no use case that depends on
repository behavior through an abstraction (ponytail: a port only for
external I/O or two real implementations, same reasoning as farms' T1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, SensorRow
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    Node,
    NodeStatus,
    NodeTransport,
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

    async def list_for_org(
        self, org_id: UUID, *, plot_id: UUID | None = None, status: NodeStatus | None = None
    ) -> list[Node]:
        stmt = select(*_NODE_COLUMNS).where(NodeRow.org_id == org_id)
        if plot_id is not None:
            stmt = stmt.where(NodeRow.plot_id == plot_id)
        if status is not None:
            stmt = stmt.where(NodeRow.status == status.value)
        stmt = stmt.order_by(NodeRow.id)
        result = await self._session.execute(stmt)
        return [_node_from_row(row) for row in result]


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
        try:
            await self._session.commit()
        except IntegrityError:
            # No domain error to map a duplicate (sensor_id, version) to yet
            # (no use case depends on this repository through a port, see
            # the module docstring); just keep the session usable for the
            # caller, same rollback-then-reraise shape as
            # `SqlAlchemyCropCycleRepository.create`.
            await self._session.rollback()
            raise
        result = await self._session.execute(
            select(*_CALIBRATION_COLUMNS).where(CalibrationRow.id == calibration_id)
        )
        return _calibration_from_row(result.one())
