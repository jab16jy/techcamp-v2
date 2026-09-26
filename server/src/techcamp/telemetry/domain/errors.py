"""Domain-level telemetry failures. Pure, no I/O."""

from __future__ import annotations

from typing import Any
from uuid import UUID


class InvalidCalibrationParamsError(Exception):
    """Raised when a `Calibration.params` shape doesn't match its `method`
    (docs/03-modelo-datos.md:463-467): a missing or non-numeric coefficient,
    or `raw_dry == raw_wet` (undefined slope for `two_point`).

    `method` is `CalibrationMethod`, typed as `str` (its base type) to avoid
    an import cycle with `domain.models`, which imports this module.
    """

    def __init__(self, method: str, detail: str) -> None:
        self.method = method
        self.detail = detail
        super().__init__(f"Invalid {method} calibration params: {detail}")


class UnsupportedUplinkVersionError(Exception):
    """Raised when an uplink payload's `v` isn't a version this server
    understands (docs/04-api.md:216): the ingestor discards the message and
    counts it, rather than guessing at an unknown schema."""

    def __init__(self, version: Any) -> None:
        self.version = version
        super().__init__(f"Unsupported uplink payload version: {version!r}")


class MalformedUplinkPayloadError(Exception):
    """Raised when an uplink payload doesn't match the documented shape
    (docs/04-api.md:202-220): a missing/wrong-typed field, or a `m` channel
    value that isn't numeric."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NodeNotFoundError(Exception):
    """Raised when a node isn't visible to the caller: unknown id, or not
    claimed by any organization the caller belongs to (docs/09-cuellos-de-
    botella.md#seguridad: a resource in another organization is 404, never
    403)."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        super().__init__(f"Node {node_id} not found")


class ClaimCodeNotFoundError(Exception):
    """Raised when `POST /nodes:claim` is given a `claim_code` that matches
    no node (docs/04-api.md:81; docs/06-diseno-detallado.md §2)."""

    def __init__(self, claim_code: str) -> None:
        self.claim_code = claim_code
        super().__init__(f"No node with claim code {claim_code!r}")


class NodeAlreadyClaimedError(Exception):
    """Raised when `POST /nodes:claim` targets a node that already has an
    organization (docs/06-diseno-detallado.md §2: `claim_code` is single-use).
    T3 decision: docs/04-api.md doesn't say which status this is, so it
    mirrors `farms.domain.errors.ActiveCropCycleExistsError` — a conflicting
    state on an otherwise well-formed request, mapped to `409`, not a missing
    resource."""

    def __init__(self, claim_code: str) -> None:
        self.claim_code = claim_code
        super().__init__(f"Node with claim code {claim_code!r} is already claimed")


class CalibrationVersionConflictError(Exception):
    """Raised when a `POST /sensors/{sensor_id}/calibrations` loses the race for
    the next `version` to another request (docs/03-modelo-datos.md:461:
    calibration is versioned and never edited in place, so the version is
    `uq_calibration_sensor_version`). T3-follow-up decision: a conflicting
    state on an otherwise well-formed request is a `409` the client retries,
    not a `500`."""

    def __init__(self, sensor_id: int) -> None:
        self.sensor_id = sensor_id
        super().__init__(f"Sensor {sensor_id} calibration version conflict")


class SensorNotFoundError(Exception):
    """Raised when a sensor isn't visible to the caller (see
    `NodeNotFoundError`)."""

    def __init__(self, sensor_id: int) -> None:
        self.sensor_id = sensor_id
        super().__init__(f"Sensor {sensor_id} not found")


class InvalidPlotError(Exception):
    """Raised when a `plot_id` referenced by `PATCH /nodes/{node_id}` doesn't
    belong to the node's own organization (task instruction). T3 decision:
    mirrors farms' `InvalidTechnicianError` — an invalid nested reference in
    the body is a `422`, not a `404` on the primary resource."""

    def __init__(self, plot_id: UUID) -> None:
        self.plot_id = plot_id
        super().__init__(f"Plot {plot_id} is not in this organization")


class InvalidReadingRangeError(Exception):
    """Raised by `GET /plots/{plot_id}/readings` (docs/04-api.md:92-97) for a
    `from`/`to` range whose boundaries are not both timezone-aware, that isn't
    `to` strictly after `from`, or a resolution requested over a range wider
    than it supports: `raw` up to 2 days, `hour` up to 60 days. `day` has no
    documented upper limit."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class InsufficientRoleError(Exception):
    """Raised when a membership role may not claim, patch, rotate or
    calibrate a node/sensor (mirrors `farms.domain.errors.InsufficientRoleError`,
    kept as telemetry's own type: `domain` never imports another module's
    `domain`)."""

    def __init__(self, role: object) -> None:
        self.role = role
        super().__init__(f"Role {role} cannot perform this write")
