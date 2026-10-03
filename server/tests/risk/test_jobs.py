"""The daily risk job and its seminar-only trigger (E10 T6b, docs/06-diseno-detallado.md
§8, docs/04-api.md §Solo perfil seminario (`/dev`), ADR-0012, ADR-0021; D-T6b.2).

Against real Postgres, because the rows are written by the real repository: a
rerun's idempotence is `uq_risk_prediction_cell_event_month_version` (docs/03
§Unicidad de la predicción) and not a double's memory. The archive is T6a's own
seminar adapter replaying its recorded responses (ADR-0021), and the predictor is
the double T9 replaces with the promoted model's.
"""

from __future__ import annotations

import datetime
import importlib
import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import techcamp.risk.adapters.api.dev_jobs as dev_jobs_module
import techcamp.risk.adapters.jobs as jobs_module
from techcamp.alerts.adapters.evaluate_risk import build_risk_evaluation
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.application import PredictionEvidence
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.main import app
from techcamp.risk.adapters.jobs import (
    QUEUE_NAME,
    RUN_ACTIVE_CELLS_TASK_NAME,
    AlertEvaluationFactory,
    predict_active_cells,
)
from techcamp.risk.adapters.open_meteo_archive import seminar_archive_adapter
from techcamp.risk.adapters.orm import ModelVersionRow
from techcamp.risk.application.ports import PredictionOutcome, PredictorRegistry
from techcamp.risk.domain.models import ModelVersion
from techcamp.shared.ids import uuid7
from techcamp.shared.jobs import app as jobs_app
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

pytestmark = pytest.mark.anyio

_ROUTE = "/dev/jobs/risk:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/risk:run"
_FROZEN_TODAY = datetime.date(2026, 10, 2)
"""The day every test in this module believes it is.

No test here may depend on the wall clock: `local_today()` moves the job's
six-month window, the default day of the route and the future-day `422` with it,
and the recorded responses of the seminar adapter (ADR-0021) are only guaranteed for
the window they were recorded over (#240 R3-wall-clock-dependent-job-tests). The
clock is frozen per module, so a test on 2026-10-02 and the same test in December are
the same test.
"""

_DAY = _FROZEN_TODAY
_VERSION = "2026-10-02"
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze "today" in the two modules that read it (#240
    R3-wall-clock-dependent-job-tests)."""
    monkeypatch.setattr(jobs_module, "local_today", lambda: _FROZEN_TODAY)
    monkeypatch.setattr(dev_jobs_module, "local_today", lambda: _FROZEN_TODAY)
    _EVALUATED.clear()


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """The route defers through procrastinate's own psycopg connector, on a
    connection this session cannot clean up through (same as the weather and
    irrigation route tests)."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


class _OutOfRangePredictor:
    """Answers a probability outside `[0, 1]` on one call, which the CHECK of
    `risk_prediction` rejects (docs/03-modelo-datos.md)."""

    def __init__(self, probability: float, *, out_of_range_on_call: int) -> None:
        self._probability = probability
        self._out_of_range_on_call = out_of_range_on_call
        self.calls = 0

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        self.calls += 1
        probability = 1.5 if self.calls == self._out_of_range_on_call else self._probability
        return PredictionOutcome(probability=probability, top_factors=[])


class _FixedPredictor:
    """The predictor double: one probability for every version and cell."""

    def __init__(self, probability: float) -> None:
        self._probability = probability

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        return PredictionOutcome(
            probability=self._probability,
            top_factors=[{"feature": "precip_sum_1m", "value": 900.0, "contribution": 0.4}],
        )


_EVALUATED: list[tuple[datetime.datetime, tuple[PredictionEvidence, ...]]] = []
"""What the run handed the evaluation, one entry per run. Cleared per test by
`_frozen_clock`, so nothing depends on the order the tests ran in."""


class _RecordingEvaluation:
    """The model-rule evaluation double: it records what the run handed it, so a
    test can tell "the run wrote predictions" from "the run decided alerts"."""

    async def __call__(
        self, *, at: datetime.datetime, predictions: Sequence[PredictionEvidence]
    ) -> None:
        _EVALUATED.append((at, tuple(predictions)))


def _recording_evaluation(session: AsyncSession) -> _RecordingEvaluation:
    """The factory the composition root injects, answering the double."""
    return _RecordingEvaluation()


class _RaisingPredictor:
    """A predictor that never answers: one event's artifact fails to load.

    Registered for a single event, so the cell stores the other event's prediction
    first and then fails on this one (`EventType` walks flood before drought).
    """

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        raise RuntimeError("the model artifact is corrupt")


class _FlakyOnce:
    """The alert evaluation that fails its first call and delegates every call
    after it, the way procrastinate's `max_attempts=2` retry sees it.

    One instance for every run of the job (a test hands the same one to the
    factory), because a retry is a second call of a process that is still up.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.failed = False

    async def __call__(
        self, *, at: datetime.datetime, predictions: Sequence[PredictionEvidence]
    ) -> None:
        if not self.failed:
            self.failed = True
            raise RuntimeError("the alert write failed")
        await self._inner(at=at, predictions=predictions)  # type: ignore[operator]


async def _alerts_of_plot(db_session: AsyncSession, plot_id: UUID) -> list[tuple[str, str, str]]:
    rows = (
        await db_session.execute(
            select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
            .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
            .where(AlertRow.plot_id == plot_id)
            .order_by(AlertRuleRow.code)
        )
    ).all()
    return [(code, state, severity) for code, state, severity in rows]


def _use_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_predictors: bool = True,
    with_cells: bool = True,
    predictors: PredictorRegistry | None = None,
    alert_evaluation: AlertEvaluationFactory = _recording_evaluation,
) -> None:
    if with_cells:
        # The composition root does this in `techcamp/worker.py`
        # (docs/05 §Solo la fachada pública). Through monkeypatch, so whether a test
        # finds the seam configured never depends on the order they ran in (#240
        # R3-global-seam-leak).
        monkeypatch.setattr(jobs_module, "_weather_cells", SqlAlchemyWeatherRepository)
    # Same seam for the model rules (docs/06 §8 "Alertas"), by default a double:
    # the alerts themselves are `tests/alerts/test_risk_rules.py`, and the test
    # below is the one that runs the real composition.
    monkeypatch.setattr(jobs_module, "_alert_evaluation", alert_evaluation)
    monkeypatch.setattr(jobs_module, "_archive", lambda: seminar_archive_adapter())
    monkeypatch.setattr(
        jobs_module,
        "_predictors",
        lambda: (
            predictors
            if predictors is not None
            else PredictorRegistry(
                {
                    ("risk_flood", _VERSION): _FixedPredictor(0.82),
                    ("risk_drought", _VERSION): _FixedPredictor(0.2),
                }
                if with_predictors
                else {}
            )
        ),
    )


async def _cell_with_plot(db_session: AsyncSession, *, lat: str, lon: str) -> int:
    """A cell and a plot on it, the arrangement that makes a cell active
    (docs/06-diseno-detallado.md §6)."""
    cell_id = await SqlAlchemyWeatherRepository(db_session).get_or_create_cell(
        Decimal(lat), Decimal(lon)
    )
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id: UUID = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
            weather_cell_id=cell_id,
        )
    )
    await db_session.commit()
    return cell_id


async def _register_versions(db_session: AsyncSession, *names: str) -> None:
    for name in names:
        db_session.add(
            ModelVersionRow(
                id=uuid7(),
                name=name,
                version=_VERSION,
                artifact_uri="s3://models/risk.ubj",
                is_baseline=False,
                thresholds={"high": 0.7, "critical": 0.85},
                promoted=True,
                created_at=datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC),
            )
        )
    await db_session.commit()


async def _stored(db_session: AsyncSession) -> list[tuple[int, str, datetime.date, str]]:
    return [
        (row.cell_id, row.event_type, row.horizon_start, row.severity)
        for row in (
            await db_session.execute(
                text(
                    "SELECT cell_id, event_type, horizon_start, severity FROM risk_prediction "
                    "ORDER BY cell_id, event_type"
                )
            )
        ).all()
    ]


async def test_the_daily_run_predicts_every_cell_a_plot_points_at(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §8: features per active cell, one prediction per
    event. A cell no plot falls into is not active and gets no provider call."""
    _use_doubles(monkeypatch)
    first = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    second = await _cell_with_plot(db_session, lat="11.9", lon="-74.2")
    await SqlAlchemyWeatherRepository(db_session).get_or_create_cell(
        Decimal("9.9"), Decimal("-75.1")
    )
    await _register_versions(db_session, "risk_flood", "risk_drought")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    # `0.82` clears `{high: 0.7}` but not `{critical: 0.85}` (docs/08-ml.md §M2
    # "Severidad"), and the read is ordered by event, so `drought` comes first.
    assert await _stored(db_session) == [
        (first, "drought", datetime.date(2026, 10, 1), "low"),
        (first, "flood", datetime.date(2026, 10, 1), "high"),
        (second, "drought", datetime.date(2026, 10, 1), "low"),
        (second, "flood", datetime.date(2026, 10, 1), "high"),
    ]


async def test_the_run_predicts_the_month_of_the_day_it_is_given(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The month is the day's, and `horizon_days` its length (docs/08-ml.md §M2
    "Horizonte"; D-T0.3): the run of the 20th of a month writes that month. August,
    not a month ahead of the frozen today, so its window is a window the recorded
    responses cover (#240 R3-wall-clock-dependent-job-tests)."""
    _use_doubles(monkeypatch)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=datetime.date(2026, 8, 20).isoformat())

    assert await _stored(db_session) == [(cell_id, "flood", datetime.date(2026, 8, 1), "high")]


async def test_a_second_run_of_the_same_month_writes_no_second_prediction(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La predicción de una celda, evento y mes se escribe una vez; las corridas
    siguientes del mismo mes no la repiten (docs/06-diseno-detallado.md §8)."""
    _use_doubles(monkeypatch)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert len(await _stored(db_session)) == 1


async def test_a_run_with_no_registered_predictor_stores_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a predictor there is no probability to store, and the job finishes
    normally instead of failing (docs/06-diseno-detallado.md §8 "Sin modelo
    promovido")."""
    _use_doubles(monkeypatch, with_predictors=False)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == []


async def test_a_cell_the_database_rejects_does_not_lose_the_cells_around_it(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#240 R3-one-cell-failure-aborts-run and R3-long-transaction-across-http: a
    write the database rejects (a probability outside [0, 1] trips the CHECK of
    docs/03-modelo-datos.md) used to leave `run_daily_risk` entirely, and the single
    commit at the end of the run rolled back every cell predicted before it. The
    failed cell stores nothing; the cells around it are stored."""
    first = await _cell_with_plot(db_session, lat="10.1", lon="-74.1")
    second = await _cell_with_plot(db_session, lat="11.1", lon="-74.2")
    third = await _cell_with_plot(db_session, lat="12.1", lon="-74.3")
    await _register_versions(db_session, "risk_flood")
    predictor = _OutOfRangePredictor(0.4, out_of_range_on_call=2)
    _use_doubles(
        monkeypatch,
        predictors=PredictorRegistry({("risk_flood", _VERSION): predictor}),
    )

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert predictor.calls == 3
    assert [(cell_id, event) for cell_id, event, _, _ in await _stored(db_session)] == [
        (first, "flood"),
        (third, "flood"),
    ]
    assert second not in {cell_id for cell_id, _, _, _ in await _stored(db_session)}


async def test_a_run_without_a_configured_cell_reader_fails_loudly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composition seam is not optional: a worker that never called
    `configure_weather_cells` must raise instead of running a job that predicts
    nothing and looks like a month without risk (docs/05-arquitectura.md §Solo la
    fachada pública)."""
    monkeypatch.setattr(jobs_module, "_weather_cells", None)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    with pytest.raises(RuntimeError, match="configure_weather_cells"):
        await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == []


async def test_the_run_hands_the_predictions_it_wrote_to_the_alert_evaluation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §8: "después de escribir las predicciones" the
    rules are evaluated, in the SAME run and over the predictions stored for the
    month that run predicted. The month, not the run's own insert: a rerun of a
    month already predicted writes nothing (docs/06 §8) and still has to decide
    the stored rows, or the alert of that month is lost when the first attempt's
    alert step is what failed."""
    _use_doubles(monkeypatch)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood", "risk_drought")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert len(_EVALUATED) == 2
    first_run = _EVALUATED[0][1]
    assert {prediction.event for prediction in first_run} == {"flood", "drought"}
    assert {prediction.cell_id for prediction in first_run} == {cell_id}
    assert {prediction.severity.value for prediction in first_run} == {"high", "low"}
    # The second run of the same month stored nothing and decided the same rows.
    assert _EVALUATED[1][1] == first_run


async def test_a_cell_that_fails_after_it_stored_a_row_alerts_on_the_row_it_stored(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#242 R3-rollback-leaves-written-count-inflated against the REAL repository:
    `insert_prediction` commits every row of its own
    (`risk/adapters/repositories.py:163`), so the cell's rollback does not undo the
    flood row stored before the drought predict raised. That row is written, it is
    stored, and it is what the alert is decided on — dropping it from either count
    lost both the count and the alert."""
    caplog.set_level(logging.INFO)
    _use_doubles(
        monkeypatch,
        predictors=PredictorRegistry(
            {
                ("risk_flood", _VERSION): _FixedPredictor(0.82),
                ("risk_drought", _VERSION): _RaisingPredictor(),
            }
        ),
        alert_evaluation=build_risk_evaluation,
    )
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood", "risk_drought")
    plot_id = (
        await db_session.execute(select(PlotRow.id).where(PlotRow.weather_cell_id == cell_id))
    ).scalar_one()

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == [(cell_id, "flood", datetime.date(2026, 10, 1), "high")]
    assert "risk: 1 predictions for 2026-10-01" in caplog.text
    assert await _alerts_of_plot(db_session, plot_id) == [("flood_risk", "open", "critical")]


async def test_a_retried_run_opens_the_alert_the_failed_attempt_left_undecided(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job retries (`RetryStrategy(max_attempts=2)`, ADR-0012) and the retry
    re-runs everything: the predictions are already stored, so the run writes
    nothing — and evaluating only what this attempt inserted would leave that
    month with no alert at all, lost in silence (docs/06 §8 "Alertas"). The stored
    rows of the month are what both attempts decide, and `open_alert` is
    idempotent, so the second one opens what the first could not."""
    flaky = _FlakyOnce(build_risk_evaluation(db_session))
    _use_doubles(monkeypatch, alert_evaluation=lambda _session: flaky)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")
    plot_id = (
        await db_session.execute(select(PlotRow.id).where(PlotRow.weather_cell_id == cell_id))
    ).scalar_one()

    with pytest.raises(RuntimeError, match="the alert write failed"):
        await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    assert await _alerts_of_plot(db_session, plot_id) == []

    # The retry: the prediction is already stored, so nothing new is written.
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == [(cell_id, "flood", datetime.date(2026, 10, 1), "high")]
    assert await _alerts_of_plot(db_session, plot_id) == [("flood_risk", "open", "critical")]


async def test_a_second_run_of_the_same_month_opens_no_second_alert(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression fix 2 opened: every run of a month hands the same stored
    prediction over, so a second run that decided it again would put a second
    `flood_risk` alert on the plot — and after a day of that, a farmer closing one
    by hand would see the next morning reopen it (docs/06 §3 "cierre manual").

    docs/06 §3: una sola alerta abierta por (`rule_id`, `plot_id`), and a decision
    taken from a prediction is taken once."""
    _use_doubles(monkeypatch, alert_evaluation=build_risk_evaluation)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood", "risk_drought")
    plot_id = (
        await db_session.execute(select(PlotRow.id).where(PlotRow.weather_cell_id == cell_id))
    ).scalar_one()

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _alerts_of_plot(db_session, plot_id) == [("flood_risk", "open", "critical")]


async def test_the_run_opens_the_model_alert_of_the_cell_it_predicted(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path over real repositories: the run writes the predictions and
    the same run opens `flood_risk` on the plot of the cell, because 0.82 clears
    `{high: 0.7}` (docs/08-ml.md §M2 "Severidad"), while the drought prediction of
    0.2 is `low` and opens nothing (docs/06-diseno-detallado.md §8 "Alertas")."""
    _use_doubles(monkeypatch, alert_evaluation=build_risk_evaluation)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood", "risk_drought")
    plot_id = (
        await db_session.execute(select(PlotRow.id).where(PlotRow.weather_cell_id == cell_id))
    ).scalar_one()

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    rows = (
        await db_session.execute(
            select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
            .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
            .where(AlertRow.plot_id == plot_id)
            .order_by(AlertRuleRow.code)
        )
    ).all()
    assert [(code, state, severity) for code, state, severity in rows] == [
        ("flood_risk", "open", "critical")
    ]


async def test_a_run_without_a_configured_alert_evaluation_fails_loudly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other composition seam, for the same reason as the cell reader: without
    it the run writes predictions that never become an alert, which is exactly the
    gap docs/06 §8 "Alertas" closes (docs/05-arquitectura.md §Solo la fachada
    pública)."""
    _use_doubles(monkeypatch)
    monkeypatch.setattr(jobs_module, "_alert_evaluation", None)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    with pytest.raises(RuntimeError, match="configure_alert_evaluation"):
        await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == []


async def test_the_worker_composes_the_alert_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    """`techcamp/worker.py` is the composition root (docs/05-arquitectura.md §Solo
    la fachada pública), so the real builder has to be injected from there: a
    builder injected nowhere is a run that predicts and never alerts."""
    import techcamp.worker as worker

    monkeypatch.setattr(worker.app, "run_worker", lambda **kwargs: None)

    worker.main()

    assert jobs_module._alert_evaluation is build_risk_evaluation


async def test_the_worker_runs_the_risk_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task on a queue this worker does not listen to never runs, and every test
    that calls the coroutine directly would stay green (docs/10-dag.md §3, ADR-0012)."""
    import techcamp.worker as worker

    listened: list[str] = []
    monkeypatch.setattr(
        worker.app, "run_worker", lambda **kwargs: listened.extend(kwargs["queues"])
    )

    worker.main()

    assert QUEUE_NAME in listened


async def test_the_daily_run_is_scheduled_at_six_in_the_morning(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §8 and docs/10-dag.md §3: the risk inference runs
    every day at 06:00, on the queue this worker listens to. The cron is read in
    the worker's own local time, which is America/Bogota (docs/10-dag.md §3)."""
    import techcamp.worker  # noqa: F401  registers the periodic schedules

    scheduled = {
        task_name: entry
        for (task_name, _periodic_id), entry in jobs_app.periodic_registry.periodic_tasks.items()
    }

    assert scheduled[RUN_ACTIVE_CELLS_TASK_NAME].cron == "0 6 * * *"
    assert scheduled[RUN_ACTIVE_CELLS_TASK_NAME].configure_kwargs.get("queue") == QUEUE_NAME


async def test_the_run_defaults_to_the_bogota_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`day` is optional in the job too: the 06:00 cron hands over only
    `timestamp`, so the run has to resolve the local day itself."""
    _use_doubles(monkeypatch)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0)

    assert await _stored(db_session) == [(cell_id, "flood", _FROZEN_TODAY.replace(day=1), "high")]


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _registered_paths(application: object) -> set[str]:
    return set(application.openapi()["paths"])  # type: ignore[union-attr]


async def _queued_jobs(db_session: AsyncSession) -> dict[int, dict[str, object]]:
    return {
        job.id: {
            "task_name": job.task_name,
            "queue_name": job.queue_name,
            "status": job.status,
            "args": job.args,
        }
        for job in (
            await db_session.execute(
                text(
                    "SELECT id, task_name, queue_name, status, args FROM procrastinate_jobs "
                    "WHERE task_name = :t ORDER BY id"
                ),
                {"t": RUN_ACTIVE_CELLS_TASK_NAME},
            )
        ).all()
    }


async def test_the_dev_route_returns_a_job_id_and_defaults_the_day_to_today(
    db_session: AsyncSession,
) -> None:
    """`POST /dev/jobs/risk:run { day? }` → `{ job_id }` (docs/04-api.md §Solo perfil
    seminario), with the day defaulting to today."""
    response = _client().post(_ROUTE, json={})

    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    queued = await _queued_jobs(db_session)
    assert response.json()["job_id"] in queued
    assert queued[response.json()["job_id"]] == {
        "task_name": RUN_ACTIVE_CELLS_TASK_NAME,
        "queue_name": QUEUE_NAME,
        "status": "todo",
        "args": {"day": _FROZEN_TODAY.isoformat(), "timestamp": 0},
    }


async def test_the_dev_route_accepts_the_day_it_is_given(db_session: AsyncSession) -> None:
    day = _DAY
    response = _client().post(_ROUTE, json={"day": day.isoformat()})

    assert response.status_code == 200
    queued = await _queued_jobs(db_session)
    assert queued[response.json()["job_id"]]["args"]["day"] == day.isoformat()


async def test_the_dev_route_rejects_a_future_day_with_422(db_session: AsyncSession) -> None:
    """The month of a day that has not happened is not a month to predict: the
    horizon reads data through the last day of M-1 (docs/08-ml.md §M2 "Horizonte"),
    and a month whose window does not exist cannot have a prediction."""
    tomorrow = _FROZEN_TODAY + datetime.timedelta(days=1)
    response = _client().post(_ROUTE, json={"day": tomorrow.isoformat()})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert await _queued_jobs(db_session) == {}


async def test_the_dev_route_rejects_an_unparseable_day_with_422(
    db_session: AsyncSession,
) -> None:
    """A `date` field, so a malformed value is FastAPI's own 422 and not a
    `fromisoformat` failure inside the job."""
    response = _client().post(_ROUTE, json={"day": "manana"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert await _queued_jobs(db_session) == {}


async def test_the_dev_route_is_absent_in_the_production_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0021: `/dev` routes exist only in the seminar profile."""
    import techcamp.main as main

    assert _REGISTERED_PATH in _registered_paths(app), "seminar profile keeps the route"
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    try:
        production_app = importlib.reload(main).app
        assert _REGISTERED_PATH not in _registered_paths(production_app)
    finally:
        monkeypatch.undo()
        importlib.reload(main)
    assert _REGISTERED_PATH in _registered_paths(main.app)
