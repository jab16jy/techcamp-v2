"""Alerts and alert rules schema behavior (docs/03-modelo-datos.md:272-297, 484; docs/06 §3)."""

from __future__ import annotations

import decimal
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _create_test_plot_and_node(
    db_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org_id = uuid7()
    org = OrganizationRow(id=org_id, name="Org Test", kind="individual")
    db_session.add(org)
    await db_session.commit()

    farm_id = uuid7()
    farm = FarmRow(
        id=farm_id,
        org_id=org_id,
        name="Finca Test",
        municipality_code="47001",
        location=_POINT,
    )
    db_session.add(farm)
    await db_session.commit()

    plot_id = uuid7()
    plot = PlotRow(
        id=plot_id,
        org_id=org_id,
        farm_id=farm_id,
        name="Lote 1",
        boundary=_BOUNDARY,
        irrigation_system="drip",
    )
    db_session.add(plot)
    await db_session.commit()

    node_id = uuid7()
    node = NodeRow(
        id=node_id,
        org_id=org_id,
        plot_id=plot_id,
        transport="wifi",
        claim_code="TEST-NODE-1",
        credential_hash="hash123",
        interval_s=300,
        status="online",
        claimed_at=datetime.now(UTC),
    )
    db_session.add(node)
    await db_session.commit()

    return org_id, plot_id, node_id


async def test_factory_rules_are_present_with_correct_values(db_session: AsyncSession) -> None:
    """Factory rules seeded by migration have org_id=None and match docs/06 §3 specification."""
    result = await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.org_id.is_(None)))
    rules = {r.code: r for r in result.scalars().all()}

    expected_rules = {
        "water_stress": {
            "metric": "soil_moisture",
            "operator": "<",
            "threshold": None,
            "hysteresis": decimal.Decimal("3"),
            "min_duration_min": 360,
            "severity": "warning",
            "crop_id": None,
        },
        "waterlogging": {
            "metric": "soil_moisture",
            "operator": ">",
            "threshold": None,
            "hysteresis": decimal.Decimal("3"),
            "min_duration_min": 1440,
            "severity": "warning",
            "crop_id": None,
        },
        "heat_stress": {
            "metric": "air_temp",
            "operator": ">",
            "threshold": decimal.Decimal("35"),
            "hysteresis": decimal.Decimal("1"),
            "min_duration_min": 180,
            "severity": "warning",
            "crop_id": None,
        },
        "fungal_risk": {
            "metric": "air_rh",
            "operator": ">",
            "threshold": decimal.Decimal("85"),
            "hysteresis": decimal.Decimal("5"),
            "min_duration_min": 600,
            "severity": "warning",
            "crop_id": None,
        },
        "heavy_rain_forecast": {
            "metric": "rain",
            "operator": ">",
            "threshold": decimal.Decimal("50"),
            "hysteresis": decimal.Decimal("0"),
            "min_duration_min": 0,
            "severity": "warning",
            "crop_id": None,
        },
        "flood_risk": {
            "metric": None,
            "operator": None,
            "threshold": None,
            "hysteresis": decimal.Decimal("0"),
            "min_duration_min": 0,
            "severity": "critical",
            "crop_id": None,
        },
        "drought_risk": {
            "metric": None,
            "operator": None,
            "threshold": None,
            "hysteresis": decimal.Decimal("0"),
            "min_duration_min": 0,
            "severity": "critical",
            "crop_id": None,
        },
        "node_offline": {
            "metric": None,
            "operator": None,
            "threshold": None,
            "hysteresis": decimal.Decimal("0"),
            "min_duration_min": 0,
            "severity": "warning",
            "crop_id": None,
        },
        "node_battery_low": {
            "metric": "battery_v",
            "operator": "<",
            "threshold": decimal.Decimal("3.4"),
            "hysteresis": decimal.Decimal("0.1"),
            "min_duration_min": 0,
            "severity": "info",
            "crop_id": None,
        },
    }

    assert len(rules) == 9
    for code, expected in expected_rules.items():
        assert code in rules, f"Missing factory rule: {code}"
        rule = rules[code]
        assert rule.metric == expected["metric"]
        assert rule.operator == expected["operator"]
        assert rule.threshold == expected["threshold"]
        assert rule.hysteresis == expected["hysteresis"]
        assert rule.min_duration_min == expected["min_duration_min"]
        assert rule.severity == expected["severity"]
        assert rule.crop_id == expected["crop_id"]


async def test_factory_rules_survive_per_test_cleanup(db_session: AsyncSession) -> None:
    """Proves factory rules survive per-test TRUNCATE CASCADE cleanup."""
    result = await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.org_id.is_(None)))
    rules = list(result.scalars().all())
    assert len(rules) == 9, "Factory rules must survive db_session cleanup"


async def test_alert_rejects_both_or_neither_target(db_session: AsyncSession) -> None:
    """Alert must have num_nonnulls(plot_id, node_id) = 1."""
    org_id, plot_id, node_id = await _create_test_plot_and_node(db_session)
    result = await db_session.execute(
        select(AlertRuleRow).where(AlertRuleRow.code == "heat_stress")
    )
    rule_id = result.scalar_one().id

    # Neither plot_id nor node_id
    alert_neither = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=None,
        node_id=None,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert_neither)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Both plot_id and node_id
    alert_both = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        node_id=node_id,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert_both)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_second_non_resolved_alert_for_same_rule_and_plot_is_rejected(
    db_session: AsyncSession,
) -> None:
    """Partial unique index prevents multiple non-resolved alerts on (rule_id, plot_id)."""
    org_id, plot_id, _node_id = await _create_test_plot_and_node(db_session)
    result = await db_session.execute(
        select(AlertRuleRow).where(AlertRuleRow.code == "water_stress")
    )
    rule_id = result.scalar_one().id

    alert1_id = uuid7()
    alert1 = AlertRow(
        id=alert1_id,
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        node_id=None,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert1)
    await db_session.commit()

    alert2 = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        node_id=None,
        state="acknowledged",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Resolve first alert
    alert1 = await db_session.get_one(AlertRow, alert1_id)
    alert1.state = "resolved"
    alert1.resolved_at = datetime.now(UTC)
    await db_session.commit()

    # Now a second alert can be created
    alert3 = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        node_id=None,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert3)
    await db_session.commit()


async def test_second_non_resolved_alert_for_same_rule_and_node_is_rejected(
    db_session: AsyncSession,
) -> None:
    """Partial unique index prevents multiple non-resolved alerts on (rule_id, node_id)."""
    org_id, _plot_id, node_id = await _create_test_plot_and_node(db_session)
    result = await db_session.execute(
        select(AlertRuleRow).where(AlertRuleRow.code == "node_offline")
    )
    rule_id = result.scalar_one().id

    alert1_id = uuid7()
    alert1 = AlertRow(
        id=alert1_id,
        org_id=org_id,
        rule_id=rule_id,
        plot_id=None,
        node_id=node_id,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert1)
    await db_session.commit()

    alert2 = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=None,
        node_id=node_id,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Resolve first alert
    alert1 = await db_session.get_one(AlertRow, alert1_id)
    alert1.state = "resolved"
    alert1.resolved_at = datetime.now(UTC)
    await db_session.commit()

    # Now a second alert can be created
    alert3 = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=None,
        node_id=node_id,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert3)
    await db_session.commit()
