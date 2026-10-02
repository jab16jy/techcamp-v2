"""Schema of the climate-risk tables (docs/03-modelo-datos.md §`municipality`,
`model_version` y `risk_prediction`: riesgo climático (E10)).

Every test runs against real Postgres: `conftest._migrated_schema` migrates to
`head` once per session and downgrades to `base` at teardown, so a broken
upgrade or downgrade fails the whole suite, not just a dedicated test.

The constraints under test are the ones the docs state as decisions, not
defensive extras: the severity and event vocabularies, the probability range,
the one promoted version per event, and one prediction per
`(cell, event, month, version)`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_CELL_ID = 901
_THRESHOLDS = '{"high": 0.7, "critical": 0.85}'


async def _cell(db_session: AsyncSession) -> int:
    await db_session.execute(
        text("INSERT INTO weather_cell (id, lat, lon) VALUES (:id, 10.9, -74.1)"),
        {"id": _CELL_ID},
    )
    await db_session.commit()
    return _CELL_ID


async def _model_version(
    db_session: AsyncSession,
    *,
    name: str = "risk_flood",
    version: str = "2026-10-02",
    promoted: bool = True,
    is_baseline: bool = False,
) -> uuid.UUID:
    version_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO model_version "
            "(id, name, version, artifact_uri, metrics, is_baseline, artifact_sha256, "
            " dataset_hash, git_commit, thresholds, promoted, promotion_reason, created_at) "
            "VALUES (:id, :name, :version, 's3://models/x.ubj', :metrics, :is_baseline, "
            " 'abc123', 'def456', '0f1e2d3', :thresholds, :promoted, NULL, :created_at)"
        ),
        {
            "id": version_id,
            "name": name,
            "version": version,
            "metrics": '{"pr_auc": 0.71}' if not is_baseline else None,
            "is_baseline": is_baseline,
            "thresholds": _THRESHOLDS,
            "promoted": promoted,
            "created_at": datetime(2026, 10, 2, tzinfo=UTC),
        },
    )
    await db_session.commit()
    return version_id


async def _prediction(
    db_session: AsyncSession,
    *,
    model_version_id: uuid.UUID,
    event_type: str = "flood",
    severity: str = "high",
    probability: float = 0.8,
) -> uuid.UUID:
    prediction_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO risk_prediction "
            "(id, cell_id, model_version_id, event_type, horizon_start, horizon_days, "
            " probability, severity, top_factors, created_at) "
            "VALUES (:id, :cell_id, :model_version_id, :event_type, '2026-11-01', 30, "
            " :probability, :severity, :top_factors, :created_at)"
        ),
        {
            "id": prediction_id,
            "cell_id": _CELL_ID,
            "model_version_id": model_version_id,
            "event_type": event_type,
            "probability": probability,
            "severity": severity,
            "top_factors": '[{"feature": "precip_sum_3m", "value": 380.0, "contribution": 0.42}]',
            "created_at": datetime(2026, 10, 2, tzinfo=UTC),
        },
    )
    await db_session.commit()
    return prediction_id


async def test_at_most_one_promoted_version_per_event(db_session: AsyncSession) -> None:
    """Índice único parcial `(name) WHERE promoted`: como máximo una versión
    promovida por evento; revertir es promover la anterior (docs/03 §Versión
    promovida)."""
    await _model_version(db_session, version="2026-10-02", promoted=True)
    with pytest.raises(IntegrityError):
        await _model_version(db_session, version="2026-10-09", promoted=True)
    await db_session.rollback()

    # A second *unpromoted* version of the same name is the normal case: the
    # next candidate registers while the promoted one serves.
    await _model_version(db_session, version="2026-10-09", promoted=False)


async def test_promoted_version_is_unique_per_name_not_globally(
    db_session: AsyncSession,
) -> None:
    """The partial index is on `name`, so promoting a `risk_drought` version is
    not blocked by a promoted `risk_flood` one."""
    await _model_version(db_session, name="risk_flood", promoted=True)
    await _model_version(db_session, name="risk_drought", version="drought-1", promoted=True)


async def test_risk_prediction_rejects_an_unknown_event_type(db_session: AsyncSession) -> None:
    """`event_type` ∈ {flood, drought} (docs/03): an event outside the M2/M3
    vocabulary is refused by the database, not only by the writer."""
    version_id = await _model_version(db_session)
    await _cell(db_session)
    with pytest.raises(IntegrityError):
        await _prediction(db_session, model_version_id=version_id, event_type="earthquake")
    await db_session.rollback()

    await _prediction(db_session, model_version_id=version_id, event_type="drought")


async def test_risk_prediction_rejects_an_unknown_severity(db_session: AsyncSession) -> None:
    """`severity` ∈ {low, high, critical} (docs/03, docs/08 §M2 "Severidad")."""
    version_id = await _model_version(db_session)
    await _cell(db_session)
    with pytest.raises(IntegrityError):
        await _prediction(db_session, model_version_id=version_id, severity="extreme")
    await db_session.rollback()

    await _prediction(db_session, model_version_id=version_id, severity="low")


@pytest.mark.parametrize("probability", [1.5, -0.1])
async def test_risk_prediction_rejects_a_probability_outside_zero_one(
    db_session: AsyncSession, probability: float
) -> None:
    """`probability` ∈ [0, 1]: a number outside it is not a probability, and a
    stored one would be served as such (docs/03)."""
    version_id = await _model_version(db_session)
    await _cell(db_session)
    with pytest.raises(IntegrityError):
        await _prediction(db_session, model_version_id=version_id, probability=probability)
    await db_session.rollback()


@pytest.mark.parametrize("probability", [0.0, 1.0])
async def test_risk_prediction_accepts_the_probability_bounds(
    db_session: AsyncSession, probability: float
) -> None:
    """Both bounds are ordinary probabilities a calibrated classifier emits."""
    version_id = await _model_version(db_session)
    await _cell(db_session)
    await _prediction(db_session, model_version_id=version_id, probability=probability)


async def test_risk_prediction_rejects_a_cell_that_does_not_exist(
    db_session: AsyncSession,
) -> None:
    """`cell_id` references `weather_cell.id`: a prediction with no cell has no
    coordinates to be a prediction about."""
    version_id = await _model_version(db_session)
    with pytest.raises(IntegrityError):
        await _prediction(db_session, model_version_id=version_id)
    await db_session.rollback()

    await _cell(db_session)
    await _prediction(db_session, model_version_id=version_id)


async def test_one_prediction_per_cell_event_month_and_version(
    db_session: AsyncSession,
) -> None:
    """`UNIQUE (cell_id, event_type, horizon_start, model_version_id)`: el job es
    idempotente y promover otra versión agrega su predicción sin borrar la
    anterior (docs/03 §Unicidad de la predicción)."""
    version_id = await _model_version(db_session, promoted=False)
    await _cell(db_session)
    await _prediction(db_session, model_version_id=version_id)

    with pytest.raises(IntegrityError):
        await _prediction(db_session, model_version_id=version_id)
    await db_session.rollback()

    # Another version of the same event adds its own row for the same month.
    other_version_id = await _model_version(db_session, version="2026-10-09", promoted=False)
    await _prediction(db_session, model_version_id=other_version_id)


async def test_model_version_rows_survive_without_organization(
    db_session: AsyncSession,
) -> None:
    """Neither risk table carries `org_id`: a cell is shared reference data and
    its prediction is the same for every organization (docs/09-cuellos-de-botella.md:39,
    docs/03-modelo-datos.md:39). Org isolation is enforced where the data leaves
    the server, at the plot endpoint (T6b)."""
    await _model_version(db_session)
    version_id = await _model_version(
        db_session, name="risk_drought", version="d-1", promoted=False
    )
    await _cell(db_session)
    await _prediction(db_session, model_version_id=version_id)

    stored = await db_session.execute(text("SELECT count(*) FROM risk_prediction"))
    assert stored.scalar_one() == 1
