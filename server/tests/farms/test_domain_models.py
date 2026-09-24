from uuid import UUID

import pytest

from techcamp.farms.domain.errors import InsufficientRoleError, RainfedPlotHasIrrigationError
from techcamp.farms.domain.models import (
    IrrigationSystem,
    SoilGridsSample,
    SoilProfileSource,
    apply_fao56_texture_fallback,
    build_soil_profile_from_soilgrids,
    classify_usda_texture,
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
        # FAO-56 (Allen et al., 1998) Table 19, midpoint of each θFC/θWP range
        # (fao.org/4/x0490e/x0490e0c.htm), as a percentage.
        ("sand", 12.0, 4.5),
        ("loamy_sand", 15.0, 6.5),
        ("sandy_loam", 23.0, 11.0),
        ("loam", 25.0, 12.0),
        ("silt_loam", 29.0, 15.0),
        ("silt", 32.0, 17.0),
        ("silty_clay_loam", 33.5, 20.5),
        ("silty_clay", 36.0, 23.0),
        ("clay", 36.0, 22.0),
    ],
)
def test_texture_fallback_uses_fao56_table_19_range_midpoints(
    texture: str, expected_fc: float, expected_wp: float
) -> None:
    fc, wp, source = apply_fao56_texture_fallback(texture, None, None)

    assert fc == pytest.approx(expected_fc)
    assert wp == pytest.approx(expected_wp)
    assert source is SoilProfileSource.FAO56_TEXTURE


def test_texture_fallback_is_case_insensitive() -> None:
    fc, wp, source = apply_fao56_texture_fallback("SILT", None, None)

    assert (fc, wp, source) == (32.0, 17.0, SoilProfileSource.FAO56_TEXTURE)


def test_unrecognized_texture_leaves_water_limits_unset() -> None:
    """Never invent values: a texture outside the FAO-56 Table 19 classes
    leaves θFC/θWP and `source` unset rather than guessing."""
    fc, wp, source = apply_fao56_texture_fallback("peat", None, None)

    assert (fc, wp, source) == (None, None, None)


def test_missing_texture_and_values_leaves_water_limits_unset() -> None:
    fc, wp, source = apply_fao56_texture_fallback(None, None, None)

    assert (fc, wp, source) == (None, None, None)


@pytest.mark.parametrize(
    ("sand", "silt", "clay", "expected"),
    [
        (100.0, 0.0, 0.0, "sand"),
        (0.0, 0.0, 100.0, "clay"),
        (0.0, 100.0, 0.0, "silt"),
        (40.0, 40.0, 20.0, "loam"),  # textbook triangle center point
    ],
)
def test_classify_usda_texture_matches_uncontroversial_reference_points(
    sand: float, silt: float, clay: float, expected: str
) -> None:
    assert classify_usda_texture(sand, silt, clay) == expected


def test_classify_usda_texture_needs_all_three_fractions() -> None:
    assert classify_usda_texture(None, 40.0, 20.0) is None
    assert classify_usda_texture(40.0, None, 20.0) is None
    assert classify_usda_texture(40.0, 40.0, None) is None


def test_build_soil_profile_from_soilgrids_uses_soilgrids_water_limits_when_present() -> None:
    plot_id = UUID("00000000-0000-7000-8000-000000000001")
    sample = SoilGridsSample(
        ph=6.5,
        organic_carbon_pct=1.5,
        sand_pct=40.0,
        silt_pct=40.0,
        clay_pct=20.0,
        field_capacity_pct=25.0,
        wilting_point_pct=12.0,
    )

    profile = build_soil_profile_from_soilgrids(plot_id, sample)

    assert profile.plot_id == plot_id
    assert profile.source is SoilProfileSource.SOILGRIDS
    assert profile.ph == 6.5
    assert profile.organic_matter_pct == pytest.approx(1.5 * 1.724)
    assert profile.texture == "loam"
    assert profile.field_capacity_pct == 25.0
    assert profile.wilting_point_pct == 12.0
    assert profile.root_depth_cm is None


def test_build_soil_profile_from_soilgrids_falls_back_to_fao56_texture_means() -> None:
    plot_id = UUID("00000000-0000-7000-8000-000000000002")
    sample = SoilGridsSample(
        ph=None,
        organic_carbon_pct=None,
        sand_pct=40.0,
        silt_pct=40.0,
        clay_pct=20.0,
        field_capacity_pct=None,
        wilting_point_pct=None,
    )

    profile = build_soil_profile_from_soilgrids(plot_id, sample)

    assert profile.source is SoilProfileSource.FAO56_TEXTURE
    assert profile.field_capacity_pct == 25.0  # loam mean, Table 19
    assert profile.wilting_point_pct == 12.0
    assert profile.organic_matter_pct is None


def test_build_soil_profile_from_soilgrids_needs_both_water_limits_together() -> None:
    """A lone `wv0033` or `wv1500` (SoilGrids has no prediction at the other
    depth/property) isn't enough: fall back rather than storing a
    half-SoilGrids, half-invented pair."""
    plot_id = UUID("00000000-0000-7000-8000-000000000003")
    sample = SoilGridsSample(
        ph=None,
        organic_carbon_pct=None,
        sand_pct=40.0,
        silt_pct=40.0,
        clay_pct=20.0,
        field_capacity_pct=25.0,
        wilting_point_pct=None,
    )

    profile = build_soil_profile_from_soilgrids(plot_id, sample)

    assert profile.source is SoilProfileSource.FAO56_TEXTURE
    assert profile.field_capacity_pct == 25.0
    assert profile.wilting_point_pct == 12.0
