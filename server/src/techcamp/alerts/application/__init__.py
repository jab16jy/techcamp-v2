"""Alerts application facade: the public surface other modules and routers use.

docs/05: a module only imports another module's public `application` package,
so the lifecycle is exported here and the use cases stay behind it.
"""

from techcamp.alerts.application.ports import AlertRepository, AlertTarget
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
    "AlertTarget",
    "acknowledge",
    "list_alerts",
    "open_alert",
    "resolve_automatically",
    "resolve_manually",
    "upgrade_to_critical",
]
