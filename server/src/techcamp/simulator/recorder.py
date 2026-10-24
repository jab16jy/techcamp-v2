"""E16 T3: `sim record-weather` — the recording of a scenario's weather
fixture (docs/06-diseno-detallado.md:490; D-T16.3).

Record once, then offline. The capture reads Open-Meteo's archive for the
scenario's cell and leaves a fixture the scenario library's contract accepts
(`techcamp.simulator.scenario_library.parse_fixture`); the demo and CI then
read that file and never open a socket (docs/06:489).

The shape is the contract T1 wrote down, so this module produces it instead of
inventing one:

- the envelope carries exactly the contract's keys — `synthetic_reason` only
  when `synthetic` is true;
- the rows are the variables the balance reads, one per day, keyed by the
  scenario's day offset (docs/06:490), so the loader can place them on the
  scenario's days whatever calendar dates the capture covered;
- `data_sha256` is the contract's canonical digest over the row array, so an
  edited measure cannot pass unnoticed on either side.

Recording is the default. A synthetic capture exists only behind an explicit
flag that carries its reason (D-T16.3): it is the fallback for a window whose
real weather does not reproduce the scenario's arithmetic (ETc ≈ 5 mm/día /
ET0 ≈ 4,2 mm/día — docs/06:442-471).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

from techcamp.weather.domain.models import cell_for

from .scenario_library import FORECAST_DAYS, SCENARIOS_DIR, canonical_days_sha256, file_sha256

ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
"""The archive the first capture read; the format mirrors the source URL the
library's recorded fixtures already carry (docs/06:490)."""

RECORD_COMMAND = "record-weather"
"""The subcommand that records the fixture (`sim record-weather`, D-T16.3)."""

DAILY_VARIABLES = (
    "et0_fao_evapotranspiration",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "relative_humidity_2m_mean",
)
"""The daily variables the balance reads: ET0, the temperature extremes, the
rain and the humidity (docs/06:246)."""

TIMEZONE = "America/Bogota"
"""The timezone the archive is asked for, so the returned days are the
scenario's local days (docs/03-modelo-datos.md)."""

MIN_RECORDED_DAYS = FORECAST_DAYS + 1
"""One observed day plus the seven-day forecast window the recommendation
reads (docs/06:246)."""

DEFAULT_SYNTHETIC_ET0_MM = 4.2
"""The Caribe cell's ET0 (docs/06:458)."""

_SYNTHETIC_RAIN_MM = 0.0
"""No rain, so Pe = 0 and the depletion only grows — the veranillo arithmetic
(docs/06:462)."""

_SYNTHETIC_TMIN_C = 24.0
_SYNTHETIC_TMAX_C = 29.0
_SYNTHETIC_RH_MEAN_PCT = 70.0
"""A benign window: the mean temperature stays inside the 20-30 °C band and
the humidity below the 85 % the fungal-risk rule reads (docs/06:129)."""

ENVELOPE_KEY_ORDER = (
    "version",
    "name",
    "scenario",
    "cell",
    "timezone",
    "source_url",
    "recorded_at",
    "synthetic",
    "synthetic_reason",
    "data_sha256",
)
"""The envelope's key order, the one the library's recorded files use."""


class RecorderError(Exception):
    """The capture cannot become a fixture."""


@dataclass(frozen=True, slots=True)
class RecordedFixture:
    """What a recording left behind: the file, its digests and its envelope."""

    name: str
    path: Path
    data_sha256: str
    file_sha256: str
    envelope: dict[str, Any]


def archive_url(*, lat: float, lon: float, from_day: date, days: int) -> str:
    """The archive URL for a capture, in the format the library's recorded
    fixtures already carry."""
    end_day = from_day + timedelta(days=days - 1)
    return (
        f"{ARCHIVE_ENDPOINT}?latitude={lat:.2f}&longitude={lon:.2f}"
        f"&start_date={from_day.isoformat()}&end_date={end_day.isoformat()}"
        f"&daily={','.join(DAILY_VARIABLES)}&timezone={TIMEZONE.replace('/', '%2F')}"
    )


def scenario_fixture_name(scenario: str) -> str:
    """The fixture a scenario pins, without its extension.

    Read from the YAML instead of the loaded contract on purpose: a recording
    replaces the file and breaks the pin the YAML carries, and the next
    recording still has to find its way to the same file.
    """
    path = SCENARIOS_DIR / f"{scenario}.yaml"
    if not path.exists():
        raise RecorderError(f"unknown scenario {scenario!r}: {path.name} is not in the library")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    fixture = raw.get("weather_fixture") if isinstance(raw, dict) else None
    if not isinstance(fixture, str) or not fixture.endswith(".json"):
        raise RecorderError(f"{path.name}: no weather_fixture to record")
    return fixture.removesuffix(".json")


def fixture_cell(name: str) -> tuple[float, float]:
    """The cell a recorded fixture names: the point a re-recording asks about."""
    path = SCENARIOS_DIR / "weather" / f"{name}.json"
    if not path.exists():
        raise RecorderError(f"no recorded fixture named {name!r} to read a cell from")
    raw = json.loads(path.read_text(encoding="utf-8"))
    cell = raw.get("cell")
    if not isinstance(cell, dict) or "lat" not in cell or "lon" not in cell:
        raise RecorderError(f"{path.name}: no cell to read")
    return float(cell["lat"]), float(cell["lon"])


def _measure(daily: Mapping[str, Any], variable: str, offset: int, day: str) -> float:
    """One measure of the capture, rounded to the fixture's precision.

    A measure the provider did not report is refused instead of becoming a
    zero: the contract wants a real float, and a silent zero is a lie the
    balance would believe.
    """
    values = daily.get(variable)
    if not isinstance(values, list) or len(values) <= offset:
        raise RecorderError(f"the capture has no {variable} for {day}")
    value = values[offset]
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise RecorderError(f"the capture has no {variable} for {day}")
    return round(float(value), 2)


def parse_daily_payload(
    payload: Mapping[str, Any], *, from_day: date, days: int
) -> list[dict[str, Any]]:
    """The archive's daily block as the contract's rows, one per day."""
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise RecorderError("the capture carries no daily block")
    times = daily.get("time")
    if not isinstance(times, list):
        raise RecorderError("the capture carries no day list")
    if len(times) != days:
        raise RecorderError(f"the capture covers {len(times)} day(s), the request asked for {days}")

    rows: list[dict[str, Any]] = []
    for offset, day in enumerate(times):
        expected = (from_day + timedelta(days=offset)).isoformat()
        if day != expected:
            raise RecorderError(
                f"the capture's day {offset} is {day}, the request asked for {expected}"
            )
        rows.append(
            {
                "day": day,
                "day_offset": offset,
                "et0_mm": _measure(daily, "et0_fao_evapotranspiration", offset, day),
                "is_forecast": offset >= days - FORECAST_DAYS,
                "rain_mm": _measure(daily, "precipitation_sum", offset, day),
                "rh_mean_pct": _measure(daily, "relative_humidity_2m_mean", offset, day),
                "tmax_c": _measure(daily, "temperature_2m_max", offset, day),
                "tmin_c": _measure(daily, "temperature_2m_min", offset, day),
            }
        )
    return rows


def synthetic_rows(*, from_day: date, days: int, et0_mm: float) -> list[dict[str, Any]]:
    """The fallback series: the documented ET0 every day, no rain and a benign
    humidity, so the scenario's arithmetic (ETc ≈ 5 mm/día with ET0 ≈ 4,2) is
    the one the balance reproduces (docs/06:458)."""
    return [
        {
            "day": (from_day + timedelta(days=offset)).isoformat(),
            "day_offset": offset,
            "et0_mm": et0_mm,
            "is_forecast": offset >= days - FORECAST_DAYS,
            "rain_mm": _SYNTHETIC_RAIN_MM,
            "rh_mean_pct": _SYNTHETIC_RH_MEAN_PCT,
            "tmax_c": _SYNTHETIC_TMAX_C,
            "tmin_c": _SYNTHETIC_TMIN_C,
        }
        for offset in range(days)
    ]


def build_envelope(
    *,
    name: str,
    scenario: str,
    lat: float,
    lon: float,
    source_url: str,
    recorded_at: datetime,
    rows: list[dict[str, Any]],
    synthetic: bool,
    synthetic_reason: str | None,
) -> dict[str, Any]:
    """The envelope: the contract's keys and the canonical digest over the rows."""
    cell_lat, cell_lon = cell_for(lat, lon)
    envelope: dict[str, Any] = {
        "version": 1,
        "name": name,
        "scenario": scenario,
        "cell": {"lat": float(cell_lat), "lon": float(cell_lon)},
        "timezone": TIMEZONE,
        "source_url": source_url,
        "recorded_at": recorded_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic": synthetic,
    }
    if synthetic:
        envelope["synthetic_reason"] = synthetic_reason
    envelope["data_sha256"] = canonical_days_sha256(rows)
    envelope["days"] = rows
    return envelope


def dump_fixture(envelope: Mapping[str, Any]) -> str:
    """The file's text, in the library's format: the envelope's keys in order
    and one compact row per line."""
    lines = ["{"]
    for key in ENVELOPE_KEY_ORDER:
        if key not in envelope:
            continue
        value = envelope[key]
        if key == "cell":
            lines.append('  "cell": {')
            lines.append(f'    "lat": {json.dumps(value["lat"])},')
            lines.append(f'    "lon": {json.dumps(value["lon"])}')
            lines.append("  },")
        else:
            lines.append(f"  {json.dumps(key)}: {json.dumps(value)},")
    lines.append('  "days": [')
    rows = list(envelope["days"])
    for index, row in enumerate(rows):
        suffix = "," if index < len(rows) - 1 else ""
        lines.append(f"    {json.dumps(row, sort_keys=True, separators=(',', ':'))}{suffix}")
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_fixture(path: Path, envelope: Mapping[str, Any]) -> RecordedFixture:
    """Write the fixture and report what it left behind."""
    path.write_text(dump_fixture(envelope), encoding="utf-8")
    return RecordedFixture(
        name=str(envelope["name"]),
        path=path,
        data_sha256=str(envelope["data_sha256"]),
        file_sha256=file_sha256(path),
        envelope=dict(envelope),
    )


async def _capture(url: str, client: httpx.AsyncClient | None) -> Mapping[str, Any]:
    """The archive's answer for the URL, or the reason it is not one."""
    if client is None:
        async with httpx.AsyncClient() as owned:
            response = await owned.get(url)
    else:
        response = await client.get(url)
    if response.status_code != 200:
        raise RecorderError(f"the archive answered {response.status_code} for the capture")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RecorderError("the archive answered something that is not a JSON object")
    return payload


async def record_weather(
    *,
    scenario: str,
    name: str,
    from_day: date,
    days: int,
    lat: float,
    lon: float,
    out_dir: Path,
    client: httpx.AsyncClient | None = None,
    synthetic: bool = False,
    synthetic_reason: str | None = None,
    recorded_at: datetime | None = None,
    synthetic_et0_mm: float = DEFAULT_SYNTHETIC_ET0_MM,
) -> RecordedFixture:
    """Capture the window and write the fixture the contract accepts."""
    if days < MIN_RECORDED_DAYS:
        raise RecorderError(
            f"a capture must cover at least {MIN_RECORDED_DAYS} days "
            f"(one observed plus the {FORECAST_DAYS}-day forecast window), got {days}"
        )
    if synthetic and not (synthetic_reason or "").strip():
        raise RecorderError("a synthetic capture must carry the reason it could not be recorded")

    url = archive_url(lat=lat, lon=lon, from_day=from_day, days=days)
    if synthetic:
        rows = synthetic_rows(from_day=from_day, days=days, et0_mm=synthetic_et0_mm)
    else:
        rows = parse_daily_payload(await _capture(url, client), from_day=from_day, days=days)

    envelope = build_envelope(
        name=name,
        scenario=scenario,
        lat=lat,
        lon=lon,
        source_url=url,
        recorded_at=recorded_at or datetime.now(tz=UTC),
        rows=rows,
        synthetic=synthetic,
        synthetic_reason=synthetic_reason,
    )
    return write_fixture(out_dir / f"{name}.json", envelope)
