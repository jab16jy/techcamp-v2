"""E16 T3: the weather recorder behind `sim record-weather`
(docs/06-diseno-detallado.md:490; D-T16.3).

Record once, then offline: the capture is the only thing that touches the
network, and what it leaves behind is a fixture the scenario library's own
contract accepts. So the recorder's job is not "fetch some JSON" but "leave a
file `parse_fixture` cannot argue with": the envelope's keys, the rows keyed by
the scenario's day offset, and the canonical digest over those rows.

The capture is doubled at its port (httpx's transport), so the parsing and the
writing are tested with no socket at all — the same property
`test_weather_fixtures.py::test_loading_the_library_opens_no_socket` asks of
the library, and the one CI needs (docs/06:489).
"""

from __future__ import annotations

import json
import socket
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from techcamp.simulator.__main__ import parse_record_weather_args
from techcamp.simulator.recorder import (
    ARCHIVE_ENDPOINT,
    DAILY_VARIABLES,
    DEFAULT_SYNTHETIC_ET0_MM,
    RecorderError,
    archive_url,
    build_envelope,
    dump_fixture,
    fixture_cell,
    parse_daily_payload,
    record_weather,
    scenario_fixture_name,
)
from techcamp.simulator.scenario_library import (
    FORECAST_DAYS,
    canonical_days_sha256,
    parse_fixture,
)

pytestmark = pytest.mark.anyio

FROM_DAY = date(2016, 12, 14)
DAYS = 28
LAT = 10.98
LON = -74.80


def _archive_payload(*, days: int = DAYS, null_at: int | None = None) -> dict[str, Any]:
    """An Open-Meteo archive answer shaped like the real one."""
    daily: dict[str, Any] = {
        "time": [(FROM_DAY + timedelta(days=offset)).isoformat() for offset in range(days)]
    }
    for variable, base in (
        ("et0_fao_evapotranspiration", 4.2),
        ("temperature_2m_max", 28.8),
        ("temperature_2m_min", 26.8),
        ("precipitation_sum", 0.0),
        ("relative_humidity_2m_mean", 80.0),
    ):
        daily[variable] = [base + offset / 10 for offset in range(days)]
    if null_at is not None:
        daily["et0_fao_evapotranspiration"][null_at] = None
    return {"latitude": LAT, "longitude": LON, "timezone": "America/Bogota", "daily": daily}


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_the_archive_url_mirrors_the_recorded_library() -> None:
    """The URL is the one the library's fixtures already carry, so a
    re-recording asks the provider exactly what the first capture asked."""
    url = archive_url(lat=LAT, lon=LON, from_day=FROM_DAY, days=DAYS)

    assert url.startswith(f"{ARCHIVE_ENDPOINT}?")
    assert "latitude=10.98" in url
    assert "longitude=-74.80" in url
    assert "start_date=2016-12-14" in url
    assert "end_date=2017-01-10" in url
    assert f"daily={','.join(DAILY_VARIABLES)}" in url
    assert "timezone=America%2FBogota" in url


def test_the_rows_are_keyed_by_the_scenario_day_offset() -> None:
    """docs/06:490: "filas diarias por día-offset del escenario" — the offset
    is what the loader places on the scenario's days, so the rows count from 0
    in order and the days are contiguous."""
    rows = parse_daily_payload(_archive_payload(), from_day=FROM_DAY, days=DAYS)

    assert [row["day_offset"] for row in rows] == list(range(DAYS))
    assert rows[0]["day"] == FROM_DAY.isoformat()
    assert rows[-1]["day"] == "2017-01-10"
    assert all(
        (date.fromisoformat(current["day"]) - date.fromisoformat(previous["day"])).days == 1
        for previous, current in zip(rows, rows[1:], strict=False)
    )


def test_the_forecast_block_is_the_last_seven_days() -> None:
    """The recommendation reads the forecast window `[D, D+6]`
    (docs/06:246), so the tail of the capture is the forecast block and the
    head is observed."""
    rows = parse_daily_payload(_archive_payload(), from_day=FROM_DAY, days=DAYS)

    assert rows[0]["is_forecast"] is False
    assert rows[-FORECAST_DAYS]["is_forecast"] is True
    assert all(row["is_forecast"] is False for row in rows[:-FORECAST_DAYS])


def test_the_rows_carry_the_variables_the_balance_needs() -> None:
    rows = parse_daily_payload(_archive_payload(), from_day=FROM_DAY, days=DAYS)

    assert set(rows[0]) == {
        "day",
        "day_offset",
        "et0_mm",
        "is_forecast",
        "rain_mm",
        "rh_mean_pct",
        "tmax_c",
        "tmin_c",
    }
    assert isinstance(rows[0]["et0_mm"], float)
    assert isinstance(rows[0]["rain_mm"], float)


def test_a_measure_the_provider_did_not_report_is_refused() -> None:
    """A null measure cannot become a row: the contract wants a real float,
    and a silent zero would be a lie the balance would believe."""
    with pytest.raises(RecorderError, match="et0_fao_evapotranspiration"):
        parse_daily_payload(_archive_payload(null_at=3), from_day=FROM_DAY, days=DAYS)


def test_a_capture_of_the_wrong_length_is_refused() -> None:
    with pytest.raises(RecorderError, match="day"):
        parse_daily_payload(_archive_payload(days=10), from_day=FROM_DAY, days=DAYS)


async def test_the_recording_leaves_a_fixture_the_contract_accepts(tmp_path: Path) -> None:
    """The whole point: the file the recorder writes is a fixture the scenario
    library accepts, digest and all."""
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json=_archive_payload())

    recorded = await record_weather(
        scenario="el-nino",
        name="el-nino-caribe",
        from_day=FROM_DAY,
        days=DAYS,
        lat=LAT,
        lon=LON,
        out_dir=tmp_path,
        client=_client(handler),
        recorded_at=datetime(2026, 10, 7, 2, 27, 43, tzinfo=UTC),
    )

    assert len(captured) == 1, "the archive is captured once, never per day"
    fixture = parse_fixture("el-nino-caribe", json.loads(recorded.path.read_text(encoding="utf-8")))
    assert fixture.scenario == "el-nino"
    assert fixture.synthetic is False
    assert len(fixture.observed) == DAYS - FORECAST_DAYS
    assert len(fixture.forecast) == FORECAST_DAYS
    assert fixture.data_sha256 == recorded.data_sha256


async def test_the_envelope_carries_exactly_the_contract_keys(tmp_path: Path) -> None:
    """`parse_fixture` refuses an unknown key and a missing one; the recorder
    must land on the same set the contract writes down."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_archive_payload())

    recorded = await record_weather(
        scenario="el-nino",
        name="el-nino-caribe",
        from_day=FROM_DAY,
        days=DAYS,
        lat=LAT,
        lon=LON,
        out_dir=tmp_path,
        client=_client(handler),
    )

    raw = json.loads(recorded.path.read_text(encoding="utf-8"))
    assert set(raw) == {
        "version",
        "name",
        "scenario",
        "cell",
        "timezone",
        "source_url",
        "recorded_at",
        "synthetic",
        "data_sha256",
        "days",
    }
    assert raw["version"] == 1
    assert raw["timezone"] == "America/Bogota"
    assert raw["recorded_at"].endswith("Z")
    assert raw["source_url"].startswith(ARCHIVE_ENDPOINT)


async def test_the_digest_covers_the_row_array(tmp_path: Path) -> None:
    """`data_sha256` is the contract's canonical digest over the rows, so an
    edited measure cannot pass unnoticed by either side."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_archive_payload())

    recorded = await record_weather(
        scenario="el-nino",
        name="el-nino-caribe",
        from_day=FROM_DAY,
        days=DAYS,
        lat=LAT,
        lon=LON,
        out_dir=tmp_path,
        client=_client(handler),
    )

    raw = json.loads(recorded.path.read_text(encoding="utf-8"))
    assert raw["data_sha256"] == canonical_days_sha256(raw["days"])

    tampered = [dict(row) for row in raw["days"]]
    tampered[0]["et0_mm"] = tampered[0]["et0_mm"] + 0.01
    assert canonical_days_sha256(tampered) != raw["data_sha256"]


async def test_a_synthetic_capture_carries_its_reason(tmp_path: Path) -> None:
    """D-T16.3: record-first; synthetic only behind an explicit flag that
    carries the reason the recording could not reproduce the arithmetic."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a synthetic capture must not reach the network")

    recorded = await record_weather(
        scenario="dry-spell-rainfed",
        name="veranillo-caribe",
        from_day=FROM_DAY,
        days=DAYS,
        lat=LAT,
        lon=LON,
        out_dir=tmp_path,
        client=_client(handler),
        synthetic=True,
        synthetic_reason="the January window's rain breaks the veranillo arithmetic",
    )

    raw = json.loads(recorded.path.read_text(encoding="utf-8"))
    assert raw["synthetic"] is True
    assert raw["synthetic_reason"]
    fixture = parse_fixture("veranillo-caribe", raw)
    assert fixture.synthetic is True


async def test_a_synthetic_capture_without_a_reason_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RecorderError, match="reason"):
        await record_weather(
            scenario="dry-spell-rainfed",
            name="veranillo-caribe",
            from_day=FROM_DAY,
            days=DAYS,
            lat=LAT,
            lon=LON,
            out_dir=tmp_path,
            synthetic=True,
        )


async def test_the_synthetic_series_reproduces_the_documented_arithmetic(
    tmp_path: Path,
) -> None:
    """docs/06:458: the Caribe cell's ET0 ≈ 4,2 mm/día and no rain over 5 mm,
    which is what keeps Pe = 0 so the depletion only grows."""
    recorded = await record_weather(
        scenario="dry-spell-rainfed",
        name="veranillo-caribe",
        from_day=FROM_DAY,
        days=DAYS,
        lat=LAT,
        lon=LON,
        out_dir=tmp_path,
        synthetic=True,
        synthetic_reason="the recorded window does not reproduce ET0 ≈ 4,2",
    )

    raw = json.loads(recorded.path.read_text(encoding="utf-8"))
    rows = raw["days"]
    assert all(row["et0_mm"] == DEFAULT_SYNTHETIC_ET0_MM for row in rows)
    assert all(row["rain_mm"] <= 5.0 for row in rows)


async def test_a_capture_shorter_than_the_forecast_window_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RecorderError, match="days"):
        await record_weather(
            scenario="el-nino",
            name="el-nino-caribe",
            from_day=FROM_DAY,
            days=3,
            lat=LAT,
            lon=LON,
            out_dir=tmp_path,
        )


def test_the_recorder_reads_the_scenario_library_for_its_defaults() -> None:
    """The fixture a scenario pins is the file a re-recording must replace,
    and its cell is the point the capture asks about."""
    assert scenario_fixture_name("el-nino") == "el-nino-caribe"
    assert scenario_fixture_name("dry-spell-rainfed") == "veranillo-caribe"

    lat, lon = fixture_cell("el-nino-caribe")
    assert (lat, lon) == (11.0, -74.8)


def test_the_recording_writes_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/06:489 — the recording is done once, by hand; the library and
    everything downstream read the file, never the network."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the recorder must not touch the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    rows = parse_daily_payload(_archive_payload(), from_day=FROM_DAY, days=DAYS)
    envelope = build_envelope(
        name="el-nino-caribe",
        scenario="el-nino",
        lat=LAT,
        lon=LON,
        source_url=archive_url(lat=LAT, lon=LON, from_day=FROM_DAY, days=DAYS),
        recorded_at=datetime(2026, 10, 7, 2, 27, 43, tzinfo=UTC),
        rows=rows,
        synthetic=False,
        synthetic_reason=None,
    )
    assert parse_fixture("el-nino-caribe", json.loads(dump_fixture(envelope))) is not None


def test_the_cli_reads_the_capture_the_way_the_docs_write_it() -> None:
    """D-T16.3: `sim record-weather --scenario X --from YYYY-MM-DD --days N`."""
    args = parse_record_weather_args(
        ["--scenario", "el-nino", "--from", "2016-12-14", "--days", "28"]
    )

    assert args.scenario == "el-nino"
    assert args.from_day == date(2016, 12, 14)
    assert args.days == 28
    assert args.synthetic is False


def test_the_cli_refuses_a_synthetic_capture_without_its_reason() -> None:
    with pytest.raises(SystemExit):
        parse_record_weather_args(
            ["--scenario", "el-nino", "--from", "2016-12-14", "--days", "28", "--synthetic"]
        )
