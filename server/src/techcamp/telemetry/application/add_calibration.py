"""`POST /sensors/{sensor_id}/calibrations` (docs/04-api.md:88): validates
params with T2's pure domain function and inserts the next version.

The insert is a separate statement from the `MAX(version) + 1` read, so a
concurrent POST can claim the same version: `CalibrationVersionConflictError`
(both versions rolled back) is the router's `409`, the client retries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7
from techcamp.telemetry.application.ports import CalibrationRepository, SensorRepository
from techcamp.telemetry.domain.errors import SensorNotFoundError
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    ensure_can_write,
    validate_calibration_params,
)


async def _resolve_sensor_org(
    *, user_id: UUID, sensor_id: int, sensors: SensorRepository, memberships: MembershipRepository
) -> tuple[UUID, Role]:
    org_id = await sensors.get_org_id(sensor_id)
    if org_id is None:
        raise SensorNotFoundError(sensor_id)
    membership = await memberships.get(user_id, org_id)
    if membership is None:
        # The sensor's own org exists, but the caller isn't a member of it —
        # 404, never 403 (docs/09-cuellos-de-botella.md#seguridad).
        raise SensorNotFoundError(sensor_id)
    return org_id, membership.role


async def add_calibration(
    *,
    user_id: UUID,
    sensor_id: int,
    method: CalibrationMethod,
    kind: CalibrationKind,
    params: dict[str, Any],
    rmse_pct: float | None,
    valid_from: datetime,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    memberships: MembershipRepository,
) -> Calibration:
    org_id, role = await _resolve_sensor_org(
        user_id=user_id, sensor_id=sensor_id, sensors=sensors, memberships=memberships
    )
    ensure_can_write(role)
    version = await calibrations.next_version(sensor_id, org_id)
    candidate = Calibration(
        id=uuid7(),
        sensor_id=sensor_id,
        version=version,
        method=method,
        kind=kind,
        params=params,
        rmse_pct=rmse_pct,
        valid_from=valid_from,
    )
    # T2's pure domain function (`InvalidCalibrationParamsError` -> 422 in the
    # router), checked per method: the params are stored as they arrive, so a
    # coefficient the method can't use has to be rejected before the insert.
    validate_calibration_params(candidate)
    created = await calibrations.add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=version,
        method=method,
        kind=kind,
        params=params,
        rmse_pct=rmse_pct,
        valid_from=valid_from,
    )
    if created is None:
        # `add_version` re-checks the sensor's org in its own statement; losing
        # that means the sensor left the caller's org between the two reads.
        raise SensorNotFoundError(sensor_id)
    return created
