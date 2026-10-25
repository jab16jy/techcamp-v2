"""E16 T1: the recorded weather fixtures' executable contract (D-T16.3).

The scenario library is data, not code: six scenario YAMLs plus the weather
fixtures they point at, living beside the simulator (D-T16.1,
`docs/06-diseno-detallado.md:421`). This module writes the *shape* of a weather
fixture down as code so the suite can enforce it before the runner (T2) and the
`/dev/scenarios/{name}:load` loader (T3) have to satisfy it.

Two properties from `docs/06-diseno-detallado.md:490` drive the design:

- **Record once, then offline.** `source_url` and `recorded_at` say where the
  numbers came from; the demo then runs from the file alone. Nothing here opens a
  socket (see `test_weather_fixtures.py`).
- **Versioned and pinned.** `data_sha256` covers the row array and the scenario
  YAML carries `weather_fixture_sha256` over the file's bytes, so an edited
  fixture cannot pass unnoticed by either its own contract or its consumer.

The fixture is deliberately not an ORM object: `WeatherDay`
(`techcamp.weather.domain.models`) is T3's write path, and importing it would
make a pure data contract depend on the adapter layer. Its row fields are exactly
the nullable columns of `weather_daily` (`docs/03-modelo-datos.md:181`), which is
what lets T3 write them through `upsert_daily` unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = SERVER_DIR / "src" / "techcamp" / "simulator" / "scenarios"
WEATHER_DIR = SCENARIOS_DIR / "weather"

SCENARIO_NAMES: tuple[str, ...] = (
    "el-nino",
    "rainy-season",
    "node-failure",
    "offline-farmer",
    "dry-spell-rainfed",
    "normal",
)
"""The six scenarios of `docs/06-diseno-detallado.md:473-480`, in table order."""

FORECAST_DAYS = 7
"""The recommendation reads the forecast window `[D, D+6]` (docs/06:246)."""

ENVELOPE_KEYS: frozenset[str] = frozenset(
    {
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
        "days",
    }
)
REQUIRED_ENVELOPE_KEYS: frozenset[str] = ENVELOPE_KEYS - {"synthetic_reason"}
"""Every envelope key except `synthetic_reason`, which a recording that never
needed the synthetic flag must not have to carry (D-T16.3)."""

DAY_KEYS: frozenset[str] = frozenset(
    {"day_offset", "day", "is_forecast", "et0_mm", "rain_mm", "tmin_c", "tmax_c", "rh_mean_pct"}
)
"""`weather_daily`'s nullable measures plus the day offset and the forecast flag
(docs/03-modelo-datos.md:181; docs/06:490, daily rows keyed by day-offset)."""

ONE_DAY = timedelta(days=1)


class FixtureSchemaError(ValueError):
    """A weather fixture does not satisfy the recorded-fixture contract."""


@dataclass(frozen=True, slots=True)
class FixtureDay:
    """One daily row: a scenario day-offset, its calendar day, and the measures."""

    day_offset: int
    day: date
    is_forecast: bool
    et0_mm: float
    rain_mm: float
    tmin_c: float
    tmax_c: float
    rh_mean_pct: float

    @property
    def mean_daily_temp_c(self) -> float:
        """`(tmin_c + tmax_c) / 2`: the thermal half of `fungal_risk` (docs/06:129).

        Both extremes are stored, so unlike the alert's own evaluation this is
        never a three-state "not said" (docs/06:137).
        """
        return (self.tmin_c + self.tmax_c) / 2.0

    @property
    def meets_fungal_risk_conditions(self) -> bool:
        """Whether the row satisfies both halves of `fungal_risk` (docs/06:129)."""
        return self.rh_mean_pct > 85.0 and 20.0 <= self.mean_daily_temp_c <= 30.0

    @property
    def effective_rain_mm(self) -> float:
        """Effective rain `Pe`: `0.8 * P` above 5 mm, else 0 (docs/06:280)."""
        return 0.8 * self.rain_mm if self.rain_mm > 5.0 else 0.0


@dataclass(frozen=True, slots=True)
class WeatherFixture:
    """A parsed, validated weather fixture."""

    name: str
    scenario: str
    version: int
    synthetic: bool
    source_url: str
    recorded_at: datetime
    timezone: str
    lat: float
    lon: float
    data_sha256: str
    days: tuple[FixtureDay, ...]

    @property
    def observed(self) -> tuple[FixtureDay, ...]:
        """Rows the daily job consolidates as observations (`is_forecast` false)."""
        return tuple(day for day in self.days if not day.is_forecast)

    @property
    def forecast(self) -> tuple[FixtureDay, ...]:
        """Rows the 3-hourly job refreshes (`is_forecast` true)."""
        return tuple(day for day in self.days if day.is_forecast)


def canonical_days_sha256(days: list[dict[str, Any]]) -> str:
    """SHA-256 over the canonical JSON of the row array.

    `sort_keys` and compact separators make the digest independent of key order
    and whitespace, so reformatting the envelope cannot change it while editing a
    measured value always does.
    """
    payload = json.dumps(days, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """SHA-256 over a file's exact bytes: what `weather_fixture_sha256` pins."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FixtureSchemaError(message)


def _require_keys(raw: dict[str, Any], expected: frozenset[str], where: str) -> None:
    missing = expected - raw.keys()
    unknown = raw.keys() - expected
    _require(not missing, f"{where}: missing key(s) {sorted(missing)}")
    _require(not unknown, f"{where}: unknown key(s) {sorted(unknown)}")


def _measure(value: Any, where: str, *, minimum: float | None = None) -> float:
    """A weather measure: a real float, never an int or a bool smuggled in as one."""
    _require(
        isinstance(value, float) and not isinstance(value, bool),
        f"{where}: expected a float, got {type(value).__name__} ({value!r})",
    )
    assert isinstance(value, float)
    if minimum is not None:
        _require(value >= minimum, f"{where}: {value} is below {minimum}")
    return value


def _parse_day(index: int, row: Any, where: str) -> FixtureDay:
    _require(isinstance(row, dict), f"{where}: must be an object")
    assert isinstance(row, dict)
    _require_keys(row, DAY_KEYS, where)
    _require(row["day_offset"] == index, f"{where}: day_offset must count from 0 in order")
    _require(isinstance(row["is_forecast"], bool), f"{where}: is_forecast must be a boolean")
    day = FixtureDay(
        day_offset=row["day_offset"],
        day=date.fromisoformat(row["day"]),
        is_forecast=row["is_forecast"],
        et0_mm=_measure(row["et0_mm"], f"{where}.et0_mm", minimum=0.0),
        rain_mm=_measure(row["rain_mm"], f"{where}.rain_mm", minimum=0.0),
        tmin_c=_measure(row["tmin_c"], f"{where}.tmin_c"),
        tmax_c=_measure(row["tmax_c"], f"{where}.tmax_c"),
        rh_mean_pct=_measure(row["rh_mean_pct"], f"{where}.rh_mean_pct", minimum=0.0),
    )
    _require(day.tmin_c <= day.tmax_c, f"{where}: tmin_c above tmax_c")
    _require(day.rh_mean_pct <= 100.0, f"{where}: rh_mean_pct above 100")
    return day


def _parse_envelope(name: str, raw: Any, where: str) -> dict[str, Any]:
    _require_keys(raw, REQUIRED_ENVELOPE_KEYS, where)
    _require(raw["name"] == name.removesuffix(".json"), f"{where}: name must match the filename")
    _require(raw["scenario"] in SCENARIO_NAMES, f"{where}: unknown scenario {raw['scenario']!r}")
    _require(raw["version"] == 1, f"{where}: unsupported version {raw['version']!r}")

    cell = raw["cell"]
    _require(isinstance(cell, dict), f"{where}: cell must be an object")
    assert isinstance(cell, dict)
    _require_keys(cell, frozenset({"lat", "lon"}), f"{where}.cell")
    _require(-90.0 <= cell["lat"] <= 90.0, f"{where}: cell latitude out of range")
    _require(-180.0 <= cell["lon"] <= 180.0, f"{where}: cell longitude out of range")

    _require(
        isinstance(raw["source_url"], str) and raw["source_url"].startswith("https://"),
        f"{where}: source_url must be the https URL the rows were recorded from",
    )
    _require(
        isinstance(raw["recorded_at"], str) and raw["recorded_at"].endswith("Z"),
        f"{where}: recorded_at must be a UTC instant ending in Z",
    )
    _require(
        isinstance(raw["timezone"], str) and raw["timezone"] == "America/Bogota",
        f"{where}: timezone must be America/Bogota, the cell's local day (docs/06:249)",
    )
    # D-T16.3: synthetic is a last resort, allowed only behind an explicit flag
    # carrying the reason the recording could not reproduce the arithmetic.
    _require(isinstance(raw["synthetic"], bool), f"{where}: synthetic must be a boolean")
    _require(
        raw["synthetic"] == ("synthetic_reason" in raw),
        f"{where}: synthetic_reason must be present exactly when synthetic is true",
    )
    _require(isinstance(raw["days"], list) and raw["days"], f"{where}: days must be non-empty")
    return raw


def parse_fixture(name: str, raw: Any) -> WeatherFixture:
    """Validate one fixture's parsed JSON and return it.

    Raises:
        FixtureSchemaError: on any shape, range or provenance violation, with a
            message naming the offending row so a broken recording is cheap to fix.
    """
    where = f"{name}.json"
    _require(isinstance(raw, dict), f"{where}: top level must be an object")
    assert isinstance(raw, dict)
    envelope = _parse_envelope(name, raw, where)
    raw_days = envelope["days"]
    assert isinstance(raw_days, list)

    days: list[FixtureDay] = []
    for index, row in enumerate(raw_days):
        day = _parse_day(index, row, f"{where}.days[{index}]")
        if days:
            _require(
                day.day == days[-1].day + ONE_DAY,
                f"{where}.days[{index}]: day must follow the previous row's calendar day",
            )
        days.append(day)

    _require(
        not days[0].is_forecast,
        f"{where}: the first row must be observed; the balance starts on scenario day 0",
    )
    forecast_rows = [day for day in days if day.is_forecast]
    _require(
        len(forecast_rows) == FORECAST_DAYS,
        f"{where}: {len(forecast_rows)} forecast rows, expected {FORECAST_DAYS} (docs/06:246)",
    )
    _require(
        days[-FORECAST_DAYS:][0].is_forecast,
        f"{where}: observed rows must precede the {FORECAST_DAYS}-day forecast block",
    )
    _require(
        envelope["data_sha256"] == canonical_days_sha256(raw_days),
        f"{where}: data_sha256 does not match the rows (a recorded value was edited)",
    )

    return WeatherFixture(
        name=envelope["name"],
        scenario=envelope["scenario"],
        version=envelope["version"],
        synthetic=envelope["synthetic"],
        source_url=envelope["source_url"],
        recorded_at=datetime.fromisoformat(envelope["recorded_at"].removesuffix("Z") + "+00:00"),
        timezone=envelope["timezone"],
        lat=envelope["cell"]["lat"],
        lon=envelope["cell"]["lon"],
        data_sha256=envelope["data_sha256"],
        days=tuple(days),
    )


def load_fixture(name: str) -> WeatherFixture:
    """Read and validate the fixture named `<name>.json` from the library."""
    path = WEATHER_DIR / f"{name}.json"
    return parse_fixture(name, json.loads(path.read_text(encoding="utf-8")))
