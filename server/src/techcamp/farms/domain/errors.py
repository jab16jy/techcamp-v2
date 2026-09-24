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


class MissingIrrigationEfficiencyError(Exception):
    """Raised when a `PATCH` explicitly nulls `irrigation_efficiency` while the
    plot stays or becomes irrigated (GitHub issue #21 round 4,
    odd/tasks/techcamp-v2-e3-farms.md T3b): an irrigated plot always needs an
    efficiency, so an explicit `null` is invalid input, not "use the
    default" — the default applies only when the field is omitted.
    """

    def __init__(self, irrigation_system: str) -> None:
        self.irrigation_system = irrigation_system
        super().__init__(f"An irrigated plot (system={irrigation_system}) requires an efficiency")


class SoilGridsUnavailableError(Exception):
    """Raised when the SoilGrids adapter can't produce a sample: either the
    request never reached ISRIC (`upstream_status=None`: timeout, DNS,
    connection refused) or ISRIC answered with a non-200 status. Callers
    (T5, `POST /plots/{plot_id}/soil:autofill`) map the two cases to `503`
    and `502` respectively, never a raw `500` (docs/04-api.md conventions).
    """

    def __init__(self, *, upstream_status: int | None, detail: str) -> None:
        self.upstream_status = upstream_status
        self.detail = detail
        super().__init__(detail)


class InvalidTechnicianError(Exception):
    """Raised when `technician_id` isn't a member of the farm's org with a
    write role (T2b decision, odd/tasks/techcamp-v2-e3-farms.md: docs/03 is
    silent on which role, so owner or technician — same as `WRITE_ROLES`).
    """

    def __init__(self, technician_id: UUID) -> None:
        self.technician_id = technician_id
        super().__init__(f"{technician_id} is not an owner or technician of this organization")
