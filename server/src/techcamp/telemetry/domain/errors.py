"""Domain-level telemetry failures. Pure, no I/O."""

from __future__ import annotations

from typing import Any


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
