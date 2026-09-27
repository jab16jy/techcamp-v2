"""Factory alert rules seed data and helpers (docs/06-diseno-detallado.md §3)."""

from __future__ import annotations

import decimal
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from techcamp.alerts.adapters.orm import AlertRuleRow

# Deterministic namespace for factory alert rules
FACTORY_RULE_NAMESPACE = uuid.UUID("7b9c6f2a-8d1e-4c3b-9a5f-0e2d4c6b8a10")

FACTORY_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "water_stress"),
        "org_id": None,
        "code": "water_stress",
        "metric": "soil_moisture",
        "operator": "<",
        "threshold": None,
        "hysteresis": decimal.Decimal("3"),
        "min_duration_min": 360,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "waterlogging"),
        "org_id": None,
        "code": "waterlogging",
        "metric": "soil_moisture",
        "operator": ">",
        "threshold": None,
        "hysteresis": decimal.Decimal("3"),
        "min_duration_min": 1440,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "heat_stress"),
        "org_id": None,
        "code": "heat_stress",
        "metric": "air_temp",
        "operator": ">",
        "threshold": decimal.Decimal("35"),
        "hysteresis": decimal.Decimal("1"),
        "min_duration_min": 180,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "fungal_risk"),
        "org_id": None,
        "code": "fungal_risk",
        "metric": "air_rh",
        "operator": ">",
        "threshold": decimal.Decimal("85"),
        "hysteresis": decimal.Decimal("5"),
        "min_duration_min": 600,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "heavy_rain_forecast"),
        "org_id": None,
        "code": "heavy_rain_forecast",
        "metric": "rain",
        "operator": ">",
        "threshold": decimal.Decimal("50"),
        "hysteresis": decimal.Decimal("0"),
        "min_duration_min": 0,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "flood_risk"),
        "org_id": None,
        "code": "flood_risk",
        "metric": None,
        "operator": None,
        "threshold": None,
        "hysteresis": decimal.Decimal("0"),
        "min_duration_min": 0,
        "severity": "critical",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "drought_risk"),
        "org_id": None,
        "code": "drought_risk",
        "metric": None,
        "operator": None,
        "threshold": None,
        "hysteresis": decimal.Decimal("0"),
        "min_duration_min": 0,
        "severity": "critical",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "node_offline"),
        "org_id": None,
        "code": "node_offline",
        "metric": None,
        "operator": None,
        "threshold": None,
        "hysteresis": decimal.Decimal("0"),
        "min_duration_min": 0,
        "severity": "warning",
        "crop_id": None,
    },
    {
        "id": uuid.uuid5(FACTORY_RULE_NAMESPACE, "node_battery_low"),
        "org_id": None,
        "code": "node_battery_low",
        "metric": "battery_v",
        "operator": "<",
        "threshold": decimal.Decimal("3.4"),
        "hysteresis": decimal.Decimal("0.1"),
        "min_duration_min": 0,
        "severity": "info",
        "crop_id": None,
    },
)


async def seed_factory_rules(conn: AsyncConnection) -> None:
    """Insert factory rules, ignoring conflicts if already present.

    The rows are the same ones the `d4e6f8a0b2c1` revision inserted as its own
    frozen copy; the test teardown needs this one because a `TRUNCATE
    organization ... CASCADE` wipes the whole table.
    """
    stmt = insert(AlertRuleRow).values([dict(r) for r in FACTORY_RULES])
    stmt = stmt.on_conflict_do_nothing(index_elements=[AlertRuleRow.id])
    await conn.execute(stmt)
