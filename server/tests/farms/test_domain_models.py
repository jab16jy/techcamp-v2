import pytest

from techcamp.farms.domain.errors import InsufficientRoleError, RainfedPlotHasIrrigationError
from techcamp.farms.domain.models import (
    IrrigationSystem,
    default_efficiency_for,
    ensure_can_write,
    ensure_rainfed_has_no_irrigation,
)
from techcamp.identity.domain.models import Role


def test_irrigation_system_matches_the_documented_values() -> None:
    assert {s.value for s in IrrigationSystem} == {"none", "drip", "sprinkler", "gravity"}


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        (IrrigationSystem.DRIP, 0.90),
        (IrrigationSystem.SPRINKLER, 0.75),
        (IrrigationSystem.GRAVITY, 0.60),
        (IrrigationSystem.NONE, None),
    ],
)
def test_default_efficiency_matches_docs_03(
    system: IrrigationSystem, expected: float | None
) -> None:
    assert default_efficiency_for(system) == expected


def test_rainfed_plot_accepts_no_irrigation() -> None:
    ensure_rainfed_has_no_irrigation(IrrigationSystem.NONE, None, None)


def test_rainfed_plot_rejects_efficiency() -> None:
    with pytest.raises(RainfedPlotHasIrrigationError):
        ensure_rainfed_has_no_irrigation(IrrigationSystem.NONE, 0.9, None)


def test_rainfed_plot_rejects_flow() -> None:
    with pytest.raises(RainfedPlotHasIrrigationError):
        ensure_rainfed_has_no_irrigation(IrrigationSystem.NONE, None, 500.0)


def test_irrigated_plot_accepts_efficiency_and_flow() -> None:
    ensure_rainfed_has_no_irrigation(IrrigationSystem.DRIP, 0.9, 500.0)


@pytest.mark.parametrize("role", [Role.OWNER, Role.TECHNICIAN])
def test_owner_and_technician_can_write(role: Role) -> None:
    ensure_can_write(role)


@pytest.mark.parametrize("role", [Role.PRODUCER, Role.VIEWER])
def test_producer_and_viewer_cannot_write(role: Role) -> None:
    with pytest.raises(InsufficientRoleError):
        ensure_can_write(role)
