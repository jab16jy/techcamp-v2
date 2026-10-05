"""The organization-indicators endpoint (docs/04-api.md:226, 236; D-T0.10, D-T0.12,
D-T7.1).

Against the real database and the real router: the mean ignores the plots without
an index, the ratios are computed over the stored month, and org isolation is
adapter behavior — a double at the port would prove none of it.

D-T7.1 ships this **partial**: `harvested_cycles_ratio` and
`median_hours_to_first_reading` arrive as `null`, because the org-month listing
carries nothing about cycles and nothing about node instants
(docs/03-modelo-datos.md:441 — a figure with no evidence is `null`, never `0`).
Both unlock in the follow-up lane that adds the two `metrics_*` views and their
`source_repository` methods.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from home.conftest import BOUNDARY, make_env
from metrics.conftest import MONTH
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.metrics.adapters.orm import PlotMetricMonthlyRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_COMPUTED_AT = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: what the month-1 02:00 job wrote (D-T0.7)."""

_MONTH_QUERY = "2026-09"
_phone_seq = iter(range(1, 10_000))


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _phone() -> str:
    return f"+57300{next(_phone_seq):05d}"


async def _add_plot(db_session: AsyncSession, *, env: Any, name: str = "Lote 2") -> UUID:
    """A second plot inside the env's own organization.

    `make_env` builds one plot per org, and the mean of docs/11 §2 is over the
    organization, so a second plot of the same org is what makes the average
    mean something other than "the one plot's figure".
    """
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=env.org_id,
            farm_id=env.farm_id,
            name=name,
            boundary=BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()
    return plot_id


async def _stored_month(
    db_session: AsyncSession,
    *,
    plot_id: UUID,
    org_id: UUID,
    monitoring: float | None,
    record_keeping: float | None,
    decision: float | None,
    risk_management: float | None,
    index: float | None,
    month: Any = MONTH,
) -> None:
    """One stored plot-month, so the test states the figures it expects.

    A component and the index are both `None` together when the plot had no
    evidence at all that month (D-T0.3: all four null means the index is null).
    """
    db_session.add(
        PlotMetricMonthlyRow(
            plot_id=plot_id,
            month=month,
            org_id=org_id,
            monitoring=None if monitoring is None else Decimal(str(monitoring)),
            record_keeping=None if record_keeping is None else Decimal(str(record_keeping)),
            decision=None if decision is None else Decimal(str(decision)),
            risk_management=None if risk_management is None else Decimal(str(risk_management)),
            digital_adoption_index=None if index is None else Decimal(str(index)),
            computed_at=_COMPUTED_AT,
        )
    )
    await db_session.commit()


async def _month_rows(db_session: AsyncSession, *, org_id: UUID, month: Any = MONTH) -> int:
    return (
        await db_session.execute(
            select(func.count())
            .select_from(PlotMetricMonthlyRow)
            .where(PlotMetricMonthlyRow.org_id == org_id, PlotMetricMonthlyRow.month == month)
        )
    ).scalar_one()


async def test_an_owner_reads_the_three_computed_figures(db_session: AsyncSession) -> None:
    """docs/11-metricas.md:75 — the mean is over the plots **with an index**, so a
    plot with no evidence at all must not drag it down as a zero.

    Plot 1 has all four components and an index of 50; plot 2 has none and no
    index. So the mean is 50 (not 25), `plots_with_index` is 1, and the
    monitored ratio is 1/2 because only plot 1 had a claimed node that month.
    """
    env = await make_env(db_session, role="owner")
    second = await _add_plot(db_session, env=env)
    await _stored_month(
        db_session,
        plot_id=env.plot_id,
        org_id=env.org_id,
        monitoring=0.5,
        record_keeping=0.25,
        decision=0.75,
        risk_management=0.25,
        index=50,
    )
    await _stored_month(
        db_session,
        plot_id=second,
        org_id=env.org_id,
        monitoring=None,
        record_keeping=None,
        decision=None,
        risk_management=None,
        index=None,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["org_id"] == str(env.org_id)
    assert body["month"] == "2026-09-01"
    assert body["mean_digital_adoption_index"] == 50.0
    assert body["plots_with_index"] == 1
    assert body["monitored_plots_ratio"] == 0.5


async def test_the_two_locked_figures_arrive_as_null(db_session: AsyncSession) -> None:
    """D-T7.1: `harvested_cycles_ratio` and `median_hours_to_first_reading` are `null`
    even when the month has rows — they are not `0`, which would say an
    organization harvested nothing and every node arrived instantly."""
    env = await make_env(db_session, role="owner")
    await _stored_month(
        db_session,
        plot_id=env.plot_id,
        org_id=env.org_id,
        monitoring=0.5,
        record_keeping=0.5,
        decision=0.5,
        risk_management=0.5,
        index=50,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["harvested_cycles_ratio"] is None
    assert body["median_hours_to_first_reading"] is None


async def test_a_month_nobody_reported_is_all_null(db_session: AsyncSession) -> None:
    """No stored row for the asked month is missing evidence for every figure, so
    the endpoint answers `200` with nulls rather than `404`: the organization
    exists, the month simply has no report yet (docs/04-api.md:236)."""
    env = await make_env(db_session, role="owner")
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["org_id"] == str(env.org_id)
    assert body["month"] == "2026-09-01"
    assert body["mean_digital_adoption_index"] is None
    assert body["plots_with_index"] is None
    assert body["monitored_plots_ratio"] is None
    assert body["harvested_cycles_ratio"] is None
    assert body["median_hours_to_first_reading"] is None


async def test_another_month_of_the_same_org_is_null(db_session: AsyncSession) -> None:
    env = await make_env(db_session, role="owner")
    await _stored_month(
        db_session,
        plot_id=env.plot_id,
        org_id=env.org_id,
        monitoring=0.5,
        record_keeping=0.5,
        decision=0.5,
        risk_management=0.5,
        index=50,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": "2026-10"},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    assert response.json()["mean_digital_adoption_index"] is None


async def test_a_stored_month_of_another_org_is_not_counted(db_session: AsyncSession) -> None:
    """docs/09-cuellos-de-botella.md#seguridad: the org-month listing filters on
    `org_id`, so another organization's figures are absent, not averaged in."""
    env_a = await make_env(db_session, role="owner")
    env_b = await make_env(db_session, role="owner")
    await _stored_month(
        db_session,
        plot_id=env_b.plot_id,
        org_id=env_b.org_id,
        monitoring=0.5,
        record_keeping=0.5,
        decision=0.5,
        risk_management=0.5,
        index=80,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env_a.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env_a.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mean_digital_adoption_index"] is None
    assert body["plots_with_index"] is None
    assert await _month_rows(db_session, org_id=env_a.org_id) == 0


async def test_a_technician_reads_them(db_session: AsyncSession) -> None:
    """D-T0.10: owner or technician, like the extension-visit export."""
    env = await make_env(db_session, role="technician")
    await _stored_month(
        db_session,
        plot_id=env.plot_id,
        org_id=env.org_id,
        monitoring=1.0,
        record_keeping=1.0,
        decision=1.0,
        risk_management=1.0,
        index=100,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mean_digital_adoption_index"] == 100.0
    assert body["monitored_plots_ratio"] == 1.0


@pytest.mark.parametrize("role", ["producer", "viewer"])
async def test_another_role_is_403(db_session: AsyncSession, role: str) -> None:
    """D-T0.10: the organization's indicators are owner/technician only. The stored
    row must be untouched by the refused call — the negative assertion."""
    env = await make_env(db_session, role=role)
    await _stored_month(
        db_session,
        plot_id=env.plot_id,
        org_id=env.org_id,
        monitoring=0.5,
        record_keeping=0.5,
        decision=0.5,
        risk_management=0.5,
        index=50,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert "mean_digital_adoption_index" not in response.text
    assert await _month_rows(db_session, org_id=env.org_id) == 1


async def test_an_org_of_another_organization_is_404(db_session: AsyncSession) -> None:
    """A caller who is not a member gets `404`, never `403`: the endpoint must
    not reveal that the organization exists (docs/04-api.md:236,
    docs/09-cuellos-de-botella.md#seguridad)."""
    env_a = await make_env(db_session, role="owner")
    env_b = await make_env(db_session, role="owner")
    client = _client()

    response = client.get(
        f"/organizations/{env_b.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(env_a.user_id))),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_member_of_two_orgs_reads_only_the_asked_one(db_session: AsyncSession) -> None:
    env_a = await make_env(db_session, role="owner")
    env_b = await make_env(db_session, role="owner")
    other_user = uuid7()
    db_session.add(AppUserRow(id=other_user, phone=_phone()))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=env_a.org_id, user_id=other_user, role="owner"))
    db_session.add(MembershipRow(org_id=env_b.org_id, user_id=other_user, role="technician"))
    await db_session.commit()
    await _stored_month(
        db_session,
        plot_id=env_a.plot_id,
        org_id=env_a.org_id,
        monitoring=1.0,
        record_keeping=1.0,
        decision=1.0,
        risk_management=1.0,
        index=40,
    )
    client = _client()

    response = client.get(
        f"/organizations/{env_a.org_id}/metrics",
        params={"month": _MONTH_QUERY},
        headers=_auth(issue_token(str(other_user))),
    )

    assert response.status_code == 200, response.text
    assert response.json()["mean_digital_adoption_index"] == 40.0


@pytest.mark.parametrize("month", ["2026-13", "202609", "2026-9", "septiembre", ""])
async def test_a_malformed_month_is_422(db_session: AsyncSession, month: str) -> None:
    env = await make_env(db_session, role="owner")
    client = _client()

    response = client.get(
        f"/organizations/{env.org_id}/metrics",
        params={"month": month},
        headers=_auth(issue_token(str(env.user_id))),
    )

    assert response.status_code == 422, response.text


async def test_org_metrics_without_a_token_is_401(db_session: AsyncSession) -> None:
    env = await make_env(db_session, role="owner")
    client = _client()

    response = client.get(f"/organizations/{env.org_id}/metrics", params={"month": _MONTH_QUERY})

    assert response.status_code == 401
