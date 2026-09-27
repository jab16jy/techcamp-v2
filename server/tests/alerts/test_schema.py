"""Alerts and alert rules schema behavior (docs/03-modelo-datos.md:272-297, 484; docs/06 §3)."""

from __future__ import annotations

import decimal
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.seed import FACTORY_RULES, seed_factory_rules
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.db import engine
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow

pytestmark = pytest.mark.anyio

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "d4e6f8a0b2c1_add_alerts_and_notifications_schema.py"
)

# The condition and severity docs/06 §3 fixes per factory rule code.
_DOCUMENTED_RULES: dict[str, tuple[str | None, decimal.Decimal | None, int, str]] = {
    "water_stress": ("<", None, 360, "warning"),
    "waterlogging": (">", None, 1440, "warning"),
    "heat_stress": (">", decimal.Decimal("35"), 180, "warning"),
    "fungal_risk": (">", decimal.Decimal("85"), 0, "warning"),
    "heavy_rain_forecast": (">", decimal.Decimal("50"), 0, "warning"),
    "flood_risk": (None, None, 0, "critical"),
    "drought_risk": (None, None, 0, "critical"),
    "node_offline": (None, None, 0, "warning"),
    "node_battery_low": ("<", decimal.Decimal("3.4"), 0, "info"),
}

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


def test_the_alerts_migration_freezes_its_factory_seed_copy() -> None:
    """A revision is frozen once merged: it may not import application code that
    can change underneath it, so the factory rules it inserts are a literal copy
    of the rows. `test_the_migrated_factory_rules_equal_the_runtime_seed` is what
    keeps that copy equal to the runtime seed.
    """
    app_imports = [
        line
        for line in _MIGRATION.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import techcamp", "from techcamp"))
    ]
    assert app_imports == []


async def test_the_migrated_factory_rules_equal_the_runtime_seed(
    db_session: AsyncSession,
) -> None:
    """The revision inserts a frozen copy of `FACTORY_RULES` (it cannot import it),
    so this is the guard that keeps the two copies from drifting apart."""
    rows = (
        await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.org_id.is_(None)))
    ).scalars()
    columns = (
        "id",
        "org_id",
        "metric",
        "operator",
        "threshold",
        "hysteresis",
        "min_duration_min",
        "severity",
        "crop_id",
    )
    assert {row.code: tuple(getattr(row, c) for c in columns) for row in rows} == {
        rule["code"]: tuple(rule[c] for c in columns) for rule in FACTORY_RULES
    }


async def test_the_seeded_factory_rules_match_the_documented_conditions(
    db_session: AsyncSession,
) -> None:
    """Condition and severity per code as docs/06 §3 fixes them.

    `water_stress` and `waterlogging` have no `threshold` of their own: it is
    resolved per plot (θ_estrés, field capacity + 5), and the model's
    `flood_risk`/`drought_risk` have none either. The `hysteresis` of each rule is
    not asserted: the doc does not state it yet (D13, pending agronomist
    validation).
    """
    result = await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.org_id.is_(None)))
    rules = {row.code: row for row in result.scalars()}

    assert set(rules) == set(_DOCUMENTED_RULES)
    for code, condition in _DOCUMENTED_RULES.items():
        rule = rules[code]
        assert (rule.operator, rule.threshold, rule.min_duration_min, rule.severity) == condition, (
            code
        )


async def test_factory_rules_survive_a_truncate_and_a_re_seed() -> None:
    """`TRUNCATE organization CASCADE` wipes `alert_rule` (its `org_id` FK), which is
    why the teardown re-seeds the factory rules. This test runs that cleanup itself
    instead of relying on which tests ran before it."""
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE organization CASCADE"))
        await seed_factory_rules(conn)
        result = await conn.execute(select(AlertRuleRow))
        assert len(list(result.scalars())) == 9


def _rule_values(org_id: uuid.UUID | None, **overrides: Any) -> dict[str, Any]:
    """A valid `alert_rule`: every column the CHECKs and the not-nulls demand."""
    return {
        "id": uuid7(),
        "org_id": org_id,
        "code": "custom_temp",
        "metric": "air_temp",
        "operator": ">",
        "threshold": decimal.Decimal("40"),
        "hysteresis": decimal.Decimal("0"),
        "min_duration_min": 60,
        "severity": "warning",
        **overrides,
    }


async def _alert_values(db_session: AsyncSession) -> dict[str, Any]:
    """A valid `alert`, so the column under test is the only thing a CHECK can catch."""
    org_id, plot_id, _node_id = await _create_test_plot_and_node(db_session)
    rule_id = (
        (await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.code == "heat_stress")))
        .scalar_one()
        .id
    )
    return {
        "id": uuid7(),
        "org_id": org_id,
        "rule_id": rule_id,
        "plot_id": plot_id,
        "node_id": None,
        "state": "open",
        "severity": "warning",
        "opened_at": datetime.now(UTC),
    }


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [("state", "closed"), ("severity", "urgent"), ("outcome", "maybe")],
)
async def test_alert_rejects_a_value_outside_its_checked_domain(
    db_session: AsyncSession, column: str, bad_value: str
) -> None:
    """docs/03 `alert`: `ck_alert_state`, `ck_alert_severity`, `ck_alert_outcome`."""
    values = await _alert_values(db_session)
    values[column] = bad_value

    db_session.add(AlertRow(**values))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.parametrize(("column", "bad_value"), [("operator", "="), ("severity", "fatal")])
async def test_alert_rule_rejects_a_value_outside_its_checked_domain(
    db_session: AsyncSession, column: str, bad_value: str
) -> None:
    """docs/03 `alert_rule`: `ck_alert_rule_operator`, `ck_alert_rule_severity`."""
    org_id, _plot_id, _node_id = await _create_test_plot_and_node(db_session)

    db_session.add(AlertRuleRow(**_rule_values(org_id, **{column: bad_value})))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_a_second_factory_rule_with_an_existing_code_is_rejected(
    db_session: AsyncSession,
) -> None:
    """`uq_alert_rule_factory_code` is unique on `code` where `org_id IS NULL`: a
    second factory rule cannot reuse a code, but an org's own rule may."""
    db_session.add(AlertRuleRow(**_rule_values(None, code="heat_stress")))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    org_id, _plot_id, _node_id = await _create_test_plot_and_node(db_session)
    db_session.add(AlertRuleRow(**_rule_values(org_id, code="heat_stress")))
    await db_session.commit()


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
