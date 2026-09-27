"""FAO-56 water balance domain math tests.

Pure calculations, citing FAO-56 chapters/equations/examples and project docs
(docs/06-diseno-detallado.md §5, docs/03-modelo-datos.md, ADR-0009, ADR-0022).
All expected values are hand-derived and documented in arithmetic comments.
"""

from __future__ import annotations

import pytest

from techcamp.farms.domain.models import CropStage, KcSource
from techcamp.irrigation.domain.models import (
    K_ASSIMILATION_DEFAULT,
    K_ASSIMILATION_NONE,
    P_MAX,
    P_MIN,
    PE_THRESHOLD_MM,
    WATCH_THRESHOLD_RATIO,
    ZR_HALF_TOLERANCE_RATIO,
    IrrigationRecommendation,
    RainfedAdvice,
    RecommendationKind,
    WaterBalanceStatus,
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
    compute_water_balance_status,
    decide_recommendation,
    determine_sensor_weight,
    evaluate_rainfed_advice,
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
    assert WATCH_THRESHOLD_RATIO == 0.8


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


# --- 11. Water Balance Status (docs/04:66,75) ---


def test_compute_water_balance_status_irrigated_and_rainfed() -> None:
    """docs/04:66,75: status is ok | watch | irrigate | stress.

    Rainfed never reports 'irrigate'; reports 'stress' when Dr >= RAW.
    Watch threshold is minimally defined at 0.8 * RAW.
    Let RAW = 50.0 mm.
    0.8 * RAW = 40.0 mm.

    Dr = 20.0 mm (< 40.0) -> 'ok' for both.
    Dr = 45.0 mm (40.0 <= Dr < 50.0) -> 'watch' for both.
    Dr = 55.0 mm (>= 50.0):
      irrigated -> 'irrigate'
      rainfed -> 'stress'
    """
    raw = 50.0
    # ok
    assert compute_water_balance_status(dr=20.0, raw=raw, is_rainfed=False) is WaterBalanceStatus.OK
    assert compute_water_balance_status(dr=20.0, raw=raw, is_rainfed=True) is WaterBalanceStatus.OK

    # watch
    assert (
        compute_water_balance_status(dr=45.0, raw=raw, is_rainfed=False) is WaterBalanceStatus.WATCH
    )
    assert (
        compute_water_balance_status(dr=45.0, raw=raw, is_rainfed=True) is WaterBalanceStatus.WATCH
    )

    # At or above RAW
    assert (
        compute_water_balance_status(dr=55.0, raw=raw, is_rainfed=False)
        is WaterBalanceStatus.IRRIGATE
    )
    assert (
        compute_water_balance_status(dr=55.0, raw=raw, is_rainfed=True) is WaterBalanceStatus.STRESS
    )


# --- 12. Rainfed Advice Evaluation in Table Order (docs/06 §5 table) ---


def test_evaluate_rainfed_advice_no_active_cycle() -> None:
    """docs/06 §5 table row 1: delay_sowing when no active cycle and 7d rain < 7d ET0."""
    # 7d rain (15 mm) < 7d ET0 (35 mm) -> delay_sowing
    advice = evaluate_rainfed_advice(
        has_active_cycle=False,
        dr=0.0,
        raw=50.0,
        forecast_rain_7d_mm=15.0,
        forecast_et0_7d_mm=35.0,
        stage="initial",
    )
    assert advice == (RainfedAdvice.DELAY_SOWING,)

    # 7d rain (40 mm) >= 7d ET0 (35 mm) -> no advice needed
    advice_sufficient_rain = evaluate_rainfed_advice(
        has_active_cycle=False,
        dr=0.0,
        raw=50.0,
        forecast_rain_7d_mm=40.0,
        forecast_et0_7d_mm=35.0,
        stage="initial",
    )
    assert advice_sufficient_rain == ()


def test_evaluate_rainfed_advice_active_cycle_branches() -> None:
    """docs/06 §5 table: evaluated in order, all that apply:

    - rain_expected: Dr >= RAW and 7d rain >= Dr
    - conserve_moisture: Dr >= RAW and 7d rain < Dr
    - prioritize_harvest: conserve_moisture in stage late
    - no_action: Dr < RAW
    """
    raw = 50.0

    # No action: Dr (30 mm) < RAW (50 mm)
    assert evaluate_rainfed_advice(
        has_active_cycle=True,
        dr=30.0,
        raw=raw,
        forecast_rain_7d_mm=10.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
    ) == (RainfedAdvice.NO_ACTION,)

    # Rain expected: Dr (55 mm) >= RAW (50 mm) and 7d rain (60 mm) >= Dr (55 mm)
    assert evaluate_rainfed_advice(
        has_active_cycle=True,
        dr=55.0,
        raw=raw,
        forecast_rain_7d_mm=60.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
    ) == (RainfedAdvice.RAIN_EXPECTED,)

    # Conserve moisture (mid stage): Dr (55 mm) >= RAW (50 mm) and 7d rain (20 mm) < Dr (55 mm)
    assert evaluate_rainfed_advice(
        has_active_cycle=True,
        dr=55.0,
        raw=raw,
        forecast_rain_7d_mm=20.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
    ) == (RainfedAdvice.CONSERVE_MOISTURE,)

    # Prioritize harvest (late stage): conserve_moisture in stage late -> both codes in table order
    assert evaluate_rainfed_advice(
        has_active_cycle=True,
        dr=55.0,
        raw=raw,
        forecast_rain_7d_mm=20.0,
        forecast_et0_7d_mm=30.0,
        stage="late",
    ) == (RainfedAdvice.CONSERVE_MOISTURE, RainfedAdvice.PRIORITIZE_HARVEST)


# --- 13. Recommendation Decision Flowchart (docs/06 §5 flowchart) ---


def test_decide_recommendation_no_active_cycle() -> None:
    """Flowchart:

    - plot with irrigation system and no active cycle -> None
    - rainfed plot and no active cycle with rain < ET0 -> rainfed with delay_sowing
    """
    # Irrigated plot without active cycle:
    assert (
        decide_recommendation(
            has_active_cycle=False,
            is_rainfed=False,
            kc_source=KcSource.FAO56.value,
            dr=0.0,
            raw=50.0,
            irrigation_efficiency=0.9,
            area_m2=10000.0,
            system_flow_lph=5000.0,
            forecast_rain_48h_mm=0.0,
            forecast_rain_7d_mm=10.0,
            forecast_et0_7d_mm=30.0,
            stage="initial",
            rationale_context={},
        )
        is None
    )

    # Rainfed plot without active cycle:
    rec = decide_recommendation(
        has_active_cycle=False,
        is_rainfed=True,
        kc_source=KcSource.FAO56.value,
        dr=0.0,
        raw=50.0,
        irrigation_efficiency=None,
        area_m2=10000.0,
        system_flow_lph=None,
        forecast_rain_48h_mm=0.0,
        forecast_rain_7d_mm=10.0,
        forecast_et0_7d_mm=30.0,
        stage="initial",
        rationale_context={},
    )
    assert rec is not None
    assert isinstance(rec, IrrigationRecommendation)
    assert rec.kind is RecommendationKind.RAINFED
    assert rec.depth_mm is None
    assert rec.duration_min is None
    assert rec.advice == (RainfedAdvice.DELAY_SOWING,)


def test_decide_recommendation_no_kc() -> None:
    """Flowchart: kc_source == none -> no_kc recommendation."""
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.NONE.value,
        dr=30.0,
        raw=50.0,
        irrigation_efficiency=0.9,
        area_m2=10000.0,
        system_flow_lph=5000.0,
        forecast_rain_48h_mm=0.0,
        forecast_rain_7d_mm=10.0,
        forecast_et0_7d_mm=30.0,
        stage="initial",
        rationale_context={},
    )
    assert rec is not None
    assert rec.kind is RecommendationKind.NO_KC
    assert rec.depth_mm is None
    assert rec.duration_min is None
    assert rec.advice == ()


def test_decide_recommendation_irrigated_not_needed() -> None:
    """Flowchart: Dr < RAW -> not_needed."""
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.FAO56.value,
        dr=30.0,
        raw=50.0,
        irrigation_efficiency=0.9,
        area_m2=10000.0,
        system_flow_lph=5000.0,
        forecast_rain_48h_mm=0.0,
        forecast_rain_7d_mm=10.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
        rationale_context={},
    )
    assert rec is not None
    assert rec.kind is RecommendationKind.NOT_NEEDED
    assert rec.depth_mm is None
    assert rec.duration_min is None


def test_decide_recommendation_irrigated_postpone() -> None:
    """Flowchart: Dr >= RAW and 48h rain >= Dr -> postpone."""
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.FAO56.value,
        dr=55.0,
        raw=50.0,
        irrigation_efficiency=0.9,
        area_m2=10000.0,
        system_flow_lph=5000.0,
        forecast_rain_48h_mm=60.0,  # 60 >= 55
        forecast_rain_7d_mm=70.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
        rationale_context={},
    )
    assert rec is not None
    assert rec.kind is RecommendationKind.POSTPONE
    assert rec.depth_mm is None
    assert rec.duration_min is None


def test_decide_recommendation_irrigated_irrigate_depth_and_minutes() -> None:
    """Flowchart: Dr >= RAW and 48h rain < Dr -> irrigate.

    Arithmetic:
      Dr = 54.0 mm, RAW = 50.0 mm
      efficiency = 0.90 (drip)
      depth_mm = Dr / efficiency = 54.0 / 0.90 = 60.0 mm
      area_m2 = 5,000 m2
      system_flow_lph = 10,000 L/h
      minutes = depth * area_m2 / flow_lph * 60
              = 60.0 * 5,000 / 10,000 * 60
              = 300,000 / 10,000 * 60 = 30 * 60 = 1800 min
    """
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.FAO56.value,
        dr=54.0,
        raw=50.0,
        irrigation_efficiency=0.90,
        area_m2=5000.0,
        system_flow_lph=10000.0,
        forecast_rain_48h_mm=10.0,
        forecast_rain_7d_mm=20.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
        rationale_context={},
    )
    assert rec is not None
    assert rec.kind is RecommendationKind.IRRIGATE
    assert rec.depth_mm == pytest.approx(60.0)
    assert rec.duration_min == 1800


def test_decide_recommendation_irrigated_duration_rounding() -> None:
    """Verify sensible rounding for fractional minutes and depth:

    depth rounded to 2 decimal places, duration rounded to nearest integer minute.
    Arithmetic:
      Dr = 45.0 mm, efficiency = 0.75 (sprinkler) -> depth = 60.0 mm
      area_m2 = 1,234 m2, flow_lph = 4,000 L/h
      raw minutes = 60.0 * 1,234 / 4,000 * 60 = 74,040 / 4,000 * 60 = 18.51 * 60 = 1110.6 min
      rounded to integer -> 1111 min
    """
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.LOCAL.value,
        dr=45.0,
        raw=40.0,
        irrigation_efficiency=0.75,
        area_m2=1234.0,
        system_flow_lph=4000.0,
        forecast_rain_48h_mm=5.0,
        forecast_rain_7d_mm=15.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
        rationale_context={},
    )
    assert rec is not None
    assert rec.kind is RecommendationKind.IRRIGATE
    assert rec.depth_mm == pytest.approx(60.0)
    assert rec.duration_min == 1111


def test_decide_recommendation_rationale_contents() -> None:
    """docs/06 §5 / brief: rationale contains:

    ET0, Kc, kc_source, flag approximate, p, RAW, TAW, Dr model, Dr assimilated,
    K, forecast sums, without_sensor flag.
    """
    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source=KcSource.APPROXIMATE.value,
        dr=54.0,
        raw=50.0,
        irrigation_efficiency=0.90,
        area_m2=5000.0,
        system_flow_lph=10000.0,
        forecast_rain_48h_mm=10.0,
        forecast_rain_7d_mm=20.0,
        forecast_et0_7d_mm=30.0,
        stage="mid",
        rationale_context={
            "et0_mm": 5.0,
            "kc": 1.20,
            "p": 0.55,
            "taw_mm": 100.0,
            "dr_model": 50.0,
            "k": 0.0,
        },
    )
    assert rec is not None
    rationale = rec.rationale
    assert rationale["et0_mm"] == 5.0
    assert rationale["kc"] == 1.20
    assert rationale["kc_source"] == "approximate"
    assert rationale["kc_approximate"] is True
    assert rationale["p"] == 0.55
    assert rationale["raw_mm"] == 50.0
    assert rationale["taw_mm"] == 100.0
    assert rationale["depletion_model_mm"] == 50.0
    assert rationale["depletion_mm"] == 54.0
    assert rationale["k"] == 0.0
    assert rationale["without_sensor"] is True
    assert rationale["forecast_rain_48h_mm"] == 10.0
    assert rationale["forecast_rain_7d_mm"] == 20.0
    assert rationale["forecast_et0_7d_mm"] == 30.0
