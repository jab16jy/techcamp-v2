"""`GET /plots/{plot_id}/risk` (docs/04-api.md §Riesgo, métricas y asistente;
docs/06-diseno-detallado.md §8).

The plot's current prediction per event, of the version being served (the promoted
one, or the baseline when no model passed the gate), and the most recent one when
the current month has none — ERA5 arrives with ~5 days of delay, so for the first
days of a month the cell still shows the month before (docs/04 §Riesgo).

Access control is the plot's own (docs/04: mismo control de acceso que
`/plots/{plot_id}`): `404` for a plot that does not exist or belongs to another
organization, never `403` and never another cell's risk (docs/09-cuellos-de-botella.md
§Seguridad). Neither `risk_prediction` nor `weather_cell` carries `org_id`: a
prediction is a property of a shared cell, so isolation is entirely this
endpoint's job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.risk.adapters.orm import ModelVersionRow, RiskPredictionRow
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_CREATED = datetime(2026, 10, 2, tzinfo=UTC)
_THIS_MONTH = local_today().replace(day=1)
_LAST_MONTH = (_THIS_MONTH - timedelta(days=1)).replace(day=1)

_phone_seq = count()


async def _member(
    db_session: AsyncSession, *, role: str, org_name: str = "Finca"
) -> tuple[uuid.UUID, uuid.UUID, str]:
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name=org_name, kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730077{next(_phone_seq):05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await db_session.commit()
    return org_id, user_id, issue_token(str(user_id))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_plot(
    db_session: AsyncSession, org_id: uuid.UUID, *, cell_id: int | None
) -> uuid.UUID:
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
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
    return plot_id


async def _cell(db_session: AsyncSession, cell_id: int) -> int:
    """A cell per id, on its own coordinates: `(lat, lon)` is unique
    (docs/03-modelo-datos.md §Unicidad de la celda)."""
    await db_session.execute(
        text("INSERT INTO weather_cell (id, lat, lon) VALUES (:id, :lat, -74.1)"),
        {"id": cell_id, "lat": Decimal("10.9") + Decimal(cell_id) / 1000},
    )
    await db_session.commit()
    return cell_id


async def _version(
    db_session: AsyncSession,
    *,
    name: str,
    promoted: bool = True,
    is_baseline: bool = False,
    metrics: dict | None = None,
    baseline_metrics: dict | None = None,
) -> uuid.UUID:
    version_id = uuid7()
    db_session.add(
        ModelVersionRow(
            id=version_id,
            name=name,
            version="2026-10-02",
            artifact_uri="s3://models/risk.ubj",
            metrics=metrics,
            baseline_metrics=baseline_metrics,
            is_baseline=is_baseline,
            thresholds={"high": 0.7, "critical": 0.85},
            promoted=promoted,
            created_at=_CREATED,
        )
    )
    await db_session.commit()
    return version_id


async def _prediction(
    db_session: AsyncSession,
    *,
    cell_id: int,
    version_id: uuid.UUID,
    event_type: str,
    horizon_start: date,
    probability: float = 0.8,
) -> uuid.UUID:
    prediction_id = uuid7()
    db_session.add(
        RiskPredictionRow(
            id=prediction_id,
            cell_id=cell_id,
            model_version_id=version_id,
            event_type=event_type,
            horizon_start=horizon_start,
            horizon_days=31,
            probability=probability,
            severity="high",
            top_factors=[
                {"feature": "precip_sum_1m", "value": 900.0, "contribution": 0.42},
            ],
            created_at=_CREATED,
        )
    )
    await db_session.commit()
    return prediction_id


def _client() -> TestClient:
    return TestClient(app)


async def test_it_returns_one_element_per_event_with_its_model_version(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md §Riesgo: `RiskPrediction[]`, `flood` and `drought`, each with
    `model_version: { name, version, is_baseline, metrics }` nested."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    cell_id = await _cell(db_session, 901)
    plot_id = await _make_plot(db_session, org_id, cell_id=cell_id)
    flood = await _version(db_session, name="risk_flood", metrics={"pr_auc": 0.71})
    drought = await _version(
        db_session,
        name="risk_drought",
        promoted=False,
        is_baseline=True,
        baseline_metrics={"brier": 0.2},
    )
    await _prediction(
        db_session, cell_id=cell_id, version_id=flood, event_type="flood", horizon_start=_THIS_MONTH
    )
    await _prediction(
        db_session,
        cell_id=cell_id,
        version_id=drought,
        event_type="drought",
        horizon_start=_THIS_MONTH,
        probability=0.3,
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert [item["event_type"] for item in body] == ["flood", "drought"]
    assert body[0]["horizon_start"] == _THIS_MONTH.isoformat()
    assert body[0]["horizon_days"] == 31
    assert body[0]["probability"] == pytest.approx(0.8)
    assert body[0]["severity"] == "high"
    assert body[0]["top_factors"] == [
        {"feature": "precip_sum_1m", "value": 900.0, "contribution": 0.42}
    ]
    assert body[0]["model_version"] == {
        "name": "risk_flood",
        "version": "2026-10-02",
        "is_baseline": False,
        "metrics": {"pr_auc": 0.71},
    }
    # A served baseline reports the numbers docs/03-modelo-datos.md stores in
    # `baseline_metrics`; `is_baseline` says which kind of version answered.
    assert body[1]["model_version"] == {
        "name": "risk_drought",
        "version": "2026-10-02",
        "is_baseline": True,
        "metrics": {"brier": 0.2},
    }


async def test_a_plot_without_a_cell_answers_an_empty_list(db_session: AsyncSession) -> None:
    """docs/04-api.md §Riesgo: una parcela sin celda responde `[]`."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id, cell_id=None)

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == []


async def test_a_plot_of_another_organization_answers_404(db_session: AsyncSession) -> None:
    """docs/09-cuellos-de-botella.md §Seguridad and docs/04 (mismo control de acceso
    que `/plots/{plot_id}`): another org's plot is not found, never forbidden, and
    its cell's risk never leaks."""
    _other_org_id, _user_id, token = await _member(db_session, role="owner")
    other_org_id, _uid2, _token2 = await _member(db_session, role="owner", org_name="Otra")
    cell_id = await _cell(db_session, 902)
    plot_id = await _make_plot(db_session, other_org_id, cell_id=cell_id)
    version_id = await _version(db_session, name="risk_flood")
    await _prediction(
        db_session,
        cell_id=cell_id,
        version_id=version_id,
        event_type="flood",
        horizon_start=_THIS_MONTH,
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.status_code == 404
    assert "risk" not in response.text


async def test_an_unknown_plot_answers_404(db_session: AsyncSession) -> None:
    _org_id, _user_id, token = await _member(db_session, role="owner")

    response = _client().get(f"/api/v1/plots/{uuid7()}/risk", headers=_auth(token))

    assert response.status_code == 404


async def test_a_request_without_a_token_answers_401(db_session: AsyncSession) -> None:
    org_id, _user_id, _token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id, cell_id=None)

    response = _client().get(f"/api/v1/plots/{plot_id}/risk")

    assert response.status_code == 401


async def test_the_most_recent_prediction_is_served_when_the_month_has_none(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md §Riesgo: si el mes en curso todavía no tiene predicción (ERA5
    llega con ~5 días de retraso), la más reciente."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    cell_id = await _cell(db_session, 903)
    plot_id = await _make_plot(db_session, org_id, cell_id=cell_id)
    version_id = await _version(db_session, name="risk_flood")
    await _prediction(
        db_session,
        cell_id=cell_id,
        version_id=version_id,
        event_type="flood",
        horizon_start=_LAST_MONTH,
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert [item["horizon_start"] for item in body] == [_LAST_MONTH.isoformat()]


async def test_an_event_with_no_registered_version_does_not_appear(
    db_session: AsyncSession,
) -> None:
    """Un evento sin predicción no aparece (docs/04-api.md §Riesgo): with only
    `risk_flood` registered, the response carries flood alone."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    cell_id = await _cell(db_session, 904)
    plot_id = await _make_plot(db_session, org_id, cell_id=cell_id)
    version_id = await _version(db_session, name="risk_flood")
    await _prediction(
        db_session,
        cell_id=cell_id,
        version_id=version_id,
        event_type="flood",
        horizon_start=_THIS_MONTH,
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert [item["event_type"] for item in response.json()] == ["flood"]


async def test_the_prediction_of_another_cell_is_not_served(db_session: AsyncSession) -> None:
    """A prediction belongs to its cell: another cell's risk is never this plot's,
    even when both cells are active (docs/09-cuellos-de-botella.md:39)."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    mine = await _cell(db_session, 905)
    other = await _cell(db_session, 906)
    plot_id = await _make_plot(db_session, org_id, cell_id=mine)
    version_id = await _version(db_session, name="risk_flood")
    await _prediction(
        db_session,
        cell_id=other,
        version_id=version_id,
        event_type="flood",
        horizon_start=_THIS_MONTH,
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == []


async def test_the_prediction_of_another_version_is_not_served(db_session: AsyncSession) -> None:
    """Only the served version's prediction is served, so promoting a version does
    not show a cell a month the new version has not predicted yet
    (docs/04-api.md §Riesgo: la versión promovida o, sin ella, la línea base)."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    cell_id = await _cell(db_session, 907)
    plot_id = await _make_plot(db_session, org_id, cell_id=cell_id)
    old = await _version(db_session, name="risk_flood", promoted=False)
    await _version(db_session, name="risk_flood", promoted=True)
    await _prediction(
        db_session, cell_id=cell_id, version_id=old, event_type="flood", horizon_start=_THIS_MONTH
    )

    response = _client().get(f"/api/v1/plots/{plot_id}/risk", headers=_auth(token))

    assert response.json() == []
