"""FAO-56 water balance domain math (docs/06 §5; ADR-0009/0022).

Pure functions and dataclasses, stdlib only, floats (no I/O, no DB).
Follows FAO Irrigation and Drainage Paper 56 single-Kc methodology and
weighted sensor assimilation for Caribbean smallholder plots.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

# Physical and agronomic constants
P_MIN: float = 0.1
"""Minimum depletion fraction p (FAO-56 Table 22 / Eq. 62 clamp)."""

P_MAX: float = 0.8
"""Maximum depletion fraction p (FAO-56 Table 22 / Eq. 62 clamp)."""

PE_THRESHOLD_MM: float = 5.0
"""Precipitation threshold below which rainfall is ineffective (docs/06 §5 table)."""

PE_FACTOR: float = 0.8
"""Effective precipitation fraction above threshold: Pe = 0.8 * P (docs/06 §5 table)."""

K_ASSIMILATION_DEFAULT: float = 0.5
"""Sensor assimilation weight K with field calibration and representative depth (ADR-0022)."""

K_ASSIMILATION_NONE: float = 0.0
"""Sensor assimilation weight K without representative sensor / lab calibration (ADR-0022)."""

ZR_HALF_TOLERANCE_RATIO: float = 0.15
# ponytail: tolerance fraction around Zr/2 pending agronomic validation (docs/06 §5)


class StageLike(Protocol):
    """Structural protocol for crop stages (matches farms.domain.models.CropStage)."""

    stage: str
    length_days: int
    kc: float


def stage_for_cycle_day(stages: Sequence[StageLike], day_of_cycle: int) -> str:
    """Identify growth stage name for a given 1-based cycle day.

    Stages must be ordered (initial, development, mid, late).
    Days past cycle length return the last stage name ('late').
    """
    if day_of_cycle < 1:
        raise ValueError("day_of_cycle must be >= 1")
    if not stages:
        raise ValueError("stages must not be empty")

    accum_days = 0
    for stage in stages:
        accum_days += stage.length_days
        if day_of_cycle <= accum_days:
            return stage.stage
    return stages[-1].stage


def compute_kc_for_cycle_day(stages: Sequence[StageLike], day_of_cycle: int) -> float:
    """Compute crop coefficient Kc for a 1-based cycle day from ordered stages.

    FAO-56 Chapter 6 single-Kc methodology (Eq. 66; docs/06 §5 table):
    - Initial stage: Kc = Kc_ini
    - Development stage: linear interpolation from Kc_ini to Kc_mid
    - Mid-season stage: Kc = Kc_mid
    - Late-season stage: uses its own Kc (docs/06 §5 table; feature doc Decision)
    - Days past cycle: uses late-season Kc
    """
    if day_of_cycle < 1:
        raise ValueError("day_of_cycle must be >= 1")
    if not stages:
        raise ValueError("stages must not be empty")

    stage_by_name = {s.stage: s for s in stages}
    ini_stage = stage_by_name.get("initial", stages[0])
    dev_stage = stage_by_name.get("development")
    mid_stage = stage_by_name.get("mid")
    late_stage = stage_by_name.get("late", stages[-1])

    ini_len = ini_stage.length_days
    ini_kc = ini_stage.kc

    if day_of_cycle <= ini_len:
        return ini_kc

    dev_len = dev_stage.length_days if dev_stage else 0
    dev_end = ini_len + dev_len
    mid_kc = mid_stage.kc if mid_stage else ini_kc

    if day_of_cycle <= dev_end:
        if dev_len <= 0:
            return mid_kc
        fraction = (day_of_cycle - ini_len) / dev_len
        return ini_kc + fraction * (mid_kc - ini_kc)

    mid_len = mid_stage.length_days if mid_stage else 0
    mid_end = dev_end + mid_len

    if day_of_cycle <= mid_end:
        return mid_kc

    return late_stage.kc


def compute_etc(kc: float, et0_mm: float) -> float:
    """Crop evapotranspiration: ETc = Kc * ET0 (FAO-56 Eq. 58)."""
    return kc * et0_mm


def compute_taw(fc: float, wp: float, root_depth_m: float) -> float:
    """Total Available Water: TAW = 1000 * (θFC - θWP) * Zr (FAO-56 Eq. 82).

    Args:
        fc: Soil moisture at field capacity (fraction 0-1).
        wp: Soil moisture at permanent wilting point (fraction 0-1).
        root_depth_m: Effective rooting depth in meters (Zr).
    """
    return 1000.0 * (fc - wp) * root_depth_m


def compute_adjusted_p(p_table: float, etc_mm: float) -> float:
    """Adjust depletion fraction p for ETc rate (FAO-56 Table 22 / Eq. 62).

    Formula: p = p_table + 0.04 * (5 - ETc), clamped to [0.1, 0.8].
    """
    raw_p = p_table + 0.04 * (5.0 - etc_mm)
    return max(P_MIN, min(P_MAX, raw_p))


def compute_raw(p: float, taw_mm: float) -> float:
    """Readily Available Water: RAW = p * TAW (FAO-56 Eq. 83)."""
    return p * taw_mm


def compute_stress_moisture(fc: float, wp: float, p: float) -> float:
    """Soil moisture threshold below which water stress begins (ADR-0022; docs/06 §3).

    Formula: θ_stress = θFC - p * (θFC - θWP).
    """
    return fc - p * (fc - wp)


def compute_effective_rain(rain_mm: float) -> float:
    """Effective precipitation: Pe = 0.8 * P if P > 5 mm else 0 (docs/06 §5 table)."""
    if rain_mm > PE_THRESHOLD_MM:
        return PE_FACTOR * rain_mm
    return 0.0


def compute_model_depletion(
    dr_prev: float,
    etc_mm: float,
    effective_rain_mm: float,
    irrigation_mm: float,
    taw_mm: float,
) -> float:
    """Daily root zone depletion model balance (FAO-56 Chapter 8, Eq. 85 / Example 38).

    Formula: Dr_model = clamp(Dr_prev - Pe - I + ETc, 0, TAW).
    """
    balance = dr_prev - effective_rain_mm - irrigation_mm + etc_mm
    return max(0.0, min(taw_mm, balance))


def compute_observed_depletion(fc: float, theta_obs: float, root_depth_m: float) -> float:
    """Observed depletion from sensor soil moisture (docs/06 §5 table).

    Formula: Dr_obs = 1000 * (θFC - θobs) * Zr.
    """
    return 1000.0 * (fc - theta_obs) * root_depth_m


def assimilate_depletion(dr_model: float, dr_obs: float, k: float) -> float:
    """Weighted assimilation of sensor depletion into model depletion (ADR-0022; docs/06 §5).

    Formula: Dr = Dr_model + K * (Dr_obs - Dr_model).
    """
    return dr_model + k * (dr_obs - dr_model)


def is_sensor_depth_representative(
    sensor_depths: Sequence[float],
    root_depth: float,
    tolerance_ratio: float = ZR_HALF_TOLERANCE_RATIO,
) -> bool:
    """Check if sensor installation depth represents the crop root zone (docs/06 §5).

    Rules:
    - Exactly one sensor: must be near Zr/2 within tolerance (tolerance_ratio * root_depth).
    - Exactly two sensors: both within root zone (0 < depth <= root_depth) at different depths.
    - Otherwise (0 or 3+ sensors): not representative.
    """
    if root_depth <= 0:
        return False

    if len(sensor_depths) == 1:
        d = sensor_depths[0]
        target = root_depth / 2.0
        tolerance = tolerance_ratio * root_depth
        return 0.0 < d <= root_depth and abs(d - target) <= tolerance

    if len(sensor_depths) == 2:
        d1, d2 = sensor_depths[0], sensor_depths[1]
        return 0.0 < d1 <= root_depth and 0.0 < d2 <= root_depth and d1 != d2

    return False


def determine_sensor_weight(
    has_valid_reading: bool,
    calibration_kind: str,
    is_representative: bool,
) -> float:
    """Determine sensor assimilation weight K (docs/06 §5 table; ADR-0022).

    Returns:
        0.5 if field calibration, valid reading in last 24h, and representative depth.
        0.0 otherwise.
    """
    if has_valid_reading and calibration_kind == "field" and is_representative:
        return K_ASSIMILATION_DEFAULT
    return K_ASSIMILATION_NONE
