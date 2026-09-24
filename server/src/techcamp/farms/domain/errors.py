"""Domain-level farm/plot failures. Pure, no I/O."""

from __future__ import annotations

from uuid import UUID

from techcamp.identity.domain.models import Role


class RainfedPlotHasIrrigationError(Exception):
    """Raised when a rainfed (`irrigation_system = none`) plot carries efficiency or flow.

    ADR-0023: `none` is a rainfed plot, with no efficiency or flow. The database
    `CHECK` constraint mirrors this rule as a second line of defense.
    """

    def __init__(self, efficiency: float | None, flow_lph: float | None) -> None:
        self.efficiency = efficiency
        self.flow_lph = flow_lph
        super().__init__("A rainfed plot (irrigation_system='none') cannot have efficiency or flow")


class FarmNotFoundError(Exception):
    """Raised when a farm doesn't exist or isn't in one of the caller's orgs.

    Callers map this to 404 (docs/04-api.md: cross-org access must not reveal
    that a resource exists).
    """

    def __init__(self, farm_id: UUID) -> None:
        self.farm_id = farm_id
        super().__init__(f"Farm {farm_id} not found")


class PlotNotFoundError(Exception):
    """Raised when a plot doesn't exist or isn't in one of the caller's orgs."""

    def __init__(self, plot_id: UUID) -> None:
        self.plot_id = plot_id
        super().__init__(f"Plot {plot_id} not found")


class InsufficientRoleError(Exception):
    """Raised when a member's role may not write farms or plots (T2 decision,
    odd/tasks/techcamp-v2-e3-farms.md: docs are silent, so owner and
    technician write, producer and viewer read only).
    """

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot write farms or plots")
