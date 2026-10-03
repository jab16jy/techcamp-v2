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
    CellTransactions,
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


class _FailingPredictor:
    """A predictor double that raises on the call named by `fail_on_call`, so a
    failure lands partway through a run (#240 R3-one-cell-failure-aborts-run)."""

    def __init__(self, probability: float, *, fail_on_call: int) -> None:
        self._probability = probability
        self._fail_on_call = fail_on_call
        self.calls = 0

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        self.calls += 1
        if self.calls == self._fail_on_call:
            raise RuntimeError("the model artifact is corrupt")
        return PredictionOutcome(probability=self._probability, top_factors=[])


class FakeRiskRepository:
    """The risk rows double, with the uniqueness of
    `uq_risk_prediction_cell_event_month_version` (docs/03-modelo-datos.md).

    `insert_prediction` stores the row the way the real adapter does, by committing
    it: a rollback of the caller's transaction cannot undo it (#242
    R3-rollback-leaves-written-count-inflated)."""

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

    async def stored_predictions(
        self,
        *,
        horizon_start: date,
        cell_ids: Sequence[int],
        model_version_ids: Sequence[uuid.UUID],
    ) -> list[RiskPrediction]:
        return sorted(
            (
                row
                for row in self.rows
                if row.horizon_start == horizon_start
                and row.cell_id in cell_ids
                and row.model_version_id in model_version_ids
            ),
            key=lambda row: (row.cell_id, row.event_type.value),
        )


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
        unmeasured: date | None = None,
    ) -> None:
        self.requested: list[tuple[float, float, date, date]] = []
        self.elevation_requests: list[list[tuple[float, float]]] = []
        self._elevations = list(elevations)
        self._unavailable_for = set(unavailable_for)
        self._last_day = last_day
        self._unmeasured = unmeasured

    async def fetch_daily(
        self, lat: float, lon: float, *, start_day: date, end_day: date
    ) -> list[ArchiveDay]:
        self.requested.append((lat, lon, start_day, end_day))
        if (lat, lon) in self._unavailable_for:
            raise ArchiveUnavailableError("Open-Meteo archive is down")
        last = self._last_day or end_day
        return [
            ArchiveDay(
                day=day,
                # How the real adapter reports a day ERA5 has not aggregated: the
                # row is there and its measures are `None`
                # (`adapters/open_meteo_archive.py`, `_extract_float`).
                precipitation_mm=None if day == self._unmeasured else _PRECIPITATION_MM,
                soil_moisture_m3_m3=None if day == self._unmeasured else _SOIL_MOISTURE,
            )
            for day in _days(start_day, last)
        ]

    async def fetch_elevations(self, points: Sequence[tuple[float, float]]) -> list[float | None]:
        self.elevation_requests.append(list(points))
        if any(point in self._unavailable_for for point in points):
            raise ArchiveUnavailableError("Open-Meteo archive is down")
        return list(self._elevations)


class _FailingInsertRepository(FakeRiskRepository):
    """The rows double whose insert rejects one cell the way the table's CHECK
    would: a probability outside `[0, 1]` (docs/03-modelo-datos.md)."""

    def __init__(self, versions: Mapping[str, ModelVersion], *, fail_cell_id: int) -> None:
        super().__init__(versions)
        self._fail_cell_id = fail_cell_id

    async def insert_prediction(self, prediction: RiskPrediction) -> bool:
        if prediction.cell_id == self._fail_cell_id:
            raise ValueError("probability out of range")
        return await super().insert_prediction(prediction)


class _CountingVersions(FakeRiskRepository):
    """The rows double that counts how many times the served version was read."""

    def __init__(self, versions: Mapping[str, ModelVersion]) -> None:
        super().__init__(versions)
        self.reads = 0

    async def served_version(self, name: str) -> ModelVersion | None:
        self.reads += 1
        return await super().served_version(name)


class _RecordingTransactions:
    """The caller's transaction hook, recorded instead of performed.

    Structural, not a subclass: `CellTransactions` is a frozen slotted dataclass and
    subclassing one re-creates the class, which breaks `super()`.
    """

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


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
    transactions: CellTransactions | None = None,
):
    return await run_daily_risk(
        day=_DAY,
        cells=cells,
        versions=versions,
        archive=archive,
        predictors=predictors,
        transactions=transactions,
    )


def _every_event(
    versions: FakeRiskRepository,
    archive: FakeArchive,
    predictors: PredictorRegistry,
    transactions: CellTransactions | None = None,
):
    """The default arrangement: two cells, both events served and registered."""
    return _run(
        cells=[_cell(1), _cell(2)],
        versions=versions,
        archive=archive,
        predictors=predictors,
        transactions=transactions,
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


async def test_a_day_of_the_window_era5_has_not_measured_predicts_nothing() -> None:
    """The row for the last day of M-1 can be present and still carry no value: the
    T6a client reports a day ERA5 has not aggregated with `None` measures
    (`_extract_float`), so checking the row's existence is not enough. Without this
    guard every feature would come out `None`, a prediction would be written, and
    `ON CONFLICT DO NOTHING` would block the right one for the rest of the month
    (docs/06-diseno-detallado.md §8 "Datos de entrada"; docs/03 §Unicidad)."""
    versions = _both_events_served()
    archive = FakeArchive(unmeasured=_WINDOW[1])

    run = await _run(
        cells=[_cell(1)],
        versions=versions,
        archive=archive,
        predictors=_both_events_registered(),
    )

    assert versions.rows == []
    assert (run.written, run.skipped) == (0, 2)


async def test_a_day_measured_in_the_middle_of_the_window_predicts_nothing() -> None:
    """A missing day anywhere in the six months leaves every rainfall accumulation
    `None` (`domain.features._window_sum`), so the window is not computable at all."""
    versions = _both_events_served()
    archive = FakeArchive(unmeasured=date(2026, 6, 15))

    run = await _run(
        cells=[_cell(1)],
        versions=versions,
        archive=archive,
        predictors=_both_events_registered(),
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


async def test_the_neighbours_are_one_kilometre_away_where_training_places_them() -> None:
    """Train/serve parity (docs/08-ml.md §M2 "Features" and §Reglas de gobierno
    "Paridad de features"; docs/06-diseno-detallado.md §8): the training neighbours
    come from `ml/src/techcamp_ml/sources/elevation.py` (`neighbour_offsets`, lane
    `e10-t3`), which converts metres to degrees with 111_320.0 m per degree of
    latitude:

        d_lat = 1000 / 111_320             = 0.008983111749910169
        d_lon = 1000 / (111_320 * cos 10.9) = 0.009148156265391933

    The literals are pinned here on purpose, not recomputed from the constants: a
    different constant or a different formula at serving time would ask Open-Meteo
    for the elevation of another point, and `slope_deg` would be the slope of
    another slope than the one the model was trained on. The latitude step does not
    depend on the latitude; the longitude one does.
    """
    archive = FakeArchive()

    await _run(
        cells=[RiskCell(id=1, lat=10.9, lon=-74.1)],
        versions=FakeRiskRepository({"risk_flood": _version("risk_flood")}),
        archive=archive,
        predictors=_registry(("risk_flood", _RecordingPredictor(0.5))),
    )

    centre, east, west, north, south = archive.elevation_requests[0]
    assert north[0] - centre[0] == pytest.approx(0.008983111749910169)
    assert centre[0] - south[0] == pytest.approx(0.008983111749910169)
    assert east[1] - centre[1] == pytest.approx(0.009148156265391933)
    assert centre[1] - west[1] == pytest.approx(0.009148156265391933)

    archive_at_45 = FakeArchive()
    await _run(
        cells=[RiskCell(id=2, lat=45.0, lon=-74.1)],
        versions=FakeRiskRepository({"risk_flood": _version("risk_flood")}),
        archive=archive_at_45,
        predictors=_registry(("risk_flood", _RecordingPredictor(0.5))),
    )
    north_45, east_45 = (
        archive_at_45.elevation_requests[0][3],
        archive_at_45.elevation_requests[0][1],
    )
    assert north_45[0] - 45.0 == pytest.approx(0.008983111749910169)
    assert east_45[1] + 74.1 == pytest.approx(0.012704038469036066)


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


async def test_a_predictor_that_fails_partway_through_does_not_stop_the_run() -> None:
    """One cell's failure never cancels the rest of the run (docs/06-diseno-detallado.md
    §6 "Degradación"), and the cell it happened on stores nothing (#240
    R3-one-cell-failure-aborts-run: only ArchiveUnavailableError was contained, so a
    predictor raising left run_daily_risk entirely)."""
    versions = FakeRiskRepository({"risk_flood": _version("risk_flood")})
    predictor = _FailingPredictor(0.4, fail_on_call=2)

    run = await _run(
        cells=[_cell(1), _cell(2), _cell(3)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", predictor)),
    )

    assert predictor.calls == 3
    assert {row.cell_id for row in versions.rows} == {1, 3}
    assert run.written == 2


async def test_a_rejected_prediction_does_not_stop_the_run() -> None:
    """The same containment for the write: an insert the database rejects (a
    probability outside [0, 1] trips the CHECK of docs/03-modelo-datos.md) leaves
    that cell with nothing and the run with its other cells (#240
    R3-one-cell-failure-aborts-run)."""
    versions = _FailingInsertRepository({"risk_flood": _version("risk_flood")}, fail_cell_id=2)

    run = await _run(
        cells=[_cell(1), _cell(2), _cell(3)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", _RecordingPredictor(0.4))),
    )

    assert {row.cell_id for row in versions.rows} == {1, 3}
    assert run.written == 2


async def test_a_failed_cell_is_rolled_back_before_the_run_continues() -> None:
    """A rejected statement leaves the transaction aborted, so every later statement
    of it would fail too: the run has to undo the failed cell before moving on (#240
    R3-long-transaction-across-http)."""
    versions = _FailingInsertRepository({"risk_flood": _version("risk_flood")}, fail_cell_id=1)
    transactions = _RecordingTransactions()

    await _run(
        cells=[_cell(1), _cell(2)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", _RecordingPredictor(0.4))),
        transactions=transactions,
    )

    assert transactions.rollbacks == 1
    # One commit closed the read of the served versions, the other the surviving cell.
    assert transactions.commits == 2


async def test_a_cell_that_fails_after_it_stored_a_row_keeps_that_row_written() -> None:
    """#242 R3-rollback-leaves-written-count-inflated, the production direction:
    `SqlAlchemyRiskRepository.insert_prediction` commits every row of its own
    (`risk/adapters/repositories.py:163`), so the cell's rollback does NOT undo the
    flood row it stored before the drought predict raised. A stored row is written
    whether or not the cell went on to fail — dropping it from `written` reported a
    month with fewer predictions than the table holds.

    `EventType` walks flood first, so the cell stores its flood prediction and then
    fails on the drought one."""
    versions = FakeRiskRepository(
        {"risk_flood": _version("risk_flood"), "risk_drought": _version("risk_drought")}
    )
    transactions = _RecordingTransactions()

    run = await _run(
        cells=[_cell(1)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(
            ("risk_flood", _RecordingPredictor(0.82)),
            ("risk_drought", _FailingPredictor(0.2, fail_on_call=1)),
        ),
        transactions=transactions,
    )

    assert transactions.rollbacks == 1
    assert [(row.event_type, row.severity) for row in versions.rows] == [
        (EventType.FLOOD, Severity.HIGH)
    ]
    assert run.written == 1
    # The pair that raised produced nothing, and none of the cell's was stored
    # twice: `written` and `skipped` still add up to the pairs of the run.
    assert run.skipped == 1
    assert [row.event_type for row in run.predictions] == [EventType.FLOOD]


async def test_a_rerun_of_a_month_hands_over_the_rows_the_first_run_stored() -> None:
    """docs/06-diseno-detallado.md §8: la predicción de una celda, evento y mes se
    escribe una vez, so the runs after the first write nothing — and would hand the
    caller an empty list to evaluate, losing that month's alert. What the caller
    needs is the month, not the run's own insert, so the run reads the rows back
    from the store and both runs return the same ones."""
    versions = _both_events_served()
    archive = FakeArchive()
    predictors = _both_events_registered()

    first = await _every_event(versions=versions, archive=archive, predictors=predictors)
    second = await _every_event(versions=versions, archive=archive, predictors=predictors)

    assert first.written == 4
    assert second.written == 0
    assert second.skipped == 4
    assert [row.id for row in second.predictions] == [row.id for row in first.predictions]


async def test_a_rollback_that_itself_fails_does_not_abort_the_run() -> None:
    """A rollback on a dropped connection raises. The containment of docs/06 §6
    covers one cell's failure, and that includes failing to undo it: the cells
    after it still get predicted (#242 R3-rollback-leaves-written-count-inflated)."""

    class _BrokenTransactions(_RecordingTransactions):
        async def rollback(self) -> None:
            self.rollbacks += 1
            raise ConnectionError("the connection went away")

    versions = _FailingInsertRepository({"risk_flood": _version("risk_flood")}, fail_cell_id=1)
    transactions = _BrokenTransactions()

    run = await _run(
        cells=[_cell(1), _cell(2)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", _RecordingPredictor(0.4))),
        transactions=transactions,
    )

    assert transactions.rollbacks == 1
    assert {row.cell_id for row in versions.rows} == {2}
    assert run.written == 1


async def test_it_commits_after_every_cell() -> None:
    """Committing per cell is what limits a late failure to that cell: one
    transaction across the whole run would roll back every cell's predictions when
    the last one fails (#240 R3-long-transaction-across-http)."""
    transactions = _RecordingTransactions()

    run = await _every_event(
        versions=_both_events_served(),
        archive=FakeArchive(),
        predictors=_both_events_registered(),
        transactions=transactions,
    )

    # One commit closes the read the served versions were resolved in, before the
    # first provider call, plus one per cell.
    assert transactions.commits == 3
    assert transactions.rollbacks == 0
    assert run.written == 4
    # The rows the caller decides the alerts from are the ones the store holds,
    # which on a first run of a month is exactly what this run wrote (a rerun
    # stores nothing and still hands the stored rows over, asserted below).
    assert len(run.predictions) == run.written
    assert {row.cell_id for row in run.predictions} == {1, 2}


async def test_the_read_of_the_served_versions_is_closed_before_the_first_provider_call() -> None:
    """A `SELECT` autobegins a transaction, so resolving which version serves each
    event must not leave one open across `fetch_daily`: an open transaction pins a
    pooled connection for the length of the HTTP call and its retries
    (docs/09-cuellos-de-botella.md; #240 R3-long-transaction-across-http)."""
    events: list[str] = []

    class _OrderedVersions(FakeRiskRepository):
        async def served_version(self, name: str) -> ModelVersion | None:
            events.append(f"served_version:{name}")
            return await super().served_version(name)

    class _OrderedArchive(FakeArchive):
        async def fetch_daily(
            self, lat: float, lon: float, *, start_day: date, end_day: date
        ) -> list[ArchiveDay]:
            events.append("fetch_daily")
            return await super().fetch_daily(lat, lon, start_day=start_day, end_day=end_day)

    class _OrderedTransactions:
        async def commit(self) -> None:
            events.append("commit")

        async def rollback(self) -> None:
            events.append("rollback")

    await _run(
        cells=[_cell(1)],
        versions=_OrderedVersions({"risk_flood": _version("risk_flood")}),
        archive=_OrderedArchive(),
        predictors=_registry(("risk_flood", _RecordingPredictor(0.82))),
        transactions=_OrderedTransactions(),
    )

    first_provider_call = events.index("fetch_daily")
    # Negative: the version reads alone are not enough, a commit has to land first.
    assert "commit" in events[:first_provider_call]
    assert events.index("commit") < first_provider_call


async def test_the_served_versions_are_resolved_once_for_the_whole_run() -> None:
    """The served version and its predictor are properties of the event, not of the
    cell: resolving them per cell repeats the same read once per cell and per event
    for an answer that cannot differ."""
    versions = _CountingVersions({"risk_flood": _version("risk_flood")})

    await _run(
        cells=[_cell(1), _cell(2), _cell(3)],
        versions=versions,
        archive=FakeArchive(),
        predictors=_registry(("risk_flood", _RecordingPredictor(0.82))),
    )

    # One read per event, whatever the number of cells.
    assert versions.reads == len(EventType)


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
