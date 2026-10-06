"""The metrics read endpoints (docs/04-api.md:224-226, 234-235; D-T0.8, D-T0.10).

Against the real database and the real router: which row a cycle summary comes
from — the stored one for a finished cycle, a computation for an active one
(D-T0.8) — is a branch on adapter behavior, and so is org isolation, so a double
at the port would prove neither.

Every negative test carries its negative assertion: the state the rejected call
must not have changed follows the call.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from home.conftest import make_env
from metrics.conftest import MONTH, add_cycle, add_logbook_entry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.metrics.adapters.orm import CropCycleSummaryRow, PlotMetricMonthlyRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_COMPUTED_AT = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: what the month-1 02:00 job wrote (D-T0.7)."""

_MONTH_QUERY = "2026-09"
_CYCLE = {"sown_on": date(2026, 9, 1), "expected_harvest_on": date(2026, 9, 30)}


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _stored_month(db_session: AsyncSession, *, plot_id: UUID, org_id: UUID) -> None:
    """One stored plot-month with three components and a null `decision`.

    The null component is the interesting figure: a rainfed plot has no applied
    depth to follow (docs/11-metricas.md:52), so it must reach the wire as `null`
    and never as 0 (D-T0.3). The index is the 100 points split over the three
    components that do have a denominator: `100 × (0.5 + 0.25 + 0.75) / 3 = 50`.
    """
    db_session.add(
        PlotMetricMonthlyRow(
            plot_id=plot_id,
            month=MONTH,
            org_id=org_id,
            monitoring=Decimal("0.5"),
            record_keeping=Decimal("0.25"),
            decision=None,
            risk_management=Decimal("0.75"),
            digital_adoption_index=Decimal("50"),
            computed_at=_COMPUTED_AT,
        )
    )
    await db_session.commit()


async def _stored_summary(
    db_session: AsyncSession, *, cycle_id: UUID, plot_id: UUID, org_id: UUID
) -> None:
    """The row the monthly job stored for a finished cycle.

    Every figure is left null except `yield_kg_ha`, a value no computation over an
    empty logbook could produce: that is what proves the read answers the stored
    row instead of recomputing it.
    """
    db_session.add(
        CropCycleSummaryRow(
            crop_cycle_id=cycle_id,
            plot_id=plot_id,
            org_id=org_id,
            yield_kg_ha=Decimal("1234.5"),
            computed_at=_COMPUTED_AT,
        )
    )
    await db_session.commit()


async def _summary_rows(db_session: AsyncSession) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(CropCycleSummaryRow))
    ).scalar_one()


async def test_a_member_reads_the_stored_month(db_session: AsyncSession) -> None:
    env = await make_env(db_session, role="producer")
    await _stored_month(db_session, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plot_id"] == str(env.plot_id)
    assert body["month"] == "2026-09-01"
    assert body["monitoring"] == 0.5
    assert body["record_keeping"] == 0.25
    assert body["decision"] is None
    assert body["risk_management"] == 0.75
    assert body["digital_adoption_index"] == 50.0
    assert body["computed_at"].startswith("2026-10-01T07:00:00")


async def test_a_viewer_reads_the_month(db_session: AsyncSession) -> None:
    """D-T0.10: the metrics read is for any member, as the baseline `GET` is."""
    env = await make_env(db_session, role="viewer")
    await _stored_month(db_session, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    assert response.json()["plot_id"] == str(env.plot_id)


async def test_a_month_without_a_row_is_404(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_another_month_is_404_even_when_one_is_stored(db_session: AsyncSession) -> None:
    """The `month` of the query is the month of the read: a stored September row
    must not answer an October request (docs/03-modelo-datos.md:442 — the row is
    keyed by its month)."""
    env = await make_env(db_session)
    await _stored_month(db_session, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics",
        params={"month": "2026-10"},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "month", ["2026-13", "2026-00", "202609", "2026-9", "2026-09-01", "septiembre", ""]
)
async def test_a_malformed_month_is_422(db_session: AsyncSession, month: str) -> None:
    """docs/04-api.md:234: "un `month` mal formado responde `422`". A month the
    job never stored cannot be a 404, and a day-precision or free-text value is
    not the `YYYY-MM` the query declares."""
    env = await make_env(db_session)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics",
        params={"month": month},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 422, response.text


async def test_a_missing_month_is_422(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/metrics", headers=_auth(issue_token(str(env.user_id)))
    )

    assert response.status_code == 422, response.text


async def test_a_plot_in_another_org_is_404(db_session: AsyncSession) -> None:
    """docs/09-cuellos-de-botella.md#seguridad: another organization's plot is not
    found at all, and its stored figures never leak."""
    _env_a = await make_env(db_session, role="owner")
    env_b = await make_env(db_session, role="owner")
    await _stored_month(db_session, plot_id=env_b.plot_id, org_id=env_b.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env_b.plot_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(_env_a.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_plot_metrics_without_a_token_is_401(db_session: AsyncSession) -> None:
    """#244 WARNING 006 (deferred from T2): the anonymous-request test this
    surface was missing. Without it, a route that dropped its auth dependency
    would keep every other test in this file green."""
    env = await make_env(db_session)
    await _stored_month(db_session, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(f"/plots/{env.plot_id}/metrics", params={"month": _MONTH_QUERY})

    assert response.status_code == 401


async def test_a_finished_cycle_answers_its_stored_row(db_session: AsyncSession) -> None:
    """D-T0.8: a `harvested` cycle returns what the job stored, and the row's
    `yield_kg_ha` of 1234.5 with an empty logbook is what proves it."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, status="harvested", **_CYCLE)
    await _stored_summary(db_session, cycle_id=cycle_id, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["crop_cycle_id"] == str(cycle_id)
    assert body["plot_id"] == str(env.plot_id)
    assert body["cycle_status"] == "harvested"
    assert body["yield_kg_ha"] == 1234.5
    assert body["computed_at"].startswith("2026-10-01T07:00:00")
    # Null metrics reach the wire as null, not as zero (docs/03-modelo-datos.md:441),
    # and `relative_yield` is always null in E11 (D-T0.9).
    assert body["relative_yield"] is None
    assert body["water_stress_days"] is None
    assert body["loss_kg"] is None


async def test_an_active_cycle_is_computed_on_read_and_not_stored(
    db_session: AsyncSession,
) -> None:
    """D-T0.8, second half: an active cycle has no stored row, so the endpoint
    computes the same impact from the views and must not write it."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, status="active", **_CYCLE)
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["crop_cycle_id"] == str(cycle_id)
    assert body["cycle_status"] == "active"
    # 500 kg over the plot PostGIS computed: not null proves the views fed the
    # math, and the exact figure is `test_cycle_summary_domain`'s business.
    assert body["yield_kg_ha"] is not None
    assert body["yield_kg_ha"] > 0

    assert await _summary_rows(db_session) == 0

    again = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )
    assert again.status_code == 200, again.text
    assert again.json()["yield_kg_ha"] == body["yield_kg_ha"]
    assert await _summary_rows(db_session) == 0


async def test_a_finished_cycle_without_a_stored_row_is_404(
    db_session: AsyncSession,
) -> None:
    """The stored row is the resource for a finished cycle: a `GET` never
    recomputes and never writes one, so a cycle the job has not summarized yet
    has nothing to answer with (docs/03-modelo-datos.md:443)."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, status="harvested", **_CYCLE)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert await _summary_rows(db_session) == 0


async def test_a_cycle_of_another_plot_is_404(db_session: AsyncSession) -> None:
    """docs/04-api.md:235: "un ciclo de otra parcela responde `404`" — the path's
    plot owns the cycle, not just the organization."""
    env = await make_env(db_session)
    other = await make_env(db_session)
    cycle_id = await add_cycle(db_session, other, status="active", **_CYCLE)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_cycle_in_another_org_is_404(db_session: AsyncSession) -> None:
    _env_a = await make_env(db_session)
    env_b = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env_b, status="active", **_CYCLE)
    await add_logbook_entry(
        db_session,
        env_b,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )
    client = _client()

    response = client.get(
        f"/plots/{env_b.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(_env_a.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_viewer_reads_the_cycle_summary(db_session: AsyncSession) -> None:
    env = await make_env(db_session, role="viewer")
    cycle_id = await add_cycle(db_session, env, status="harvested", **_CYCLE)
    await _stored_summary(db_session, cycle_id=cycle_id, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{cycle_id}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    assert response.json()["crop_cycle_id"] == str(cycle_id)


async def test_cycle_summary_without_a_token_is_401(db_session: AsyncSession) -> None:
    """#244 WARNING 006, second route of this surface."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, status="harvested", **_CYCLE)
    await _stored_summary(db_session, cycle_id=cycle_id, plot_id=env.plot_id, org_id=env.org_id)
    client = _client()

    response = client.get(f"/plots/{env.plot_id}/cycles/{cycle_id}/summary")

    assert response.status_code == 401


async def test_an_unknown_cycle_is_404(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    client = _client()

    response = client.get(
        f"/plots/{env.plot_id}/cycles/{uuid7()}/summary",
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 404
