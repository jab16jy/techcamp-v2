"""Tests for technician tray: `GET /me/tray` and use case (E9 T3).

docs/04 §Visitas de extensión y bandeja del técnico; docs/04 §Estado de la parcela;
docs/05 D-T0.1; docs/09 #seguridad; feature doc D-T0.3, D-T0.8; closes #210.

Rules tested:
- Multi-org technician: farms in two orgs, alerts in both (end-to-end multi-org proof).
- User with no assigned farm -> [] (200, never 403).
- Farm assigned to someone else excluded.
- Farm in an unjoined org excluded even if technician_id matches (org isolation, docs/09).
- Ordering:
  * open critical alerts desc (critical beats more warnings)
  * open alerts desc
  * last_visit_on asc with null first (never-visited before visited; older before newer)
  * farm name asc (name tiebreak)
  * farm id asc
- Deleted visit ignored (last_visit_on is null or older valid visit).
- Resolved alert not counted in open_alerts and does not affect order.
- Unauthenticated request returns 401.

Every test carries its negative assertion.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.home.application import build_technician_tray
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.irrigation.adapters.api.deps import get_now
from techcamp.main import app
from techcamp.shared.ids import uuid7

from .conftest import (
    NOW,
    TODAY,
    HomeEnv,
    add_alert,
    add_farm,
    add_plot,
    add_visit,
    make_env,
    tray_repos,
)

pytestmark = pytest.mark.anyio


def _client() -> TestClient:
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app, base_url="http://testserver/api/v1")


@pytest.fixture
def client() -> TestClient:
    test_client = _client()
    yield test_client
    app.dependency_overrides.clear()


async def test_multi_org_technician_tray(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """Closes #210: a technician assigned to farms in two different orgs sees both

    in their tray with their respective open alerts and last visit dates.
    """
    tech_id = uuid7()
    db_session.add(
        AppUserRow(id=tech_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Carlos")
    )
    org_1, org_2 = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_1, name="Asociación Norte", kind="cooperative"))
    db_session.add(OrganizationRow(id=org_2, name="Asociación Sur", kind="cooperative"))
    await db_session.commit()

    db_session.add(MembershipRow(org_id=org_1, user_id=tech_id, role="technician"))
    db_session.add(MembershipRow(org_id=org_2, user_id=tech_id, role="technician"))
    await db_session.commit()

    # Farm 1 in Org 1 with an alert and a visit
    farm_1 = await add_farm(db_session, org_id=org_1, name="Finca Norte 1", technician_id=tech_id)
    plot_1 = await add_plot(db_session, org_id=org_1, farm_id=farm_1, name="Lote Norte")
    dummy_env_1 = HomeEnv(
        session=db_session, org_id=org_1, user_id=tech_id, farm_id=farm_1, plot_id=plot_1
    )
    alert_1 = await add_alert(
        db_session,
        dummy_env_1,
        severity="critical",
        rule_code="water_stress",
        at=NOW - timedelta(hours=3),
    )
    visit_1_date = TODAY - timedelta(days=5)
    await add_visit(
        db_session, org_id=org_1, farm_id=farm_1, technician_id=tech_id, visited_on=visit_1_date
    )

    # Farm 2 in Org 2 with an alert, no visits
    farm_2 = await add_farm(db_session, org_id=org_2, name="Finca Sur 1", technician_id=tech_id)
    plot_2 = await add_plot(db_session, org_id=org_2, farm_id=farm_2, name="Lote Sur")
    dummy_env_2 = HomeEnv(
        session=db_session, org_id=org_2, user_id=tech_id, farm_id=farm_2, plot_id=plot_2
    )
    alert_2 = await add_alert(
        db_session,
        dummy_env_2,
        severity="warning",
        rule_code="heat_stress",
        at=NOW - timedelta(hours=1),
    )

    # Application use case verification
    tray = await build_technician_tray(user_id=tech_id, **tray_repos(db_session))

    assert len(tray) == 2
    # Critical alert farm comes first
    assert tray[0].farm.id == farm_1
    assert tray[0].farm.org_id == org_1
    assert tray[0].farm.name == "Finca Norte 1"
    assert tray[0].farm.municipality_code == "47001"
    assert len(tray[0].open_alerts) == 1
    assert tray[0].open_alerts[0].id == alert_1
    assert tray[0].open_alerts[0].severity == "critical"
    assert tray[0].last_visit_on == visit_1_date

    assert tray[1].farm.id == farm_2
    assert tray[1].farm.org_id == org_2
    assert tray[1].farm.name == "Finca Sur 1"
    assert len(tray[1].open_alerts) == 1
    assert tray[1].open_alerts[0].id == alert_2
    assert tray[1].open_alerts[0].severity == "warning"
    assert tray[1].last_visit_on is None

    # HTTP endpoint verification (closes #210 end-to-end)
    token = issue_token(str(tech_id))
    resp = client.get("/me/tray", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 2
    assert data[0]["farm"]["id"] == str(farm_1)
    assert data[0]["farm"]["org_id"] == str(org_1)
    assert data[0]["last_visit_on"] == visit_1_date.isoformat()
    assert data[0]["open_alerts"][0]["id"] == str(alert_1)
    assert data[1]["farm"]["id"] == str(farm_2)
    assert data[1]["last_visit_on"] is None

    # Negative: a third unassigned farm in org 1 is not in the tray
    unassigned_farm = await add_farm(
        db_session, org_id=org_1, name="Finca Unassigned", technician_id=None
    )
    tray_after = await build_technician_tray(user_id=tech_id, **tray_repos(db_session))
    farm_ids = [item.farm.id for item in tray_after]
    assert unassigned_farm not in farm_ids


async def test_user_with_no_assigned_farms_returns_empty_list(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """A user with no assigned farm gets [] (200, never 403)."""
    env = await make_env(db_session, role="producer")

    tray = await build_technician_tray(user_id=env.user_id, **tray_repos(db_session))
    assert tray == []

    # API returns 200 [], not 403
    token = issue_token(str(env.user_id))
    resp = client.get("/me/tray", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []

    # Negative: 403 is never returned for empty tray
    assert resp.status_code != 403


async def test_farm_assigned_to_someone_else_excluded(
    db_session: AsyncSession,
) -> None:
    """Farms assigned to other technicians are excluded from the caller's tray."""
    env = await make_env(db_session)
    other_tech = uuid7()
    db_session.add(
        AppUserRow(id=other_tech, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Other")
    )
    await db_session.commit()
    db_session.add(MembershipRow(org_id=env.org_id, user_id=other_tech, role="technician"))
    await db_session.commit()

    my_farm = await add_farm(
        db_session, org_id=env.org_id, name="My Farm", technician_id=env.user_id
    )
    other_farm = await add_farm(
        db_session, org_id=env.org_id, name="Other Farm", technician_id=other_tech
    )

    tray = await build_technician_tray(user_id=env.user_id, **tray_repos(db_session))

    tray_ids = [item.farm.id for item in tray]
    assert my_farm in tray_ids
    assert other_farm not in tray_ids

    # Negative: other technician sees other_farm, not my_farm
    other_tray = await build_technician_tray(user_id=other_tech, **tray_repos(db_session))
    other_tray_ids = [item.farm.id for item in other_tray]
    assert other_farm in other_tray_ids
    assert my_farm not in other_tray_ids


async def test_farm_in_unjoined_org_excluded_even_if_technician_id_matches(
    db_session: AsyncSession,
) -> None:
    """Org isolation (docs/09): a farm pointing to caller's technician_id in an org

    the caller does not belong to is excluded and never leaks existence.
    """
    env = await make_env(db_session)

    # Another organization the caller is NOT a member of
    unjoined_org = uuid7()
    db_session.add(OrganizationRow(id=unjoined_org, name="Org X", kind="cooperative"))
    await db_session.commit()

    # Farm in unjoined org with caller as technician_id
    foreign_farm = await add_farm(
        db_session, org_id=unjoined_org, name="Foreign Farm", technician_id=env.user_id
    )

    tray = await build_technician_tray(user_id=env.user_id, **tray_repos(db_session))
    tray_ids = [item.farm.id for item in tray]

    assert foreign_farm not in tray_ids
    # Negative: leak check - empty list since caller has no farms in env.org_id
    assert tray == []


async def test_tray_ordering_rules(
    db_session: AsyncSession,
) -> None:
    """Order: open critical alerts desc, then open alerts desc,

    then last_visit_on asc with null first, then farm name, then farm id (D-T0.8).
    """
    env = await make_env(db_session)
    tech = env.user_id

    # Farm 1: 1 critical alert, visited 2026-09-20
    f_crit = await add_farm(db_session, org_id=env.org_id, name="Farm Crit", technician_id=tech)
    p_crit = await add_plot(db_session, org_id=env.org_id, farm_id=f_crit, name="P1")
    await add_alert(
        db_session,
        env,
        severity="critical",
        rule_code="water_stress",
        plot_id=p_crit,
        farm_id=f_crit,
    )
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=f_crit,
        technician_id=tech,
        visited_on=date(2026, 9, 20),
    )

    # Farm 2: 0 critical, 2 warnings, visited 2026-09-01
    f_warn = await add_farm(db_session, org_id=env.org_id, name="Farm Warn", technician_id=tech)
    p_warn = await add_plot(db_session, org_id=env.org_id, farm_id=f_warn, name="P2")
    await add_alert(
        db_session, env, severity="warning", rule_code="heat_stress", plot_id=p_warn, farm_id=f_warn
    )
    await add_alert(
        db_session,
        env,
        severity="warning",
        rule_code="drought_risk",
        plot_id=p_warn,
        farm_id=f_warn,
    )
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=f_warn,
        technician_id=tech,
        visited_on=date(2026, 9, 1),
    )

    # Farm 3: 0 alerts, never visited (last_visit_on = None), name "Farm Never Visited"
    f_never = await add_farm(db_session, org_id=env.org_id, name="Farm Never", technician_id=tech)

    # Farm 4: 0 alerts, visited long ago (2026-01-10)
    f_old = await add_farm(db_session, org_id=env.org_id, name="Farm Old Visit", technician_id=tech)
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=f_old,
        technician_id=tech,
        visited_on=date(2026, 1, 10),
    )

    # Farm 5: 0 alerts, visited recently (2026-09-15)
    f_recent = await add_farm(
        db_session, org_id=env.org_id, name="Farm Recent Visit", technician_id=tech
    )
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=f_recent,
        technician_id=tech,
        visited_on=date(2026, 9, 15),
    )

    # Farm 6 & 7: 0 alerts, never visited, name tiebreak (Alpha before Beta)
    f_alpha = await add_farm(db_session, org_id=env.org_id, name="Alpha Farm", technician_id=tech)
    f_beta = await add_farm(db_session, org_id=env.org_id, name="Beta Farm", technician_id=tech)

    tray = await build_technician_tray(user_id=tech, **tray_repos(db_session))
    ordered_ids = [item.farm.id for item in tray]

    # 1. Critical beats warnings (even though f_warn has 2 alerts and f_crit has 1)
    assert ordered_ids.index(f_crit) < ordered_ids.index(f_warn)

    # 2. Alerts beat no alerts
    assert ordered_ids.index(f_warn) < ordered_ids.index(f_never)

    # 3. Never visited beats visited
    assert ordered_ids.index(f_never) < ordered_ids.index(f_old)

    # 4. Older visit beats newer visit (asc with null first)
    assert ordered_ids.index(f_old) < ordered_ids.index(f_recent)

    # 5. Name tiebreak for unvisited 0-alert farms
    assert ordered_ids.index(f_alpha) < ordered_ids.index(f_beta)
    assert ordered_ids.index(f_beta) < ordered_ids.index(f_never)

    # Negative: Beta does not precede Alpha
    assert not (ordered_ids.index(f_beta) < ordered_ids.index(f_alpha))


async def test_deleted_visit_ignored(
    db_session: AsyncSession,
) -> None:
    """A deleted visit is ignored: last_visit_on is null if all visits deleted."""
    env = await make_env(db_session)
    tech = env.user_id
    farm_id = await add_farm(
        db_session, org_id=env.org_id, name="Farm Del Visit", technician_id=tech
    )

    # Soft-deleted visit
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=farm_id,
        technician_id=tech,
        visited_on=TODAY - timedelta(days=2),
        deleted=True,
    )

    tray = await build_technician_tray(user_id=tech, **tray_repos(db_session))
    assert len(tray) == 1
    assert tray[0].farm.id == farm_id
    assert tray[0].last_visit_on is None

    # Negative: if an older non-deleted visit exists, that one is picked
    older_visit = TODAY - timedelta(days=30)
    await add_visit(
        db_session,
        org_id=env.org_id,
        farm_id=farm_id,
        technician_id=tech,
        visited_on=older_visit,
        deleted=False,
    )
    tray2 = await build_technician_tray(user_id=tech, **tray_repos(db_session))
    assert tray2[0].last_visit_on == older_visit


async def test_resolved_alert_not_counted(
    db_session: AsyncSession,
) -> None:
    """Alerts with state == 'resolved' are excluded from open_alerts."""
    env = await make_env(db_session)
    tech = env.user_id
    farm_id = await add_farm(
        db_session, org_id=env.org_id, name="Farm Res Alert", technician_id=tech
    )
    plot_id = await add_plot(db_session, org_id=env.org_id, farm_id=farm_id, name="P1")

    # Add resolved alert
    await add_alert(
        db_session,
        env,
        severity="critical",
        rule_code="water_stress",
        plot_id=plot_id,
        farm_id=farm_id,
        resolve=True,
    )

    tray = await build_technician_tray(user_id=tech, **tray_repos(db_session))
    assert len(tray) == 1
    assert tray[0].open_alerts == []

    # Negative: an unresolved alert is included
    unresolved = await add_alert(
        db_session,
        env,
        severity="warning",
        rule_code="heat_stress",
        plot_id=plot_id,
        farm_id=farm_id,
        resolve=False,
    )
    tray2 = await build_technician_tray(user_id=tech, **tray_repos(db_session))
    assert len(tray2[0].open_alerts) == 1
    assert tray2[0].open_alerts[0].id == unresolved


def test_unauthenticated_request_returns_401(
    client: TestClient,
) -> None:
    """GET /me/tray without token returns 401."""
    resp = client.get("/me/tray")
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
