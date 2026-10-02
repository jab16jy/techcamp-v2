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
from datetime import UTC, date, datetime

from techcamp.risk.application.ports import (
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

_METRES_PER_DEGREE_LAT = 110_574.0
"""Metres in one degree of latitude (~110.574 km). The longitude offset divides by
the cosine of the latitude, because a degree of longitude is shorter the closer the
cell is to a pole; the floor keeps a cell at the pole from dividing by zero."""

_POINTS_PER_CELL = 5
"""The centre plus its four neighbours: one elevation call answers all five
(`ELEVATION_MAX_POINTS` in the T6a adapter)."""


@dataclass(frozen=True, slots=True)
class DailyRiskRun:
    """What one run did: the month it predicted, how many predictions it wrote and
    how many cell/event pairs it skipped.

    `skipped` counts a pair with no prediction *from this run* — nothing
    registered for it, an archive that did not answer, an ERA5 window still
    behind M-1, or a month already stored. Each of them is logged with its reason,
    so a run that predicts nothing says why instead of looking like a quiet one.
    """

    issue_month: date
    written: int
    skipped: int


def elevation_points(cell: RiskCell) -> list[tuple[float, float]]:
    """The centre first, then east, west, north and south `NEIGHBOUR_SPACING_M`
    away: the order `neighbours_from` reads them in, and the five points of one
    elevation call (docs/08-ml.md §M2 "Elevación y pendiente").
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


def _reaches_previous_month(days: Sequence[ArchiveDay], *, window_end: date) -> bool:
    """Whether the archive answered through the last day of M-1.

    ERA5 arrives with ~5 days of delay, so for the first days of M the response
    stops short of M-1 and every window of the features would be missing its last
    days. The cell then has no prediction for the new month, and the previous one
    keeps being served (docs/06-diseno-detallado.md §8 "Datos de entrada";
    docs/04-api.md §Riesgo, métricas y asistente).
    """
    return any(row.day >= window_end for row in days)


async def _predictable(
    cell: RiskCell, *, versions: RiskRepository, predictors: PredictorRegistry
) -> list[tuple[EventType, ModelVersion, Predictor]]:
    """The `(event, version, predictor)` triples this cell can be predicted for,
    logging and dropping the ones it cannot.

    An event with no registered version and a version with no registered predictor
    are the same outcome — no prediction for that event this run — and neither is
    a reason to invent a probability (docs/06-diseno-detallado.md §8 "Sin modelo
    promovido"). Resolved before the archive is fetched because the window is the
    same for every event: a cell nothing can be predicted for costs no provider
    call at all.
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
    cancelling the rest of the run (docs/06-diseno-detallado.md §6
    "Degradación").
    """
    issue_month = day.replace(day=1)
    window_start, window_end = window_bounds(issue_month, months=FEATURE_WINDOW_MONTHS)
    horizon_days = monthrange(issue_month.year, issue_month.month)[1]

    written = 0
    skipped = 0
    for cell in cells:
        pending = await _predictable(cell, versions=versions, predictors=predictors)
        skipped += len(EventType) - len(pending)
        if not pending:
            continue

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
            skipped += len(pending)
            continue

        if not _reaches_previous_month(days, window_end=window_end):
            logger.warning(
                "risk: archive for cell %s does not cover %s yet (ERA5 delay), "
                "no prediction for %s",
                cell.id,
                window_end,
                issue_month,
            )
            skipped += len(pending)
            continue

        features = build_features(
            issue_month=issue_month,
            precipitation={row.day: row.precipitation_mm for row in days},
            soil_moisture={row.day: row.soil_moisture_m3_m3 for row in days},
            elevation_m=elevations[0] if elevations else None,
            neighbours=neighbours_from(elevations),
        )

        for event, version, predictor in pending:
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
                written += 1
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
                skipped += 1

    return DailyRiskRun(issue_month=issue_month, written=written, skipped=skipped)
