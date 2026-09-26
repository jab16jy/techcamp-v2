"""FAO-56 water balance domain math tests.

Pure calculations, citing FAO-56 chapters/equations/examples and project docs
(docs/06-diseno-detallado.md §5, docs/03-modelo-datos.md, ADR-0009, ADR-0022).
All expected values are hand-derived and documented in arithmetic comments.
"""

from __future__ import annotations

import pytest

from techcamp.farms.domain.models import CropStage
from techcamp.irrigation.domain.models import (
    K_ASSIMILATION_DEFAULT,
    K_ASSIMILATION_NONE,
    P_MAX,
    P_MIN,
    PE_THRESHOLD_MM,
    ZR_HALF_TOLERANCE_RATIO,
    assimilate_depletion,
    compute_adjusted_p,
    compute_effective_rain,
    compute_etc,
    compute_kc_for_cycle_day,
    compute_model_depletion,
    compute_observed_depletion,
    compute_raw,
    compute_stress_moisture,
    compute_taw,
    determine_sensor_weight,
    is_sensor_depth_representative,
    stage_for_cycle_day,
)


def test_irrigation_constants_match_documented_values() -> None:
    """Verify constants match FAO-56 and project design rules."""
    assert P_MIN == 0.1
    assert P_MAX == 0.8
    assert PE_THRESHOLD_MM == 5.0
    assert K_ASSIMILATION_DEFAULT == 0.5
    assert K_ASSIMILATION_NONE == 0.0
    assert ZR_HALF_TOLERANCE_RATIO == 0.15


# Reference maize crop stages from FAO-56 Table 11 / docs/06 §5:
# initial: 18 days, Kc = 0.30
# development: 27 days (days 19..45), interpolating to mid Kc = 1.20
# mid: 31 days (days 46..76), Kc = 1.20
# late: 14 days (days 77..90), Kc = 0.35
# Total cycle length = 18 + 27 + 31 + 14 = 90 days.
_MAIZE_STAGES = (
    CropStage(stage="initial", length_days=18, kc=0.30, depletion_fraction_p=0.55),
    CropStage(stage="development", length_days=27, kc=0.75, depletion_fraction_p=0.55),
    CropStage(stage="mid", length_days=31, kc=1.20, depletion_fraction_p=0.55),
    CropStage(stage="late", length_days=14, kc=0.35, depletion_fraction_p=0.55),
)


# --- 1. FAO-56 Single-Kc and stage identification (FAO-56 Eq. 66, Example 28) ---


def test_kc_initial_stage_returns_initial_kc() -> None:
    """FAO-56 Chapter 6, Eq. 66: during initial stage, Kc = Kc_ini.

    For maize: length = 18 days, Kc_ini = 0.30.
    Day 1 is the first day of the cycle.
    Day 18 is the last day of the initial stage.
    """
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=1) == pytest.approx(0.30)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=18) == pytest.approx(0.30)
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=1) == "initial"
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=18) == "initial"


def test_kc_development_stage_linear_interpolation() -> None:
    """FAO-56 Chapter 6, Eq. 66 / Example 28: linear interpolation in development stage.

    Arithmetic:
      L_ini = 18 days, Kc_ini = 0.30
      L_dev = 27 days, Kc_mid = 1.20
      Development covers cycle days 19 through 45.
      Formula: Kc_i = Kc_ini + ((i - L_ini) / L_dev) * (Kc_mid - Kc_ini)

      Day 19 (first dev day):
        (19 - 18) / 27 = 1 / 27 ≈ 0.037037
        Kc = 0.30 + (1 / 27) * (1.20 - 0.30) = 0.30 + 0.90 / 27 = 0.30 + 0.033333 = 0.333333
      Day 32 (midpoint of dev):
        (32 - 18) / 27 = 14 / 27
        Kc = 0.30 + (14 / 27) * 0.90 = 0.30 + 0.466667 = 0.766667
      Day 45 (last dev day):
        (45 - 18) / 27 = 27 / 27 = 1.0
        Kc = 0.30 + 1.0 * (1.20 - 0.30) = 1.20 = Kc_mid
    """
    expected_day_19 = 0.30 + (1.0 / 27.0) * (1.20 - 0.30)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=19) == pytest.approx(
        expected_day_19
    )
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=19) == "development"

    expected_day_32 = 0.30 + (14.0 / 27.0) * (1.20 - 0.30)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=32) == pytest.approx(
        expected_day_32
    )

    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=45) == pytest.approx(1.20)
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=45) == "development"


def test_kc_mid_stage_returns_mid_kc() -> None:
    """FAO-56 Chapter 6, Eq. 66: during mid-season stage, Kc = Kc_mid.

    Mid stage covers cycle days 46 through 76 (31 days).
    Kc_mid = 1.20.
    """
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=46) == pytest.approx(1.20)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=60) == pytest.approx(1.20)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=76) == pytest.approx(1.20)
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=60) == "mid"


def test_kc_late_stage_and_days_past_cycle() -> None:
    """Project decision (docs/06 §5 table; odd/tasks/techcamp-v2-e6-irrigation.md:79):

    `late` uses its own stage Kc; days past the cycle length use the `late` Kc.
    Late stage covers cycle days 77 through 90 (14 days), Kc_late = 0.35.
    Days past cycle (e.g. 91, 100) use Kc_late = 0.35.
    """
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=77) == pytest.approx(0.35)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=90) == pytest.approx(0.35)
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=85) == "late"

    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=91) == pytest.approx(0.35)
    assert compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=105) == pytest.approx(0.35)
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle=95) == "late"


def test_kc_rejects_non_positive_day_of_cycle() -> None:
    with pytest.raises(ValueError, match="day_of_cycle must be >= 1"):
        compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle=0)


# --- 2. Crop Evapotranspiration ETc (FAO-56 Eq. 58) ---


def test_compute_etc_matches_fao56_eq_58() -> None:
    """FAO-56 Chapter 6, Eq. 58: ETc = Kc * ET0.

    Arithmetic:
      Kc = 1.15, ET0 = 5.2 mm/day
      ETc = 1.15 * 5.2 = 5.98 mm/day
    """
    assert compute_etc(kc=1.15, et0_mm=5.2) == pytest.approx(5.98)


# --- 3. Total Available Water TAW (FAO-56 Eq. 82) ---


def test_compute_taw_matches_fao56_eq_82() -> None:
    """FAO-56 Chapter 8, Eq. 82: TAW = 1000 * (θFC - θWP) * Zr.

    Arithmetic:
      θFC = 0.23 (fraction, 23%)
      θWP = 0.09 (fraction, 9%)
      Zr = 1.0 m (root depth in meters)
      TAW = 1000 * (0.23 - 0.09) * 1.0 = 1000 * 0.14 * 1.0 = 140.0 mm
    """
    assert compute_taw(fc=0.23, wp=0.09, root_depth_m=1.0) == pytest.approx(140.0)


# --- 4. Depletion Fraction p adjusted for ETc (FAO-56 Table 22 / Eq. 62) ---


@pytest.mark.parametrize(
    ("p_table", "etc", "expected_p"),
    [
        (0.55, 5.0, 0.55),
        (0.55, 2.5, 0.65),
        (0.55, 8.0, 0.43),
        (0.75, 1.0, 0.80),
        (0.20, 10.0, 0.10),
    ],
)
def test_compute_adjusted_p_matches_fao56_eq_62_and_clamps(
    p_table: float, etc: float, expected_p: float
) -> None:
    """FAO-56 Table 22, Eq. 62: p = p_table + 0.04 * (5 - ETc), clamped 0.1 - 0.8."""
    assert compute_adjusted_p(p_table=p_table, etc_mm=etc) == pytest.approx(expected_p)


# --- 5. Readily Available Water RAW (FAO-56 Eq. 83) ---


def test_compute_raw_matches_fao56_eq_83() -> None:
    """FAO-56 Chapter 8, Eq. 83: RAW = p * TAW.

    Arithmetic:
      p = 0.55, TAW = 140.0 mm
      RAW = 0.55 * 140.0 = 77.0 mm
    """
    assert compute_raw(p=0.55, taw_mm=140.0) == pytest.approx(77.0)


# --- 6. Stress moisture threshold θ_stress (docs/06 §3; ADR-0022) ---


def test_compute_stress_moisture_matches_scenario_a_benchmark() -> None:
    """docs/06 §3 / ADR-0022 scenario A benchmark:

    Maize in sandy loam with θFC = 0.23, θWP = 0.09, p = 0.55.
    Arithmetic:
      θ_stress = θFC - p * (θFC - θWP)
               = 0.23 - 0.55 * (0.23 - 0.09)
               = 0.23 - 0.55 * 0.14
               = 0.23 - 0.077 = 0.153 (15.3%)
    """
    assert compute_stress_moisture(fc=0.23, wp=0.09, p=0.55) == pytest.approx(0.153)


# --- 7. Effective Precipitation Pe (docs/06 §5 table) ---


@pytest.mark.parametrize(
    ("rain_mm", "expected_pe"),
    [
        (0.0, 0.0),
        (3.5, 0.0),
        (5.0, 0.0),
        (10.0, 8.0),
        (25.0, 20.0),
    ],
)
def test_compute_effective_rain_applies_5mm_threshold_and_80_percent_factor(
    rain_mm: float, expected_pe: float
) -> None:
    """docs/06 §5 table: Pe = 0.8 * P if P > 5 mm else 0."""
    assert compute_effective_rain(rain_mm) == pytest.approx(expected_pe)


# --- 8. Model Root Zone Depletion Dr_model (FAO-56 Chapter 8, Eq. 85 / Example 38) ---


def test_compute_model_depletion_daily_balance_and_clamps() -> None:
    """FAO-56 Chapter 8, Eq. 85 / Example 38:

    Dr_model = clamp(Dr_prev - Pe - I + ETc, 0, TAW)
    """
    assert compute_model_depletion(
        dr_prev=10.0, etc_mm=5.0, effective_rain_mm=0.0, irrigation_mm=0.0, taw_mm=100.0
    ) == pytest.approx(15.0)

    assert compute_model_depletion(
        dr_prev=10.0, etc_mm=2.0, effective_rain_mm=20.0, irrigation_mm=0.0, taw_mm=100.0
    ) == pytest.approx(0.0)

    assert compute_model_depletion(
        dr_prev=98.0, etc_mm=6.0, effective_rain_mm=0.0, irrigation_mm=0.0, taw_mm=100.0
    ) == pytest.approx(100.0)


# --- 9. Observed Depletion Dr_obs and Assimilation (docs/06 §5; ADR-0022) ---


def test_compute_observed_depletion_matches_formula() -> None:
    """docs/06 §5 table: Dr_obs = 1000 * (θFC - θobs) * Zr.

    Arithmetic:
      θFC = 0.25 (25%), θobs = 0.18 (18%), Zr = 0.8 m
      Dr_obs = 1000 * (0.25 - 0.18) * 0.8 = 1000 * 0.07 * 0.8 = 56.0 mm
    """
    assert compute_observed_depletion(fc=0.25, theta_obs=0.18, root_depth_m=0.8) == pytest.approx(
        56.0
    )


def test_assimilate_depletion_weighted_correction() -> None:
    """ADR-0022 / docs/06 §5: Dr = Dr_model + K * (Dr_obs - Dr_model)."""
    assert assimilate_depletion(dr_model=30.0, dr_obs=50.0, k=K_ASSIMILATION_NONE) == pytest.approx(
        30.0
    )
    assert assimilate_depletion(
        dr_model=30.0, dr_obs=50.0, k=K_ASSIMILATION_DEFAULT
    ) == pytest.approx(40.0)


# --- 10. Sensor weight K and representative depth rules (docs/06 §5) ---


def test_sensor_depth_representative_single_sensor_near_half_root_depth() -> None:
    """docs/06 §5: one sensor near Zr/2 is representative.

    Root depth Zr = 100 cm (1.0 m), Zr/2 = 50 cm.
    Tolerance ratio = 0.15 (tolerance = 15 cm, acceptable range 35 cm - 65 cm).
    Sensor at 45 cm: |45 - 50| = 5 cm <= 15 cm -> True.
    Sensor at 10 cm: |10 - 50| = 40 cm > 15 cm -> False
    (docs/06 §5: 10 cm does not represent maize root).
    """
    assert is_sensor_depth_representative([45.0], root_depth=100.0) is True
    assert is_sensor_depth_representative([50.0], root_depth=100.0) is True
    assert is_sensor_depth_representative([10.0], root_depth=100.0) is False


def test_sensor_depth_representative_two_sensors_in_root_zone() -> None:
    """docs/06 §5: two sensors at different depths inside root zone are representative."""
    assert is_sensor_depth_representative([30.0, 70.0], root_depth=100.0) is True
    assert is_sensor_depth_representative([30.0, 30.0], root_depth=100.0) is False
    assert is_sensor_depth_representative([30.0, 120.0], root_depth=100.0) is False
    assert is_sensor_depth_representative([], root_depth=100.0) is False


def test_determine_sensor_weight_rules() -> None:
    """docs/06 §5:

    K = 0.5 only with field calibration, valid reading in 24h, and representative depth.
    K = 0 without valid reading, lab calibration, or non-representative depth.
    """
    # Qualified:
    assert determine_sensor_weight(
        has_valid_reading=True, calibration_kind="field", is_representative=True
    ) == pytest.approx(0.5)

    # Disqualified by lab calibration:
    assert determine_sensor_weight(
        has_valid_reading=True, calibration_kind="lab", is_representative=True
    ) == pytest.approx(0.0)

    # Disqualified by missing reading in 24h:
    assert determine_sensor_weight(
        has_valid_reading=False, calibration_kind="field", is_representative=True
    ) == pytest.approx(0.0)

    # Disqualified by non-representative depth:
    assert determine_sensor_weight(
        has_valid_reading=True, calibration_kind="field", is_representative=False
    ) == pytest.approx(0.0)
