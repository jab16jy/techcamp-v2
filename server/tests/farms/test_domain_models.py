import pytest

from techcamp.farms.domain.errors import RainfedPlotHasIrrigationError
from techcamp.farms.domain.models import (
    IrrigationSystem,
    default_efficiency_for,
    ensure_rainfed_has_no_irrigation,
)


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
