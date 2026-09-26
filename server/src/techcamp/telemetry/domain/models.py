"""Node, sensor and calibration domain entities (docs/03-modelo-datos.md:136-167; ADR-0004).

Pure data, no I/O. `reading` gets its first domain shapes in T4
(`ReadingRecord`, the ingest event dataclasses): T1 built no use case that
read or wrote it directly (ingest insert is T4, `raw|hour|day` queries are
T5) — only the hypertable schema and repositories T3 needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID

from techcamp.identity.domain.models import Role
from techcamp.telemetry.domain.errors import (
    InsufficientRoleError,
    InvalidCalibrationParamsError,
    InvalidReadingRangeError,
    MalformedUplinkPayloadError,
    UnsupportedUplinkVersionError,
)


class NodeTransport(StrEnum):
    WIFI = "wifi"
    CELLULAR = "cellular"
    LORAWAN = "lorawan"


class NodeStatus(StrEnum):
    PROVISIONED = "provisioned"
    ONLINE = "online"
    OFFLINE = "offline"
    RETIRED = "retired"


WRITE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.TECHNICIAN})
"""Membership roles that may claim, patch, rotate or calibrate nodes/sensors.

docs/04-api.md is silent on which roles may write; T3 decision mirrors
farms' `WRITE_ROLES` (`farms/domain/models.py`): owner and technician write,
producer and viewer read only.
"""


def ensure_can_write(role: Role) -> None:
    """Reject a write from a role outside `WRITE_ROLES`."""
    if role not in WRITE_ROLES:
        raise InsufficientRoleError(role)


@dataclass(frozen=True, slots=True)
class Node:
    id: UUID
    org_id: UUID | None
    """Set once claimed; null while `provisioned` and unclaimed."""
    plot_id: UUID | None
    """Set once claimed; null while `provisioned` and unclaimed."""
    transport: NodeTransport
    dev_eui: str | None
    """Unique when present; LoRaWAN nodes have one, wifi/cellular nodes don't."""
    claim_code: str
    credential_hash: str
    firmware: str | None
    """Reported by the node itself (uplink `fw` field, docs/04-api.md); unset
    before its first uplink."""
    interval_s: int
    claimed_at: datetime | None
    """Set once the app claims the node onto a plot; null while `provisioned`."""
    last_seen_at: datetime | None
    status: NodeStatus


@dataclass(frozen=True, slots=True)
class Sensor:
    id: int
    node_id: UUID
    channel_key: str
    """Key in the uplink payload, e.g. `sm_10` (docs/03-modelo-datos.md:153)."""
    metric: str
    depth_cm: int | None
    """Null for sensors with no installation depth (e.g. temperature)."""
    unit: str


class CalibrationMethod(StrEnum):
    LINEAR = "linear"
    TWO_POINT = "two_point"
    POLYNOMIAL = "polynomial"


class CalibrationKind(StrEnum):
    LAB = "lab"
    FIELD = "field"


@dataclass(frozen=True, slots=True)
class Calibration:
    id: UUID
    sensor_id: int
    version: int
    method: CalibrationMethod
    kind: CalibrationKind
    params: dict[str, Any]
    """Method-specific coefficients (docs/03-modelo-datos.md:463-467), e.g.
    `{"scale": a, "offset": b}` for `linear`."""
    rmse_pct: float | None
    """Calibration error, when measured (docs/03-modelo-datos.md:165)."""
    valid_from: datetime


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _finite_float(value: Any, method: CalibrationMethod, key: str) -> float:
    """A coefficient the method's arithmetic can actually use. A JSON integer
    too large for a float raises `OverflowError` and `NaN`/`inf` break the
    `params` JSONB column, so all three are an
    `InvalidCalibrationParamsError` (a `422`), never a `500`."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise InvalidCalibrationParamsError(method.value, f"'{key}' must be a number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise InvalidCalibrationParamsError(method.value, f"'{key}' is out of range") from exc
    if not math.isfinite(number):
        raise InvalidCalibrationParamsError(method.value, f"'{key}' must be finite")
    return number


def _require_number(params: dict[str, Any], key: str, method: CalibrationMethod) -> float:
    return _finite_float(params.get(key), method, key)


def _require_coefficients(params: dict[str, Any], method: CalibrationMethod) -> list[float]:
    coeffs = params.get("coeffs")
    if not isinstance(coeffs, list) or not coeffs:
        raise InvalidCalibrationParamsError(
            method.value, "coeffs must be a non-empty list of numbers"
        )
    return [_finite_float(c, method, f"coeffs[{i}]") for i, c in enumerate(coeffs)]


def validate_calibration_params(calibration: Calibration) -> None:
    """`params` matches its `method` (docs/03-modelo-datos.md:463-467), checked
    directly instead of by running `apply_calibration(raw=0.0)`: a probe at a
    fake `raw` reports the shape only by accident of that arithmetic, and the
    use case that stores `params` has to reject a bad coefficient whether or not
    `raw` happens to touch it."""
    method, params = calibration.method, calibration.params
    if method is CalibrationMethod.LINEAR:
        _require_number(params, "scale", method)
        _require_number(params, "offset", method)
    elif method is CalibrationMethod.TWO_POINT:
        raw_dry = _require_number(params, "raw_dry", method)
        raw_wet = _require_number(params, "raw_wet", method)
        _require_number(params, "vwc_dry", method)
        _require_number(params, "vwc_wet", method)
        if raw_dry == raw_wet:
            raise InvalidCalibrationParamsError(method.value, "raw_dry and raw_wet must differ")
    elif method is CalibrationMethod.POLYNOMIAL:
        _require_coefficients(params, method)
    else:
        raise AssertionError(f"unhandled calibration method: {method}")


def apply_calibration(calibration: Calibration, raw: float) -> float:
    """Map a raw ADC value to its calibrated value (docs/03-modelo-datos.md:463-467)."""
    # The one check this arithmetic below doesn't repeat: `two_point` divides by
    # `raw_wet - raw_dry` instead of comparing it.
    validate_calibration_params(calibration)
    params = calibration.params
    method = calibration.method
    if method is CalibrationMethod.LINEAR:
        scale = _require_number(params, "scale", method)
        offset = _require_number(params, "offset", method)
        return scale * raw + offset
    if method is CalibrationMethod.TWO_POINT:
        raw_dry = _require_number(params, "raw_dry", method)
        raw_wet = _require_number(params, "raw_wet", method)
        vwc_dry = _require_number(params, "vwc_dry", method)
        vwc_wet = _require_number(params, "vwc_wet", method)
        fraction = (raw - raw_dry) / (raw_wet - raw_dry)
        return vwc_dry + fraction * (vwc_wet - vwc_dry)
    if method is CalibrationMethod.POLYNOMIAL:
        return sum(c * raw**i for i, c in enumerate(_require_coefficients(params, method)))
    raise AssertionError(f"unhandled calibration method: {method}")


SUPPORTED_UPLINK_VERSION = 1
"""docs/04-api.md:216: the only uplink payload schema version this server
understands; any other `v` is discarded by the ingestor and counted."""


@dataclass(frozen=True, slots=True)
class UplinkPayload:
    """A validated uplink payload (docs/04-api.md:202-220)."""

    version: int
    seq: int
    ts: int | None
    """Epoch seconds from the node's clock; `None` when absent from the payload."""
    firmware: str
    channels: dict[str, float]
    """`channel_key -> raw_value` (docs/04-api.md:219)."""


def _as_utc(value: datetime) -> datetime:
    """`received_at` is the server's own clock, so a naive value is read as UTC
    rather than crashing the comparison that follows it with a bare
    `TypeError` (which would abort a whole ingest batch over an internal
    detail, and counting the node's payload as malformed would blame it for
    something it didn't send)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _epoch_to_datetime(ts: int) -> datetime:
    """An aware UTC `datetime` for a node-clock `ts`.

    An epoch outside the representable range (overflow, or a clock that never
    ticked) is a malformed payload, not a crash: docs/04-api.md:218's fallback
    is for a `ts` that is *present* but skewed, so there is nothing to fall
    back to and `datetime.fromtimestamp` would raise `OverflowError`/
    `ValueError`/`OSError` into the ingest batch."""
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise MalformedUplinkPayloadError(f"'ts' is outside the representable range: {ts}") from exc


def parse_uplink(payload: dict[str, Any]) -> UplinkPayload:
    """Validate and parse a raw uplink payload into `UplinkPayload`.

    Unknown/missing `v` is rejected with `UnsupportedUplinkVersionError`
    (distinct from other shape errors) so the ingestor can discard and count
    it separately (docs/04-api.md:216)."""
    if not isinstance(payload, dict):
        raise MalformedUplinkPayloadError("payload must be a JSON object")

    version = payload.get("v")
    if isinstance(version, bool) or (version is not None and not isinstance(version, int)):
        # `True == 1` and `1.0 == 1`, so the version is type-checked like
        # `seq`/`ts` instead of trusting a bare `!=`. An absent `v` stays an
        # unsupported version, not a malformed payload.
        raise MalformedUplinkPayloadError("'v' must be an integer")
    if version != SUPPORTED_UPLINK_VERSION:
        raise UnsupportedUplinkVersionError(version)

    seq = payload.get("seq")
    if not _is_number(seq) or not isinstance(seq, int):
        raise MalformedUplinkPayloadError("'seq' must be an integer")

    ts = payload.get("ts")
    if ts is not None and (not _is_number(ts) or not isinstance(ts, int)):
        raise MalformedUplinkPayloadError("'ts' must be an integer epoch or absent")
    if ts is not None:
        _epoch_to_datetime(ts)  # rejects an unrepresentable `ts` here, not mid-flush

    firmware = payload.get("fw")
    if not isinstance(firmware, str):
        raise MalformedUplinkPayloadError("'fw' must be a string")

    channels = payload.get("m")
    if not isinstance(channels, dict) or not channels:
        raise MalformedUplinkPayloadError("'m' must be a non-empty object")
    for key, value in channels.items():
        if not isinstance(key, str) or not _is_number(value):
            raise MalformedUplinkPayloadError(f"channel '{key}' must map to a number")
        if isinstance(value, float) and not math.isfinite(value):
            # `json` decodes NaN/Infinity by default; a non-finite raw value
            # calibrates to a non-finite reading, which no quality flag
            # describes (only `%` has a documented range, docs/06 §1).
            raise MalformedUplinkPayloadError(f"channel '{key}' must map to a finite number")

    return UplinkPayload(
        version=version,
        seq=seq,
        ts=ts,
        firmware=firmware,
        channels={key: float(value) for key, value in channels.items()},
    )


class ReadingQuality(IntEnum):
    """docs/03-modelo-datos.md:174: `0 ok, 1 ts corregido, 2 fuera de rango`."""

    OK = 0
    TIMESTAMP_CORRECTED = 1
    OUT_OF_RANGE = 2


_FUTURE_TOLERANCE = timedelta(minutes=10)


def resolve_reading_time(ts: int | None, received_at: datetime) -> tuple[datetime, ReadingQuality]:
    """docs/04-api.md:218: a missing or more-than-10-minutes-future `ts`
    falls back to `received_at` with `quality = 1`."""
    received_at = _as_utc(received_at)
    if ts is not None:
        at = _epoch_to_datetime(ts)
        if at <= received_at + _FUTURE_TOLERANCE:
            return at, ReadingQuality.OK
    return received_at, ReadingQuality.TIMESTAMP_CORRECTED


def classify_reading_range(unit: str, value: float) -> ReadingQuality:
    """docs/06-diseno-detallado.md §1: "Un valor calibrado fuera del rango de
    la variable ... se guarda con quality = 2". The only range docs document
    is implicit in a percentage (its example: humidity > 100%), so a `%`
    reading outside `[0, 100]` is the one rule enforced here. No other unit
    has a documented range (flagged gap): those always come back OK rather
    than an invented threshold."""
    if unit == "%" and not 0 <= value <= 100:
        return ReadingQuality.OUT_OF_RANGE
    return ReadingQuality.OK


def recalibrate_reading(
    calibration: Calibration, raw_value: float, unit: str, previous_quality: ReadingQuality
) -> tuple[float, ReadingQuality]:
    """Recompute `(value, quality)` for one already-stored reading against a
    (new) calibration version (docs/03-modelo-datos.md:461: recalibrating
    recomputes `value` from `valid_from` forward), reusing `apply_calibration`
    and `classify_reading_range` — same `max(ts, range)` precedence as
    `ingest_uplinks` (out-of-range is the stronger signal).

    `reading.quality` merges two independent signals into one column
    (docs/03-modelo-datos.md:174), so the original timestamp-correction bit
    isn't stored separately. `previous_quality` recovers it: `OK` and
    `TIMESTAMP_CORRECTED` are unambiguous; a previous `OUT_OF_RANGE` can't
    tell whether the timestamp was also corrected underneath it (T7 decision,
    flagged doc gap: docs are silent here), so it's conservatively treated as
    `TIMESTAMP_CORRECTED` rather than silently downgrading to a clean `OK`.
    """
    value = apply_calibration(calibration, raw_value)
    ts_quality = (
        ReadingQuality.OK
        if previous_quality is ReadingQuality.OK
        else ReadingQuality.TIMESTAMP_CORRECTED
    )
    range_quality = classify_reading_range(unit, value)
    quality = ReadingQuality(max(int(ts_quality), int(range_quality)))
    return value, quality


MAX_READING_AGE = timedelta(days=30)
"""docs/06-diseno-detallado.md §1: "Si [ts] es anterior a 30 días, se
descarta" — an outright discard by the ingestor, unlike the future-clock case
in `resolve_reading_time`, which only flags `quality=1`."""


def is_reading_too_old(ts: int, received_at: datetime) -> bool:
    """`True` when the node-clock `ts` is more than `MAX_READING_AGE` behind
    `received_at`. Only meaningful when `ts` is present; a missing `ts` is
    `resolve_reading_time`'s concern, not this one's."""
    return _epoch_to_datetime(ts) < _as_utc(received_at) - MAX_READING_AGE


@dataclass(frozen=True, slots=True)
class ReadingRecord:
    """One row for `ReadingRepository.insert_batch` (docs/03-modelo-
    datos.md:168-175). `value` is `None` when no calibration was valid at
    `time` (T4 decision, flagged doc gap: docs are silent on this case) — the
    raw value is still stored, uncalibrated."""

    sensor_id: int
    time: datetime
    raw_value: float
    value: float | None
    received_at: datetime
    quality: ReadingQuality


@dataclass(frozen=True, slots=True)
class NodeSeenUpdate:
    """One node's `last_seen_at`/`status` change from an ingest flush
    (docs/06-diseno-detallado.md §1)."""

    node_id: UUID
    org_id: UUID
    plot_id: UUID
    last_seen_at: datetime
    status: NodeStatus


class ReadingResolution(StrEnum):
    """`GET /plots/{plot_id}/readings?resolution=` (docs/04-api.md:92)."""

    RAW = "raw"
    HOUR = "hour"
    DAY = "day"


@dataclass(frozen=True, slots=True)
class ReadingPoint:
    """One `[t, value]` point (docs/04-api.md:93): the calibrated `value` for
    `raw`, or the bucket average for `hour`/`day` (T5 decision, doc gap: the
    documented point shape carries one value, not separate avg/min/max, so
    aggregated points use the bucket's average)."""

    time: datetime
    value: float


MAX_RAW_RANGE = timedelta(days=2)
"""docs/04-api.md:97: "`raw` hasta 2 días"."""

MAX_HOUR_RANGE = timedelta(days=60)
"""docs/04-api.md:97: "`hour` hasta 60 días"."""


def validate_reading_range(resolution: ReadingResolution, start: datetime, end: datetime) -> None:
    """docs/04-api.md:20 (`from` inclusive, `to` exclusive) and :97 (resolution
    range limits). `day` has no documented upper limit ("`day` para más")."""
    if end <= start:
        raise InvalidReadingRangeError("`to` must be after `from`")
    span = end - start
    if resolution is ReadingResolution.RAW and span > MAX_RAW_RANGE:
        raise InvalidReadingRangeError("raw resolution is limited to a 2-day range")
    if resolution is ReadingResolution.HOUR and span > MAX_HOUR_RANGE:
        raise InvalidReadingRangeError("hour resolution is limited to a 60-day range")


@dataclass(frozen=True, slots=True)
class ReadingEvent:
    """`NOTIFY plot_events` payload for a `reading` SSE event
    (docs/04-api.md:180-189). Carries `org_id`/`farm_id` beyond what the SSE
    client sees, for T6's org check and per-farm fan-out (task instruction)."""

    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    metric: str
    value: float
    at: datetime


@dataclass(frozen=True, slots=True)
class NodeStatusEvent:
    """`NOTIFY plot_events` payload for a `node.status` SSE event
    (docs/04-api.md:180-189), same `org_id`/`farm_id` reasoning as
    `ReadingEvent`."""

    org_id: UUID
    farm_id: UUID
    node_id: UUID
    status: NodeStatus
    at: datetime
