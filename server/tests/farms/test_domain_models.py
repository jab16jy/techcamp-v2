import pytest

from techcamp.farms.domain.errors import InsufficientRoleError, RainfedPlotHasIrrigationError
from techcamp.farms.domain.models import (
    IrrigationSystem,
    SoilProfileSource,
    apply_fao56_texture_fallback,
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


def test_lab_values_pass_through_unchanged_with_source_lab() -> None:
    fc, wp, source = apply_fao56_texture_fallback("loamy_sand", 20.0, 8.0)

    assert (fc, wp, source) == (20.0, 8.0, SoilProfileSource.LAB)


@pytest.mark.parametrize(
    ("texture", "expected_fc", "expected_wp"),
    [
        # FAO-56 (Allen et al., 1998) Table 19, Chapter 8 Example 36 ("From
        # Table 19"): the three classes verified against the primary source.
        ("loamy_sand", 15.0, 6.0),
        ("silt", 32.0, 15.0),
        ("silty_clay", 35.0, 23.0),
        ("Silty Clay".replace(" ", "_"), 35.0, 23.0),
    ],
)
def test_texture_fallback_matches_fao56_table_19_verified_classes(
    texture: str, expected_fc: float, expected_wp: float
) -> None:
    fc, wp, source = apply_fao56_texture_fallback(texture, None, None)

    assert (fc, wp, source) == (expected_fc, expected_wp, SoilProfileSource.FAO56_TEXTURE)


def test_texture_fallback_is_case_insensitive() -> None:
    fc, wp, source = apply_fao56_texture_fallback("SILT", None, None)

    assert (fc, wp, source) == (32.0, 15.0, SoilProfileSource.FAO56_TEXTURE)


def test_unrecognized_texture_leaves_water_limits_unset() -> None:
    """Never invent values: an unverified or unknown texture class (e.g. the
    FAO-56 Table 19 classes not yet verified against the primary source)
    leaves θFC/θWP and `source` unset rather than guessing."""
    fc, wp, source = apply_fao56_texture_fallback("sandy_loam", None, None)

    assert (fc, wp, source) == (None, None, None)


def test_missing_texture_and_values_leaves_water_limits_unset() -> None:
    fc, wp, source = apply_fao56_texture_fallback(None, None, None)

    assert (fc, wp, source) == (None, None, None)
