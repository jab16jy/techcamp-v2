"""Alerts application facade: the public surface other modules and routers use.

docs/05: a module only imports another module's public `application` package,
so the lifecycle and the rule use cases are exported here and the use cases
stay behind it.
"""

from techcamp.alerts.application.evaluate_node_health import evaluate_node_health
from techcamp.alerts.application.evaluate_readings import evaluate_landed_readings
from techcamp.alerts.application.evaluate_water_stress import evaluate_balance_rules
from techcamp.alerts.application.evaluate_weather_rules import evaluate_weather_rules
from techcamp.alerts.application.manage_rules import create_rule, list_rules, update_rule
from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository, AlertTarget
from techcamp.alerts.application.use_cases import (
    acknowledge,
    list_alerts,
    open_alert,
    resolve_automatically,
    resolve_manually,
    upgrade_to_critical,
)

__all__ = [
    "AlertRepository",
    "AlertRuleRepository",
    "AlertTarget",
    "acknowledge",
    "create_rule",
    "evaluate_balance_rules",
    "evaluate_landed_readings",
    "evaluate_node_health",
    "evaluate_weather_rules",
    "list_alerts",
    "list_rules",
    "open_alert",
    "resolve_automatically",
    "resolve_manually",
    "update_rule",
    "upgrade_to_critical",
]
