"""Alerts application facade: the public surface other modules and routers use.

docs/05: a module only imports another module's public `application` package,
so the lifecycle and the rule use cases are exported here and the use cases
stay behind it.
"""

from techcamp.alerts.application.escalate import escalate_due_alerts
from techcamp.alerts.application.evaluate_node_health import evaluate_node_health
from techcamp.alerts.application.evaluate_readings import evaluate_landed_readings
from techcamp.alerts.application.evaluate_risk_rules import RiskRuleEvaluation, evaluate_risk_rules
from techcamp.alerts.application.evaluate_water_stress import evaluate_balance_rules
from techcamp.alerts.application.evaluate_weather_rules import evaluate_weather_rules
from techcamp.alerts.application.manage_rules import create_rule, list_rules, update_rule
from techcamp.alerts.application.ports import (
    AlertRepository,
    AlertRuleRepository,
    AlertTarget,
    EscalationTarget,
)
from techcamp.alerts.application.use_cases import (
    acknowledge,
    list_alerts,
    list_open_alerts_for_plots,
    open_alert,
    resolve_automatically,
    resolve_manually,
    upgrade_to_critical,
)
from techcamp.alerts.domain import PredictionEvidence

__all__ = [
    "AlertRepository",
    "AlertRuleRepository",
    "AlertTarget",
    "EscalationTarget",
    "PredictionEvidence",
    "RiskRuleEvaluation",
    "acknowledge",
    "create_rule",
    "escalate_due_alerts",
    "evaluate_balance_rules",
    "evaluate_landed_readings",
    "evaluate_node_health",
    "evaluate_risk_rules",
    "evaluate_weather_rules",
    "list_alerts",
    "list_open_alerts_for_plots",
    "list_rules",
    "open_alert",
    "resolve_automatically",
    "resolve_manually",
    "update_rule",
    "upgrade_to_critical",
]
