"""The daily climate-risk inference (docs/06-diseno-detallado.md §8; docs/08-ml.md
§M2 "Horizonte", "Severidad", "Features", "Elevación y pendiente"; D-T0.3, D-T0.4,
D-T0.6, D-T6b.1).

For a day D the issue month M is D's month in America/Bogota, and every cell with
at least one plot is predicted for every event whose version is served and whose
predictor is registered: the archive window of the six months before M, the
features of that window, the probability, the severity read from the version's own
thresholds, and one idempotent row (docs/06 §8, one prediction per cell, event and
month).

Three things are a *skip* and never a wrong number, each one logged with its
reason: an event with no registered version, a version with no registered
predictor, and a cell whose archive did not answer or has not caught up with
M-1 (docs/06 §8 "Sin modelo promovido" and "Datos de entrada"). A skipped cell
leaves its previous prediction standing, which is what the plot endpoint then
serves (docs/04-api.md §Riesgo, métricas y asistente).

The climatology of the anomaly features is the one input this use case does not
build: no doc says where the served version's train years live, so D-T6b.1 leaves
the anomaly features missing and T9 wires the climatology with the real
predictors. A missing feature is `None`, never `0` (docs/08-ml.md §M2 "Features").
"""

from __future__ import annotations

import logging
import math
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from techcamp.risk.application.ports import (
    CellTransactions,
    Predictor,
    PredictorRegistry,
    RiskArchivePort,
    RiskCell,
    RiskRepository,
)
from techcamp.risk.domain.errors import ArchiveUnavailableError
from techcamp.risk.domain.features import Neighbours, build_features, window_bounds
from techcamp.risk.domain.models import (
    ArchiveDay,
    EventType,
    ModelVersion,
    RiskPrediction,
    severity_for,
)
from techcamp.shared.ids import uuid7

logger = logging.getLogger(__name__)

FEATURE_WINDOW_MONTHS = 6
"""The months before M the archive is asked for (docs/06-diseno-detallado.md §8
"Datos de entrada": "los 6 meses previos de la celda"), which is also the longest
window of `domain.features` and the reason the split needs a gap of at least six
months (docs/08-ml.md §M2 "Particion")."""

NEIGHBOUR_SPACING_M = 1000.0
"""Distance from a cell's centre to each of the four neighbours the slope comes
from, ~1 km (docs/08-ml.md §M2 "Elevación y pendiente": finite differences over
4 neighbours ~1 km apart)."""

_METRES_PER_DEGREE_LAT = 111_320.0
"""Metres in one degree of latitude, and the longitude offset's divisor by the cosine
of the latitude, because a degree of longitude is shorter the closer the cell is to a
pole. The floor keeps a cell at the pole from dividing by zero.

The constant and the formula are T3's, not a fresh choice: the training neighbours
come from `ml/src/techcamp_ml/sources/elevation.py` (`neighbour_offsets`, lane
`e10-t3`), and train/serve parity is a governance rule — the same module, the same
source and the same inputs on both sides (docs/08-ml.md §M2 "Features", §Reglas de
gobierno "Paridad de features"; docs/06-diseno-detallado.md §8). A different
constant would ask Open-Meteo for the elevation of a *different* point at serving
time, so `slope_deg` would describe a different slope than the one the model was
trained on."""

_POINTS_PER_CELL = 5
"""The centre plus its four neighbours: one elevation call answers all five
(`ELEVATION_MAX_POINTS` in the T6a adapter)."""


@dataclass(frozen=True, slots=True)
class DailyRiskRun:
    """What one run did: the month it predicted, how many predictions it wrote and
    how many cell/event pairs it skipped.

    `skipped` counts a pair with no prediction *from this run* — nothing
    registered for it, an archive that did not answer, an ERA5 window still
    behind M-1, a month already stored, or a cell that failed after this run
    wrote part of it. Each of them is logged with its reason, so a run that
    predicts nothing says why instead of looking like a quiet one.

    `written` and `skipped` add up to the pairs the run was asked about, and
    `written` never counts a row the cell's own transaction rolled back (#242
    `R3-rollback-leaves-written-count-inflated`).
    """

    issue_month: date
    written: int
    skipped: int


def elevation_points(cell: RiskCell) -> list[tuple[float, float]]:
    """The centre first, then east, west, north and south `NEIGHBOUR_SPACING_M`
    away: the order `neighbours_from` reads them in, and the five points of one
    elevation call (docs/08-ml.md §M2 "Elevación y pendiente").

    `d_lat = spacing / METRES_PER_DEGREE_LAT` and
    `d_lon = spacing / (METRES_PER_DEGREE_LAT * cos(lat))` are T3's
    `neighbour_offsets`, term for term
    (`ml/src/techcamp_ml/sources/elevation.py`, lane `e10-t3`).
    """
    lat_step = NEIGHBOUR_SPACING_M / _METRES_PER_DEGREE_LAT
    lon_step = lat_step / max(math.cos(math.radians(cell.lat)), 1e-6)
    return [
        (cell.lat, cell.lon),
        (cell.lat, cell.lon + lon_step),
        (cell.lat, cell.lon - lon_step),
        (cell.lat + lat_step, cell.lon),
        (cell.lat - lat_step, cell.lon),
    ]


def neighbours_from(elevations: Sequence[float | None]) -> Neighbours | None:
    """The four neighbours of the centre, or `None` when the provider has no
    elevation for one of them: a slope computed from an invented neighbour is the
    slope of a hill that does not exist, so `slope_deg` stays missing evidence
    (docs/06-diseno-detallado.md §8; `domain.features.build_features`).

    The centre's own elevation is read separately and may be `None` as well:
    `elevation_m` is a feature of its own.
    """
    if len(elevations) != _POINTS_PER_CELL:
        return None
    east, west, north, south = elevations[1:]
    if east is None or west is None or north is None or south is None:
        return None
    return Neighbours(east=east, west=west, north=north, south=south, spacing_m=NEIGHBOUR_SPACING_M)


def _covers_window(days: Sequence[ArchiveDay], *, window_start: date, window_end: date) -> bool:
    """Whether the archive answered **every day of the window with a real value**.

    Checking that the row for the last day of M-1 exists is not enough: the client
    keeps a day ERA5 has not aggregated, with `None` measures
    (`adapters/open_meteo_archive.py`, `_extract_float`), so for the first days of M
    the response covers M-1 on paper and measures nothing at its end. Then every
    accumulation of `domain.features` is `None` and a prediction written from them
    would be one the `ON CONFLICT DO NOTHING` of docs/03 §Unicidad blocks for the
    rest of the month: the cell would keep an empty prediction nobody can correct
    until M is over.

    Every day of the window needs `precipitation_mm`, the measure whose six-month
    window is the longest one (docs/08-ml.md §M2 "Features"); a day without soil
    moisture stays missing evidence that `build_features` reports as `None` for
    that one feature, which the predictor is free to read.

    So while the previous month is incomplete the cell has no prediction for the new
    one and keeps the one it has (docs/06-diseno-detallado.md §8 "Datos de entrada";
    docs/04-api.md §Riesgo, métricas y asistente: el mes en curso sin predicción
    sirve la más reciente).
    """
    reported = {row.day: row.precipitation_mm for row in days}
    day = window_start
    while day <= window_end:
        value = reported.get(day)
        if value is None or math.isnan(value):
            return False
        day += timedelta(days=1)
    return True


async def _predictable(
    *, versions: RiskRepository, predictors: PredictorRegistry
) -> list[tuple[EventType, ModelVersion, Predictor]]:
    """The `(event, version, predictor)` triples this run can predict, logging and
    dropping the ones it cannot.

    An event with no registered version and a version with no registered predictor
    are the same outcome — no prediction for that event this run — and neither is
    a reason to invent a probability (docs/06-diseno-detallado.md §8 "Sin modelo
    promovido"). The served version and its predictor are properties of the event,
    not of the cell, so this is asked once for the whole run: a cell nothing can be
    predicted for costs no provider call at all, and the read behind it is a read of
    the events, never one per cell.
    """
    pending: list[tuple[EventType, ModelVersion, Predictor]] = []
    for event in EventType:
        version = await versions.served_version(event.version_name)
        if version is None:
            logger.warning("risk: no registered version for %s, no predictions for it", event.value)
            continue
        predictor = predictors.resolve(version)
        if predictor is None:
            logger.warning(
                "risk: no predictor registered for %s@%s, no predictions for it",
                version.name,
                version.version,
            )
            continue
        pending.append((event, version, predictor))
    return pending


async def run_daily_risk(
    *,
    day: date,
    cells: Sequence[RiskCell],
    versions: RiskRepository,
    archive: RiskArchivePort,
    predictors: PredictorRegistry,
    transactions: CellTransactions | None = None,
) -> DailyRiskRun:
    """Predict the risk of every given cell for the month of `day`.

    `day` is read as its calendar month, so the day the job ran on never moves
    the window: the run of the 1st and the run of the 20th of M predict the same
    month, with data through the last day of M-1 (docs/08-ml.md §M2 "Horizonte",
    D-T0.3).

    `cells` are the cells at least one plot points at, resolved by the caller
    through the owning module's facade (docs/05-arquitectura.md §Lecturas
    cruzadas): this use case never joins another module's tables.

    The cells are worked one after another. An outage or a slow provider call
    therefore delays the cells after it and never blocks them, which is acceptable
    at the pilot's scale of a few hundred cells and keeps one cell's failure from
    cancelling the rest of the run (docs/06-diseno-detallado.md §6 "Degradación").
    That containment is not only for provider outages: a predictor that raises, or a
    write the database rejects — a probability outside `[0, 1]` trips the CHECK of
    docs/03-modelo-datos.md — is contained to its own cell exactly the same way, and
    the run carries on with the cells after it.

    `transactions` is the caller's transaction hook, committed once per cell: pass it
    to keep a failure late in the run from discarding the predictions already written
    (`CellTransactions`). Without it the run leaves every transaction to the
    repository, which is what the pure callers and the tests want.
    """
    issue_month = day.replace(day=1)
    window_start, window_end = window_bounds(issue_month, months=FEATURE_WINDOW_MONTHS)
    horizon_days = monthrange(issue_month.year, issue_month.month)[1]

    written = 0
    skipped = 0
    # Which version serves each event is the same answer for every cell, so it is
    # resolved once, before the walk. That read autobegins a transaction, and one
    # left open across `fetch_daily` would pin a pooled connection for the length of
    # the HTTP call and its retries, so it is closed here too (#240
    # R3-long-transaction-across-http).
    pending_events = await _predictable(versions=versions, predictors=predictors)
    skipped += (len(EventType) - len(pending_events)) * len(cells)
    if transactions is not None:
        await transactions.commit()
    if not pending_events:
        return DailyRiskRun(issue_month=issue_month, written=written, skipped=skipped)

    for cell in cells:
        try:
            days = await archive.fetch_daily(
                cell.lat, cell.lon, start_day=window_start, end_day=window_end
            )
            elevations = await archive.fetch_elevations(elevation_points(cell))
        except ArchiveUnavailableError:
            logger.warning(
                "risk: Open-Meteo archive unavailable for cell %s, keeping its stored predictions",
                cell.id,
                exc_info=True,
            )
            skipped += len(pending_events)
            continue

        if not _covers_window(days, window_start=window_start, window_end=window_end):
            logger.warning(
                "risk: archive for cell %s does not cover %s..%s with real values yet "
                "(ERA5 delay), no prediction for %s",
                cell.id,
                window_start,
                window_end,
                issue_month,
            )
            skipped += len(pending_events)
            continue

        features = build_features(
            issue_month=issue_month,
            precipitation={row.day: row.precipitation_mm for row in days},
            soil_moisture={row.day: row.soil_moisture_m3_m3 for row in days},
            elevation_m=elevations[0] if elevations else None,
            neighbours=neighbours_from(elevations),
        )

        stored_here = 0
        already_here = 0
        try:
            for event, version, predictor in pending_events:
                outcome = predictor.predict(version, features)
                prediction = RiskPrediction(
                    id=uuid7(),
                    cell_id=cell.id,
                    model_version_id=version.id,
                    event_type=event,
                    horizon_start=issue_month,
                    horizon_days=horizon_days,
                    probability=outcome.probability,
                    severity=severity_for(outcome.probability, version.thresholds),
                    top_factors=outcome.top_factors,
                    created_at=datetime.now(UTC),
                )
                if await versions.insert_prediction(prediction):
                    stored_here += 1
                else:
                    # La predicción de una celda, evento y mes se escribe una vez;
                    # las corridas siguientes del mismo mes no la repiten
                    # (docs/06-diseno-detallado.md §8).
                    logger.info(
                        "risk: cell %s already has a %s prediction for %s",
                        cell.id,
                        event.value,
                        issue_month,
                    )
                    already_here += 1
            if transactions is not None:
                await transactions.commit()
        except Exception:
            # One cell's failure is its own: the run logs it, undoes whatever that
            # cell left pending and carries on with the cells after it, because a
            # rejected statement poisons the transaction it was in (#240
            # R3-one-cell-failure-aborts-run, R3-long-transaction-across-http).
            logger.warning(
                "risk: cell %s could not be predicted for %s, the run goes on",
                cell.id,
                issue_month,
                exc_info=True,
            )
            if transactions is None:
                # Nothing was undone: the repository owns each of its own
                # transactions, so the rows this cell did store are still stored.
                written += stored_here
            else:
                try:
                    await transactions.rollback()
                except Exception:
                    # Undoing a cell that failed is part of containing it: a
                    # rollback that raises (a dropped connection) must not take the
                    # cells after this one down with it. The rows are lost either
                    # way, so the counts below treat them as never stored (#242
                    # R3-rollback-leaves-written-count-inflated).
                    logger.warning(
                        "risk: the transaction of cell %s could not be undone",
                        cell.id,
                        exc_info=True,
                    )
            # Every pair of a cell that failed produced nothing stored: the ones it
            # wrote went back with the transaction, the one that raised was never
            # stored, and the ones after it were never attempted. So the cell
            # contributes to `written` nothing at all and to `skipped` all of its
            # pairs, and `written` never reports a row the rollback discarded (#242
            # R3-rollback-leaves-written-count-inflated).
            skipped += len(pending_events)
        else:
            written += stored_here
            skipped += already_here

    return DailyRiskRun(issue_month=issue_month, written=written, skipped=skipped)
