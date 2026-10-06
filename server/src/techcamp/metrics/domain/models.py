"""The enrollment survey as a domain value type (docs/03-modelo-datos.md:239-248,
426-430; ADR-0024).

Pure data, no I/O. Floats, not `Decimal`: the survey is a farmer's approximate
figure in kg/ha and COP/ha, and `Decimal` belongs at the ORM boundary only
(the same rule `irrigation` follows). A cost the farmer does not know is
`None`, never `0` — an unrecorded figure and a free plot are different facts
(docs/03's modeling rules, E11 lessons).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import UUID

from techcamp.identity.domain.models import Role
from techcamp.metrics.domain.errors import InsufficientRoleError


class IrrigationPractice(StrEnum):
    """How the plot was irrigated before TechCamp (docs/03-modelo-datos.md:246).

    The same closed vocabulary as `plot.irrigation_system`, so the survey
    compares like with like; `none` is a rainfed plot.
    """

    NONE = "none"
    DRIP = "drip"
    SPRINKLER = "sprinkler"
    GRAVITY = "gravity"


BASELINE_WRITE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.TECHNICIAN})
"""Roles that may save a survey: docs/04-api.md:233 gives the write to owner and
technician and read access to any member (D-T0.10)."""


def ensure_can_edit_baseline(role: Role) -> None:
    """Reject a survey write from a read-only role (producer, viewer)."""
    if role not in BASELINE_WRITE_ROLES:
        raise InsufficientRoleError(role)


@dataclass(frozen=True, slots=True)
class PlotBaseline:
    """One survey per plot: the crop and yield of its last cycle, the approximate
    cost per hectare and the irrigation practice (docs/03 §`plot_baseline`).

    The impact of every later cycle is measured against these figures, which is
    why it is not the ML `baseline` heuristic (docs/03:428).
    """

    plot_id: UUID
    org_id: UUID
    """Carried with the plot so every query filters by it
    (docs/09-cuellos-de-botella.md#seguridad)."""
    enrolled_on: date
    crop_id: int
    """Crop of the last cycle, from the global catalog (docs/03:243)."""
    last_yield_kg_ha: float
    """Required: the figure impact is measured against. `last_cost_cop_ha` is the
    only optional field (docs/04-api.md:52)."""
    last_cost_cop_ha: float | None
    irrigation_practice: IrrigationPractice
    recorded_by: UUID
    """Whoever saved the survey last; a `PUT` replaces the row, so this moves
    (D-T0.11)."""
