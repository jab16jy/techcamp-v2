"""The daily climate-risk inference (docs/06-diseno-detallado.md §8; docs/08-ml.md
§M2 "Horizonte", "Severidad", "Features"; D-T0.3, D-T0.4, D-T0.6, D-T6b.1).

Pure application: the archive is a double, the rows are a double and the
predictor is a double, so what is pinned here is the rule the job has to follow
for every cell and event — the window it asks for, the features it builds, the
severity it reads from the *version's* thresholds, and the three ways a cell gets
no prediction at all (no registered version, no registered predictor, no archive
answer).

The repository double honours the same uniqueness as
`uq_risk_prediction_cell_event_month_version` (docs/03-modelo-datos.md
§Unicidad de la predicción); against real Postgres the job test (T6b) covers the
same idempotence with the real adapter.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from techcamp.risk.application.ports import (
    PredictionOutcome,
    PredictorRegistry,
    RiskCell,
)
from techcamp.risk.application.run_daily_risk import run_daily_risk
from techcamp.risk.domain.errors import ArchiveUnavailableError
from techcamp.risk.domain.models import (
    ArchiveDay,
    EventType,
    ModelVersion,
    RiskPrediction,
    Severity,
)

pytestmark = pytest.mark.anyio

_DAY = date(2026, 10, 2)
"""The day the job ran on; its month M is 2026-10."""

_ISSUE_MONTH = date(2026, 10, 1)
_WINDOW = (date(2026, 4, 1), date(2026, 9, 30))
"""The 6 months before M: April through September 2026."""

_PRECIPITATION_MM = 30.0
_SOIL_MOISTURE = 0.4
_ELEVATIONS = (615.0, 481.0, 684.0, 704.0, 330.0)
"""Centre, east, west, north, south ~1 km around it — T6a's recorded seminar
cell, reused so the slope expectation is hand-computable."""

_CREATED = datetime(2026, 10, 2, tzinfo=UTC)
_VERSION = "2026-10-02"


def _days(first_day: date, last_day: date) -> list[date]:
    return [first_day + timedelta(days=index) for index in range((last_day - first_day).days + 1)]


def _cell(cell_id: int) -> RiskCell:
    return RiskCell(id=cell_id, lat=10.9 + cell_id / 10, lon=-74.1)


def _version(
    name: str, *, thresholds: Mapping[str, float | None] | None = None, is_baseline: bool = False
) -> ModelVersion:
    return ModelVersion(
        id=uuid.uuid4(),
        name=name,
        version=_VERSION,
        artifact_uri="s3://models/risk.ubj",
        is_baseline=is_baseline,
        thresholds=dict(thresholds or {"high": 0.7, "critical": 0.85}),
        promoted=not is_baseline,
        created_at=_CREATED,
    )


class _RecordingPredictor:
    """A predictor double that records the version and the features it was called
    with and answers one probability."""

    def __init__(
        self, probability: float, *, top_factors: list[dict[str, Any]] | None = None
    ) -> None:
        self._probability = probability
        self._top_factors = top_factors or [
            {"feature": "precip_sum_1m", "value": 900.0, "contribution": 0.4}
        ]
        self.calls: list[tuple[ModelVersion, dict[str, float | None]]] = []

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        self.calls.append((version, dict(features)))
        return PredictionOutcome(probability=self._probability, top_factors=self._top_factors)


class FakeRiskRepository:
    """The risk rows double, with the uniqueness of
    `uq_risk_prediction_cell_event_month_version` (docs/03-modelo-datos.md)."""

    def __init__(self, versions: Mapping[str, ModelVersion]) -> None:
        self._versions = dict(versions)
        self.rows: list[RiskPrediction] = []

    async def served_version(self, name: str) -> ModelVersion | None:
        return self._versions.get(name)

    async def insert_prediction(self, prediction: RiskPrediction) -> bool:
        stored = (
            prediction.cell_id,
            prediction.event_type,
            prediction.horizon_start,
            prediction.model_version_id,
        )
        if any(
            (row.cell_id, row.event_type, row.horizon_start, row.model_version_id) == stored
            for row in self.rows
        ):
            return False
        self.rows.append(prediction)
        return True


class FakeArchive:
    """The Open-Meteo archive double (docs/06-diseno-detallado.md §8 "Datos de
    entrada"): it answers the window it is asked for, can stop short of it the
    way ERA5 does while it catches up, and can be unavailable for one cell."""

    def __init__(
        self,
        *,
        elevations: Sequence[float | None] = _ELEVATIONS,
        unavailable_for: set[tuple[float, float]] = frozenset(),
        last_day: date | None = None,
    ) -> None:
        self.requested: list[tuple[float, float, date, date]] = []
        self.elevation_requests: list[list[tuple[float, float]]] = []
        self._elevations = list(elevations)
        self._unavailable_for = set(unavailable_for)
        self._last_day = last_day

    async def fetch_daily(
        self, lat: float, lon: float, *, start_day: date, end_day: date
    ) -> list[ArchiveDay]:
        self.requested.append((lat, lon, start_day, end_day))
        if (lat, lon) in self._unavailable_for:
            raise ArchiveUnavailableError("Open-Meteo archive is down")
        last = self._last_day or end_day
        return [
            ArchiveDay(
                day=day, precipitation_mm=_PRECIPITATION_MM, soil_moisture_m3_m3=_SOIL_MOISTURE
            )
            for day in _days(start_day, last)
        ]

    async def fetch_elevations(self, points: Sequence[tuple[float, float]]) -> list[float | None]:
        self.elevation_requests.append(list(points))
        if any(point in self._unavailable_for for point in points):
            raise ArchiveUnavailableError("Open-Meteo archive is down")
        return list(self._elevations)


def _registry(*predictors: tuple[str, _RecordingPredictor]) -> PredictorRegistry:
    return PredictorRegistry({(name, _VERSION): predictor for name, predictor in predictors})


def _both_events_served() -> FakeRiskRepository:
    """A version registered and promoted for both events, so the only reason a
    test's cell gets no prediction is the one it is about."""
    return FakeRiskRepository(
        {"risk_flood": _version("risk_flood"), "risk_drought": _version("risk_drought")}
    )


def _both_events_registered() -> PredictorRegistry:
    return _registry(
        ("risk_flood", _RecordingPredictor(0.82)), ("risk_drought", _RecordingPredictor(0.2))
    )


async def _run(
    *,
    cells: Sequence[RiskCell],
    versions: FakeRiskRepository,
    archive: FakeArchive,
    predictors: PredictorRegistry,
):
    return await run_daily_risk(
        day=_DAY, cells=cells, versions=versions, archive=archive, predictors=predictors
    )


def _every_event(versions: FakeRiskRepository, archive: FakeArchive, predictors: PredictorRegistry):
    """The default arrangement: two cells, both events served and registered."""
    return _run(
        cells=[_cell(1), _cell(2)], versions=versions, archive=archive, predictors=predictors
    )


async def test_a_run_predicts_every_event_of_every_cell_it_is_given() -> None:
    versions = FakeRiskRepository(
        {"risk_flood": _version("risk_flood"), "risk_drought": _version("risk_drought")}
    )
    archive = FakeArchive()
    predictors = _registry(
        ("risk_flood", _RecordingPredictor(0.82)), ("risk_drought", _RecordingPredictor(0.2))
    )

    run = await _every_event(versions, archive, predictors)

    assert (run.issue_month, run.written, run.skipped) == (_ISSUE_MONTH, 4, 0)
    assert {(row.cell_id, row.event_type) for row in versions.rows} == {
        (1, EventType.FLOOD),
        (1, EventType.DROUGHT),
        (2, EventType.FLOOD),
        (2, EventType.DROUGHT),
    }
    assert {row.horizon_start for row in versions.rows} == {_ISSUE_MONTH}
    assert {row.horizon_days for row in versions.rows} == {31}
    # One window per cell, not per event: the archive does not depend on the event.
    assert [(start, end) for _, _, start, end in archive.requested] == [_WINDOW, _WINDOW]


async def test_the_severity_is_read_from_the_thresholds_of_the_served_version() -> None:
    """The same probability, two versions with different calibrations: `0.86` is
    `crítico` under `{high: 0.7, critical: 0.85}` and `bajo` under `{high: 0.9}`
    (docs/08-ml.md §M2 "Severidad"; the thresholds travel with the version,
    docs/03-modelo-datos.md §Umbrales)."""
    versions = FakeRiskRepository(
        {
            "risk_flood": _version("risk_flood", thresholds={"high": 0.7, "critical": 0.85}),
            "risk_drought": _version(
                "risk_drought", thresholds={"high": 0.9, "critical": None}, is_baseline=True
            ),
        }
    )
    predictors = _registry(
        ("risk_flood", _RecordingPredictor(0.86)), ("risk_drought", _RecordingPredictor(0.86))
    )

    await _every_event(versions, FakeArchive(), predictors)

    assert {row.event_type: row.severity for row in versions.rows} == {
        EventType.FLOOD: Severity.CRITICAL,
        EventType.DROUGHT: Severity.LOW,
    }


async def test_a_registered_baseline_serves_when_no_model_is_promoted() -> None:
    versions = FakeRiskRepository({"risk_flood": _version("risk_flood", is_baseline=True)})
    predictors = _registry(("risk_flood", _RecordingPredictor(0.75)))

    await _run(cells=[_cell(1)], versions=versions, archive=FakeArchive(), predictors=predictors)

    stored = versions.rows[0]
    assert stored.severity == Severity.HIGH
    assert versions.rows[0].model_version_id == versions._versions["risk_flood"].id


async def test_an_event_with_no_registered_version_is_not_predicted() -> None:
    """docs/06-diseno-detallado.md §8 "Sin modelo promovido": sin ninguna versión
    registrada para un evento, el job no escribe predicciones de ese evento."""
    versions = FakeRiskRepository({"risk_flood": _version("risk_flood")})
    archive = FakeArchive()
    predictors = _registry(
        ("risk_flood", _RecordingPredictor(0.82)), ("risk_drought", _RecordingPredictor(0.2))
    )

    run = await _every_event(versions, archive, predictors)

    assert [row.event_type for row in versions.rows] == [EventType.FLOOD, EventType.FLOOD]
    assert (run.written, run.skipped) == (2, 2)


async def test_a_served_version_with_no_registered_predictor_is_not_predicted() -> None:
    """No predictor is not a reason to invent a probability: nothing is written
    for that event (docs/06-diseno-detallado.md §8)."""
    versions = FakeRiskRepository(
        {"risk_flood": _version("risk_flood"), "risk_drought": _version("risk_drought")}
    )
    predictors = _registry(("risk_flood", _RecordingPredictor(0.82)))

    run = await _every_event(versions, FakeArchive(), predictors)

    assert [row.event_type for row in versions.rows] == [EventType.FLOOD, EventType.FLOOD]
    assert (run.written, run.skipped) == (2, 2)


async def test_an_archive_outage_skips_that_cell_and_the_run_goes_on() -> None:
    down = _cell(1)
    versions = _both_events_served()
    archive = FakeArchive(unavailable_for={(down.lat, down.lon)})

    run = await _every_event(versions, archive, _both_events_registered())

    assert {row.cell_id for row in versions.rows} == {2}
    assert (run.written, run.skipped) == (2, 2)


async def test_a_cell_nothing_can_be_predicted_for_costs_no_provider_call() -> None:
    versions = FakeRiskRepository({})
    archive = FakeArchive()

    run = await _run(cells=[_cell(1)], versions=versions, archive=archive, predictors=_registry())

    assert archive.requested == []
    assert (run.written, run.skipped) == (0, 2)


async def test_an_archive_that_reached_the_last_day_of_the_previous_month_predicts() -> None:
    versions = FakeRiskRepository({"risk_flood": _version("risk_flood")})
    archive = FakeArchive(last_day=_WINDOW[1])

    run = await _run(
        cells=[_cell(1)],
        versions=versions,
        archive=archive,
        predictors=_registry(("risk_flood", _RecordingPredictor(0.82))),
    )

    assert run.written == 1


async def test_an_archive_still_covering_m_minus_one_lag_predicts_nothing() -> None:
    """ERA5 arrives with ~5 days of delay, so for the first days of M its window
    stops short of M-1: the cell has no prediction for the new month and keeps
    the one it has (docs/06-diseno-detallado.md §8 "Datos de entrada")."""
    versions = _both_events_served()
    archive = FakeArchive(last_day=date(2026, 9, 25))

    run = await _run(
        cells=[_cell(1)], versions=versions, archive=archive, predictors=_both_events_registered()
    )

    assert versions.rows == []
    assert (run.written, run.skipped) == (0, 2)


async def test_the_predictor_receives_the_six_month_window_and_the_cells_terrain() -> None:
    predictor = _RecordingPredictor(0.5)
    archive = FakeArchive()
    cell = _cell(1)

    await _run(
        cells=[cell],
        versions=FakeRiskRepository({"risk_flood": _version("risk_flood")}),
        archive=archive,
        predictors=_registry(("risk_flood", predictor)),
    )

    _, features = predictor.calls[0]
    assert features["precip_sum_1m"] == pytest.approx(_PRECIPITATION_MM * 30)
    assert features["precip_sum_6m"] == pytest.approx(
        _PRECIPITATION_MM * (30 + 31 + 30 + 31 + 31 + 30)
    )
    assert features["soil_moisture_mean_1m"] == pytest.approx(_SOIL_MOISTURE)
    assert features["elevation_m"] == _ELEVATIONS[0]
    assert features["slope_deg"] == pytest.approx(
        math.degrees(
            math.atan(
                math.hypot(
                    (_ELEVATIONS[1] - _ELEVATIONS[2]) / 2000,
                    (_ELEVATIONS[3] - _ELEVATIONS[4]) / 2000,
                )
            )
        )
    )
    assert features["month_sin"] == pytest.approx(math.sin(2 * math.pi * 9 / 12))
    # Centre first, then the four neighbours ~1 km away in the order
    # `domain.features.Neighbours` reads them.
    centre, east, west, north, south = archive.elevation_requests[0]
    assert centre == (cell.lat, cell.lon)
    assert east[1] > centre[1] and west[1] < centre[1]
    assert north[0] > centre[0] and south[0] < centre[0]
    assert north[1] == centre[1] and east[0] == centre[0]


async def test_a_neighbour_without_an_elevation_leaves_the_slope_missing() -> None:
    predictor = _RecordingPredictor(0.5)
    archive = FakeArchive(elevations=(_ELEVATIONS[0], None, *_ELEVATIONS[2:]))

    await _run(
        cells=[_cell(1)],
        versions=FakeRiskRepository({"risk_flood": _version("risk_flood")}),
        archive=archive,
        predictors=_registry(("risk_flood", predictor)),
    )

    _, features = predictor.calls[0]
    assert features["slope_deg"] is None
    assert features["elevation_m"] == _ELEVATIONS[0]


async def test_the_anomaly_features_are_missing_until_the_climatology_is_wired() -> None:
    """D-T6b.1: no doc says where the served version's train years live, so T6b
    builds the features without a climatology and the anomaly features are
    missing evidence — never a zero anomaly. T9 wires it with the real predictors
    (docs/08-ml.md §M2 "Features": anomalies against the train climatology)."""
    predictor = _RecordingPredictor(0.5)

    await _run(
        cells=[_cell(1)],
        versions=FakeRiskRepository({"risk_flood": _version("risk_flood")}),
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", predictor)),
    )

    _, features = predictor.calls[0]
    assert features["precip_anomaly_1m"] is None
    assert features["precip_anomaly_6m"] is None
    assert features["precip_sum_1m"] == pytest.approx(_PRECIPITATION_MM * 30)


async def test_a_second_run_of_the_same_month_writes_nothing_new() -> None:
    versions = FakeRiskRepository({"risk_flood": _version("risk_flood")})
    predictors = _registry(("risk_flood", _RecordingPredictor(0.82)))
    cells = [_cell(1)]

    first = await _run(cells=cells, versions=versions, archive=FakeArchive(), predictors=predictors)
    second = await _run(
        cells=cells, versions=versions, archive=FakeArchive(), predictors=predictors
    )

    assert (first.written, second.written) == (1, 0)
    assert len(versions.rows) == 1
