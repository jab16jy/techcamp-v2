"""Tests for alerts home facade read queries (E9 T1b; docs/04 §Estado; D-T0.3)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import list_open_alerts_for_plots
from techcamp.alerts.domain.models import AlertState, Severity
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _make_context(
    db_session: AsyncSession, name: str = "Org Alert"
) -> tuple[UUID, UUID, UUID]:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name=name, kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name=name, municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    await db_session.commit()
    return org_id, farm_id, plot_id


async def _make_rule(db_session: AsyncSession, *, code: str, severity: str = "warning") -> UUID:
    rule_id = uuid7()
    db_session.add(
        AlertRuleRow(
            id=rule_id,
            org_id=None,
            code=code,
            metric="soil_moisture",
            operator="<",
            threshold=20.0,
            hysteresis=0.0,
            min_duration_min=0,
            severity=severity,
        )
    )
    await db_session.commit()
    return rule_id


async def test_list_open_alerts_for_plots_ordering_and_filtering(
    db_session: AsyncSession,
) -> None:
    org_a, _farm_a, plot_a = await _make_context(db_session, "Org A")
    org_b, _farm_b, plot_b = await _make_context(db_session, "Org B")

    rule_warn = await _make_rule(db_session, code="rule_warn", severity="warning")
    rule_crit = await _make_rule(db_session, code="rule_crit", severity="critical")
    rule_info = await _make_rule(db_session, code="rule_info", severity="info")

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    # 1. Critical alert opened older (now - 2 hours) -> must be first due to severity
    alert_crit = uuid7()
    db_session.add(
        AlertRow(
            id=alert_crit,
            org_id=org_a,
            rule_id=rule_crit,
            plot_id=plot_a,
            node_id=None,
            state=AlertState.OPEN.value,
            severity=Severity.CRITICAL.value,
            opened_at=now - timedelta(hours=2),
        )
    )

    # 2. Warning alert opened newer (now - 30 minutes) -> must be second (warning after critical)
    alert_warn_new = uuid7()
    db_session.add(
        AlertRow(
            id=alert_warn_new,
            org_id=org_a,
            rule_id=rule_warn,
            plot_id=plot_a,
            node_id=None,
            state=AlertState.OPEN.value,
            severity=Severity.WARNING.value,
            opened_at=now - timedelta(minutes=30),
        )
    )

    # 3. Acknowledged alert -> must be included (state <> 'resolved')
    alert_ack = uuid7()
    db_session.add(
        AlertRow(
            id=alert_ack,
            org_id=org_a,
            rule_id=rule_info,
            plot_id=plot_a,
            node_id=None,
            state=AlertState.ACKNOWLEDGED.value,
            severity=Severity.INFO.value,
            opened_at=now - timedelta(hours=3),
            acknowledged_at=now - timedelta(hours=1),
        )
    )

    # 4. Resolved alert -> must be EXCLUDED!
    rule_resolved = await _make_rule(db_session, code="rule_resolved", severity="warning")
    alert_resolved = uuid7()
    db_session.add(
        AlertRow(
            id=alert_resolved,
            org_id=org_a,
            rule_id=rule_resolved,
            plot_id=plot_a,
            node_id=None,
            state=AlertState.RESOLVED.value,
            severity=Severity.WARNING.value,
            opened_at=now - timedelta(hours=4),
            resolved_at=now - timedelta(hours=1),
        )
    )

    # 5. Alert in org_b -> must be EXCLUDED when querying org_a!
    rule_b = await _make_rule(db_session, code="rule_b", severity="critical")
    alert_b = uuid7()
    db_session.add(
        AlertRow(
            id=alert_b,
            org_id=org_b,
            rule_id=rule_b,
            plot_id=plot_b,
            node_id=None,
            state=AlertState.OPEN.value,
            severity=Severity.CRITICAL.value,
            opened_at=now - timedelta(minutes=5),
        )
    )

    await db_session.commit()

    repo = SqlAlchemyAlertRepository(db_session)
    results = await list_open_alerts_for_plots(
        plot_ids=[plot_a],
        org_ids=[org_a],
        alerts=repo,
    )

    result_ids = [a.id for a in results]

    # Order check: critical before newer warning, then older info
    assert result_ids == [alert_crit, alert_warn_new, alert_ack]

    # Content check: carry severity and plot_id
    assert results[0].severity == Severity.CRITICAL
    assert results[0].plot_id == plot_a
    assert results[1].severity == Severity.WARNING
    assert results[1].plot_id == plot_a

    # Negative checks
    assert alert_resolved not in result_ids
    assert alert_b not in result_ids


async def test_list_open_alerts_for_plots_empty_inputs(
    db_session: AsyncSession,
) -> None:
    repo = SqlAlchemyAlertRepository(db_session)
    assert await list_open_alerts_for_plots(plot_ids=[], org_ids=[uuid7()], alerts=repo) == []
    assert await list_open_alerts_for_plots(plot_ids=[uuid7()], org_ids=[], alerts=repo) == []
