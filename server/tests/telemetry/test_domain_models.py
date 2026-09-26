from datetime import UTC, datetime
from uuid import UUID

import pytest

from techcamp.telemetry.domain.errors import (
    InvalidCalibrationParamsError,
    MalformedUplinkPayloadError,
    UnsupportedUplinkVersionError,
)
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    ReadingQuality,
    UplinkPayload,
    apply_calibration,
    classify_reading_range,
    parse_uplink,
    resolve_reading_time,
)

_CALIBRATION_ID = UUID("00000000-0000-0000-0000-000000000001")
_VALID_FROM = datetime(2026, 1, 1, tzinfo=UTC)


def _calibration(method: CalibrationMethod, params: dict[str, object]) -> Calibration:
    return Calibration(
        id=_CALIBRATION_ID,
        sensor_id=1,
        version=1,
        method=method,
        kind=CalibrationKind.FIELD,
        params=params,
        rmse_pct=None,
        valid_from=_VALID_FROM,
    )


# -- calibration: linear --


def test_linear_calibration_applies_scale_and_offset() -> None:
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 0.01, "offset": -5.0})
    assert apply_calibration(calibration, 1000.0) == 5.0


def test_linear_calibration_rejects_missing_offset() -> None:
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 0.01})
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 1000.0)


def test_linear_calibration_rejects_non_numeric_param() -> None:
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": "0.01", "offset": -5.0})
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 1000.0)


# -- calibration: two_point (docs/03-modelo-datos.md:466 example values) --


def test_two_point_calibration_interpolates_capacitive_soil_moisture() -> None:
    calibration = _calibration(
        CalibrationMethod.TWO_POINT,
        {"raw_dry": 2900, "raw_wet": 1300, "vwc_dry": 5, "vwc_wet": 45},
    )
    assert apply_calibration(calibration, 2100.0) == pytest.approx(25.0)


def test_two_point_calibration_at_dry_endpoint() -> None:
    calibration = _calibration(
        CalibrationMethod.TWO_POINT,
        {"raw_dry": 2900, "raw_wet": 1300, "vwc_dry": 5, "vwc_wet": 45},
    )
    assert apply_calibration(calibration, 2900.0) == pytest.approx(5.0)


def test_two_point_calibration_rejects_equal_raw_endpoints() -> None:
    calibration = _calibration(
        CalibrationMethod.TWO_POINT,
        {"raw_dry": 2000, "raw_wet": 2000, "vwc_dry": 5, "vwc_wet": 45},
    )
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 2000.0)


def test_two_point_calibration_rejects_missing_param() -> None:
    calibration = _calibration(
        CalibrationMethod.TWO_POINT, {"raw_dry": 2900, "raw_wet": 1300, "vwc_dry": 5}
    )
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 2100.0)


# -- calibration: polynomial --


def test_polynomial_calibration_evaluates_coefficients() -> None:
    calibration = _calibration(CalibrationMethod.POLYNOMIAL, {"coeffs": [0, 1, 0.0001]})
    assert apply_calibration(calibration, 100.0) == pytest.approx(101.0)


def test_polynomial_calibration_rejects_empty_coeffs() -> None:
    calibration = _calibration(CalibrationMethod.POLYNOMIAL, {"coeffs": []})
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 100.0)


def test_polynomial_calibration_rejects_non_numeric_coeff() -> None:
    calibration = _calibration(CalibrationMethod.POLYNOMIAL, {"coeffs": [0, "1"]})
    with pytest.raises(InvalidCalibrationParamsError):
        apply_calibration(calibration, 100.0)


# -- uplink parsing (docs/04-api.md:204-211 example payload) --

_VALID_PAYLOAD = {
    "v": 1,
    "seq": 18234,
    "ts": 1760000400,
    "fw": "1.0.3",
    "m": {"sm_10": 2310, "sm_30": 2250, "st_10": 27.4, "at": 31.2, "rh": 68, "bat": 3.92},
}


def test_parse_uplink_accepts_the_documented_shape() -> None:
    payload = parse_uplink(_VALID_PAYLOAD)
    assert payload == UplinkPayload(
        version=1,
        seq=18234,
        ts=1760000400,
        firmware="1.0.3",
        channels={
            "sm_10": 2310.0,
            "sm_30": 2250.0,
            "st_10": 27.4,
            "at": 31.2,
            "rh": 68.0,
            "bat": 3.92,
        },
    )


def test_parse_uplink_accepts_missing_ts() -> None:
    payload = parse_uplink({**_VALID_PAYLOAD, "ts": None})
    assert payload.ts is None


def test_parse_uplink_rejects_unknown_version() -> None:
    with pytest.raises(UnsupportedUplinkVersionError):
        parse_uplink({**_VALID_PAYLOAD, "v": 2})


def test_parse_uplink_rejects_missing_version() -> None:
    payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "v"}
    with pytest.raises(UnsupportedUplinkVersionError):
        parse_uplink(payload)


def test_parse_uplink_rejects_non_numeric_channel_value() -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "m": {"sm_10": "wet"}})


def test_parse_uplink_rejects_empty_channel_map() -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "m": {}})


def test_parse_uplink_rejects_missing_seq() -> None:
    payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "seq"}
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink(payload)


def test_parse_uplink_rejects_non_string_firmware() -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "fw": 103})


# -- quality: timestamp resolution (docs/04-api.md:218) --


def test_resolve_reading_time_keeps_a_fresh_timestamp() -> None:
    received_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    at, quality = resolve_reading_time(int(received_at.timestamp()) - 60, received_at)
    assert at == datetime(2026, 1, 1, 11, 59, tzinfo=UTC)
    assert quality == ReadingQuality.OK


def test_resolve_reading_time_falls_back_when_ts_is_missing() -> None:
    received_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    at, quality = resolve_reading_time(None, received_at)
    assert at == received_at
    assert quality == ReadingQuality.TIMESTAMP_CORRECTED


def test_resolve_reading_time_falls_back_when_ts_is_far_in_the_future() -> None:
    received_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    future_ts = int(received_at.timestamp()) + 11 * 60
    at, quality = resolve_reading_time(future_ts, received_at)
    assert at == received_at
    assert quality == ReadingQuality.TIMESTAMP_CORRECTED


def test_resolve_reading_time_accepts_the_10_minute_boundary() -> None:
    received_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    boundary_ts = int(received_at.timestamp()) + 10 * 60
    at, quality = resolve_reading_time(boundary_ts, received_at)
    assert quality == ReadingQuality.OK
    assert at == datetime(2026, 1, 1, 12, 10, tzinfo=UTC)


# -- quality: out-of-range (docs/06-diseno-detallado.md §1: only a documented
# example is a percentage over 100%; no other metric has a documented range) --


def test_classify_reading_range_flags_a_percentage_above_100() -> None:
    assert classify_reading_range("%", 101.0) == ReadingQuality.OUT_OF_RANGE


def test_classify_reading_range_flags_a_negative_percentage() -> None:
    assert classify_reading_range("%", -1.0) == ReadingQuality.OUT_OF_RANGE


def test_classify_reading_range_accepts_a_percentage_in_range() -> None:
    assert classify_reading_range("%", 68.0) == ReadingQuality.OK


def test_classify_reading_range_has_no_documented_range_for_other_units() -> None:
    """docs give no plausible range for °C, V or dBm — flagged as a gap
    rather than an invented threshold; these always come back OK."""
    assert classify_reading_range("°C", 999.0) == ReadingQuality.OK
