"""Climate-risk repository behavior (docs/03-modelo-datos.md §`model_version` y
`risk_prediction`; docs/08-ml.md §M2 "Línea base servida"; docs/04 §Riesgo,
métricas y asistente).

Every test runs against real Postgres. Neither table carries `org_id` (a
prediction belongs to a shared `weather_cell`), so isolation is not a property
these queries can have: what is tested instead is that a read never crosses a
cell, an event or a version, which is how a plot could be served another cell's
risk.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.risk.domain.models import EventType, RiskPrediction, Severity
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_FLOOD = "risk_flood"
_DROUGHT = "risk_drought"
_CELL_ID = 801
_OTHER_CELL_ID = 802
_CREATED = datetime(2026, 10, 2, tzinfo=UTC)


def _month_start(day: date) -> date:
    return day.replace(day=1)


async def _cell(db_session: AsyncSession, cell_id: int = _CELL_ID) -> int:
    await db_session.execute(
        text("INSERT INTO weather_cell (id, lat, lon) VALUES (:id, :lat, -74.1)"),
        {"id": cell_id, "lat": 10.9 + cell_id / 1000},
    )
    await db_session.commit()
    return cell_id


async def _version(
    db_session: AsyncSession,
    *,
    name: str = _FLOOD,
    version: str = "2026-10-02",
    promoted: bool = True,
    is_baseline: bool = False,
    created_at: datetime = _CREATED,
) -> uuid.UUID:
    version_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO model_version "
            "(id, name, version, artifact_uri, metrics, is_baseline, thresholds, promoted, "
            " created_at) VALUES (:id, :name, :version, 's3://models/x.ubj', :metrics, "
            " :is_baseline, :thresholds, :promoted, :created_at)"
        ),
        {
            "id": version_id,
            "name": name,
            "version": version,
            "metrics": None if is_baseline else '{"pr_auc": 0.71}',
            "is_baseline": is_baseline,
            "thresholds": '{"high": 0.7, "critical": 0.85}',
            "promoted": promoted,
            "created_at": created_at,
        },
    )
    await db_session.commit()
    return version_id


def _prediction(
    model_version_id: uuid.UUID,
    *,
    cell_id: int = _CELL_ID,
    event_type: EventType = EventType.FLOOD,
    horizon_start: date,
    probability: float = 0.8,
    severity: Severity = Severity.HIGH,
) -> RiskPrediction:
    return RiskPrediction(
        id=uuid7(),
        cell_id=cell_id,
        model_version_id=model_version_id,
        event_type=event_type,
        horizon_start=horizon_start,
        horizon_days=30,
        probability=probability,
        severity=severity,
        top_factors=[{"feature": "precip_sum_3m", "value": 380.0, "contribution": 0.42}],
        created_at=_CREATED,
    )


async def test_the_promoted_model_is_served_over_any_baseline(
    db_session: AsyncSession,
) -> None:
    """The promoted version is what serves the event; a baseline is the fallback
    for when none passes the gate (docs/08 §M2 "Línea base servida", D-T0.5)."""
    baseline_id = await _version(
        db_session,
        version="climatology",
        promoted=False,
        is_baseline=True,
        created_at=datetime(2026, 10, 9, tzinfo=UTC),
    )
    promoted_id = await _version(db_session, promoted=True)

    served = await SqlAlchemyRiskRepository(db_session).served_version(_FLOOD)

    assert served is not None
    assert served.id == promoted_id
    # The newer baseline stays registered, it just is not the served one.
    assert served.is_baseline is False
    assert served.id != baseline_id


async def test_the_registered_baseline_is_served_when_no_model_is_promoted(
    db_session: AsyncSession,
) -> None:
    """Sin modelo promovido: se usa la línea base registrada para el evento
    (docs/06 §8). Every prediction then still has a `model_version_id`."""
    baseline_id = await _version(
        db_session, version="rain-accumulation", promoted=False, is_baseline=True
    )

    served = await SqlAlchemyRiskRepository(db_session).served_version(_FLOOD)

    assert served is not None
    assert served.id == baseline_id
    assert served.is_baseline is True
    assert served.promoted is False
    assert served.thresholds == {"high": 0.7, "critical": 0.85}


async def test_no_registered_version_leaves_the_event_unserved(
    db_session: AsyncSession,
) -> None:
    """Sin ninguna versión registrada para un evento, el job no escribe
    predicciones de ese evento (docs/06 §8): the read answers `None` rather than
    falling back to another event's version."""
    await _version(db_session, name=_DROUGHT, promoted=True)

    assert await SqlAlchemyRiskRepository(db_session).served_version(_FLOOD) is None


async def test_two_baselines_are_tie_broken_on_the_newest(
    db_session: AsyncSession,
) -> None:
    """The docs name no tie-break for two registered baselines, so the choice is
    ours and must be deterministic: the most recently registered one, which
    `uuid7` ordering expresses without a second column."""
    older_id = await _version(
        db_session, version="first", promoted=False, is_baseline=True, created_at=_CREATED
    )
    newer_id = await _version(
        db_session, version="second", promoted=False, is_baseline=True, created_at=_CREATED
    )
    assert newer_id > older_id  # uuid7 sorts by creation time

    served = await SqlAlchemyRiskRepository(db_session).served_version(_FLOOD)

    assert served is not None
    assert served.id == newer_id
    assert served.id != older_id


async def test_a_prediction_is_written_once_per_cell_event_month_and_version(
    db_session: AsyncSession,
) -> None:
    """La predicción de una celda, evento y mes se escribe una vez; las
    corridas siguientes del mismo mes no la repiten (docs/06 §8). The second
    attempt reports that it wrote nothing and leaves the stored row alone."""
    version_id = await _version(db_session)
    await _cell(db_session)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)

    assert await repository.insert_prediction(_prediction(version_id, horizon_start=month)) is True
    # A rerun with a different probability is not a correction: it changes nothing.
    assert (
        await repository.insert_prediction(
            _prediction(version_id, horizon_start=month, probability=0.95)
        )
        is False
    )

    rows = await db_session.execute(text("SELECT count(*), min(probability) FROM risk_prediction"))
    count, stored = rows.one()
    assert count == 1
    # `real` is `float4` (docs/03): the stored value carries its precision, not
    # the one the writer had in memory.
    assert stored == pytest.approx(0.8)


async def test_another_version_of_the_same_month_is_its_own_row(
    db_session: AsyncSession,
) -> None:
    """Promover otra versión agrega su predicción sin borrar la anterior
    (docs/03 §Unicidad de la predicción)."""
    served_id = await _version(db_session, promoted=False)
    other_id = await _version(db_session, version="2026-10-09", promoted=False)
    await _cell(db_session)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)

    assert await repository.insert_prediction(_prediction(served_id, horizon_start=month)) is True
    assert await repository.insert_prediction(_prediction(other_id, horizon_start=month)) is True

    rows = await db_session.execute(text("SELECT count(*) FROM risk_prediction"))
    assert rows.scalar_one() == 2


async def test_the_current_month_is_served_even_when_a_later_one_exists(
    db_session: AsyncSession,
) -> None:
    """la predicción vigente ... la del mes en curso; si el mes en curso todavía
    no tiene predicción (ERA5 llega con ~5 días de retraso), la más reciente
    (docs/04 §Riesgo, métricas y asistente)."""
    version_id = await _version(db_session)
    await _cell(db_session)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)
    await repository.insert_prediction(
        _prediction(version_id, horizon_start=month, probability=0.8)
    )
    await repository.insert_prediction(
        _prediction(version_id, horizon_start=_shift_month(month, 1), probability=0.9)
    )

    served = await repository.latest_prediction(_CELL_ID, EventType.FLOOD, version_id)

    assert served is not None
    assert served.horizon_start == month
    assert served.probability == pytest.approx(0.8)


async def test_the_most_recent_is_served_when_the_current_month_has_none(
    db_session: AsyncSession,
) -> None:
    """The ERA5 delay leaves the cell without a prediction for the new month, and
    the previous one keeps being shown."""
    version_id = await _version(db_session)
    await _cell(db_session)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)
    await repository.insert_prediction(
        _prediction(version_id, horizon_start=_shift_month(month, -1), probability=0.4)
    )
    await repository.insert_prediction(
        _prediction(version_id, horizon_start=_shift_month(month, 1), probability=0.9)
    )

    served = await repository.latest_prediction(_CELL_ID, EventType.FLOOD, version_id)

    assert served is not None
    assert served.horizon_start == _shift_month(month, 1)
    assert served.probability == pytest.approx(0.9)
    assert served.horizon_start != _shift_month(month, -1)


async def test_a_read_never_crosses_cell_event_or_version(
    db_session: AsyncSession,
) -> None:
    """Three predictions that all exist, none of them the one being asked for."""
    served_id = await _version(db_session, promoted=False)
    other_version_id = await _version(db_session, version="2026-10-09", promoted=False)
    await _cell(db_session)
    await _cell(db_session, _OTHER_CELL_ID)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)
    await repository.insert_prediction(
        _prediction(served_id, cell_id=_OTHER_CELL_ID, horizon_start=month)
    )
    await repository.insert_prediction(
        _prediction(served_id, cell_id=_CELL_ID, event_type=EventType.DROUGHT, horizon_start=month)
    )
    await repository.insert_prediction(
        _prediction(other_version_id, cell_id=_CELL_ID, horizon_start=month)
    )

    assert await repository.latest_prediction(_CELL_ID, EventType.FLOOD, served_id) is None
    other_cell = await repository.latest_prediction(_OTHER_CELL_ID, EventType.FLOOD, served_id)
    assert other_cell is not None
    assert other_cell.cell_id == _OTHER_CELL_ID


async def test_the_served_prediction_carries_its_version_identity(
    db_session: AsyncSession,
) -> None:
    """Cada predicción guarda `model_version_id`: toda alerta se puede rastrear
    hasta el modelo exacto (docs/06 §8)."""
    version_id = await _version(db_session)
    await _cell(db_session)
    month = _month_start(local_today())
    repository = SqlAlchemyRiskRepository(db_session)
    await repository.insert_prediction(
        _prediction(
            version_id,
            horizon_start=month,
            probability=0.86,
            severity=Severity.CRITICAL,
        )
    )

    served = await repository.latest_prediction(_CELL_ID, EventType.FLOOD, version_id)

    assert served is not None
    assert served.model_version_id == version_id
    assert served.severity is Severity.CRITICAL
    assert served.probability == pytest.approx(0.86)
    assert served.top_factors == [
        {"feature": "precip_sum_3m", "value": 380.0, "contribution": 0.42}
    ]


def _shift_month(day: date, months: int) -> date:
    """First day of the month `months` away, so a test never builds a date the
    way the production code would and then agrees with it by accident."""
    index = day.year * 12 + (day.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)
