from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from techcamp.telemetry.domain.errors import (
    InvalidCalibrationParamsError,
    InvalidReadingRangeError,
    MalformedUplinkPayloadError,
    UnsupportedUplinkVersionError,
)
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    ReadingQuality,
    ReadingResolution,
    UplinkPayload,
    apply_calibration,
    classify_reading_range,
    is_reading_too_old,
    parse_uplink,
    recalibrate_reading,
    resolve_reading_time,
    validate_reading_range,
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


# -- GitHub #34: strict types and a `ts` a datetime can actually represent --


@pytest.mark.parametrize("ts", [10**20, -(10**20), 10**15, -(10**12)])
def test_parse_uplink_rejects_a_ts_outside_the_representable_range(ts: int) -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "ts": ts})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_parse_uplink_rejects_a_non_finite_channel_value(value: float) -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "m": {"sm_10": value}})


@pytest.mark.parametrize("version", [True, 1.0])
def test_parse_uplink_rejects_a_non_integer_version(version: object) -> None:
    """`seq`/`ts` reject a bool or a float; `v` must too — `True == 1` and
    `1.0 == 1`, so a bare `!=` check let both through."""
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink({**_VALID_PAYLOAD, "v": version})


@pytest.mark.parametrize("payload", [[1, 2], "sm_10", 7, None])
def test_parse_uplink_rejects_a_payload_that_is_not_an_object(payload: object) -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        parse_uplink(payload)  # type: ignore[arg-type]


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


# -- T7 recalibration (docs/03-modelo-datos.md:461): recomputing `value`/
# `quality` for an already-stored reading under a (new) calibration version.


def test_recalibrate_reading_applies_the_new_calibration() -> None:
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 2.0, "offset": 1.0})
    value, quality = recalibrate_reading(calibration, 10.0, "°C", ReadingQuality.OK)
    assert value == 21.0
    assert quality == ReadingQuality.OK


def test_recalibrate_reading_reclassifies_out_of_range_under_the_new_calibration() -> None:
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 2.0, "offset": 0.0})
    value, quality = recalibrate_reading(calibration, 60.0, "%", ReadingQuality.OK)
    assert value == 120.0
    assert quality == ReadingQuality.OUT_OF_RANGE


def test_recalibrate_reading_preserves_a_timestamp_corrected_flag() -> None:
    """A previously `TIMESTAMP_CORRECTED` reading keeps that signal even when
    the recalibrated value is back in range (max(ts, range) ordering, same as
    `ingest_uplinks`)."""
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 1.0, "offset": 0.0})
    value, quality = recalibrate_reading(calibration, 50.0, "%", ReadingQuality.TIMESTAMP_CORRECTED)
    assert value == 50.0
    assert quality == ReadingQuality.TIMESTAMP_CORRECTED


def test_recalibrate_reading_treats_a_previous_out_of_range_as_conservatively_corrected() -> None:
    """T7 decision, flagged doc gap: `quality` merges two independent signals
    into one column, so a previous `OUT_OF_RANGE` can't tell whether the
    timestamp was also corrected underneath it. Recomputing with a calibration
    that brings the value back in range conservatively keeps
    `TIMESTAMP_CORRECTED` rather than silently claiming a clean `OK`."""
    calibration = _calibration(CalibrationMethod.LINEAR, {"scale": 1.0, "offset": 0.0})
    value, quality = recalibrate_reading(calibration, 50.0, "%", ReadingQuality.OUT_OF_RANGE)
    assert value == 50.0
    assert quality == ReadingQuality.TIMESTAMP_CORRECTED


# -- ingest: 30-day discard (docs/06-diseno-detallado.md §1: "Si es anterior
# a 30 días, se descarta" — outright discarded, unlike the future-clock case
# above which is only flagged with quality=1) --


def test_is_reading_too_old_flags_a_ts_more_than_30_days_in_the_past() -> None:
    received_at = datetime(2026, 2, 1, tzinfo=UTC)
    ts = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    assert is_reading_too_old(ts, received_at) is True


def test_is_reading_too_old_accepts_the_30_day_boundary() -> None:
    received_at = datetime(2026, 2, 1, tzinfo=UTC)
    ts = int((received_at - timedelta(days=30)).timestamp())
    assert is_reading_too_old(ts, received_at) is False


def test_is_reading_too_old_accepts_a_recent_ts() -> None:
    received_at = datetime(2026, 2, 1, tzinfo=UTC)
    ts = int((received_at - timedelta(days=1)).timestamp())
    assert is_reading_too_old(ts, received_at) is False


# -- GitHub #36: an unrepresentable `ts` is a malformed payload, never a
# crash that aborts the whole ingest batch (models.py:251) --


def test_is_reading_too_old_rejects_a_ts_outside_the_representable_range() -> None:
    with pytest.raises(MalformedUplinkPayloadError):
        is_reading_too_old(10**15, datetime(2026, 2, 1, tzinfo=UTC))


# -- GitHub #34: a naive `received_at` is the server's own clock, read as UTC
# instead of raising a bare `TypeError` on the comparison --


def test_resolve_reading_time_reads_a_naive_received_at_as_utc() -> None:
    naive_received_at = datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001 - the point of the test
    at, quality = resolve_reading_time(None, naive_received_at)
    assert at == naive_received_at.replace(tzinfo=UTC)
    assert quality == ReadingQuality.TIMESTAMP_CORRECTED


def test_is_reading_too_old_reads_a_naive_received_at_as_utc() -> None:
    ts = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
    assert is_reading_too_old(ts, datetime(2026, 2, 1)) is True  # noqa: DTZ001


# -- readings query range validation (docs/04-api.md:97: raw up to 2 days,
# hour up to 60 days; day has no documented upper limit) --


def test_validate_reading_range_rejects_to_not_after_from() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(InvalidReadingRangeError):
        validate_reading_range(ReadingResolution.RAW, at, at)


def test_validate_reading_range_accepts_raw_within_2_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    validate_reading_range(ReadingResolution.RAW, start, start + timedelta(days=2))


def test_validate_reading_range_rejects_raw_beyond_2_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(InvalidReadingRangeError):
        validate_reading_range(ReadingResolution.RAW, start, start + timedelta(days=2, seconds=1))


def test_validate_reading_range_accepts_hour_within_60_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    validate_reading_range(ReadingResolution.HOUR, start, start + timedelta(days=60))


def test_validate_reading_range_rejects_hour_beyond_60_days() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(InvalidReadingRangeError):
        validate_reading_range(ReadingResolution.HOUR, start, start + timedelta(days=60, seconds=1))


def test_validate_reading_range_accepts_day_beyond_60_days() -> None:
    """docs/04-api.md:97 gives `day` no documented upper limit."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    validate_reading_range(ReadingResolution.DAY, start, start + timedelta(days=365))
