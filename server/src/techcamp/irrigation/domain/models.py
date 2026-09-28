"""FAO-56 water balance domain math and recommendation decision (docs/06 §5; ADR-0009/0022/0023).

Pure functions and dataclasses, stdlib only, floats (no I/O, no DB).
Follows FAO Irrigation and Drainage Paper 56 single-Kc methodology and
project-specific agronomic recommendation rules for Caribbean smallholders.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from techcamp.irrigation.domain.errors import InvalidCropStagesError


class RecommendationKind(StrEnum):
    """Irrigation recommendation decision outcome (docs/03-modelo-datos.md:210, 464)."""

    IRRIGATE = "irrigate"
    POSTPONE = "postpone"
    NOT_NEEDED = "not_needed"
    NO_KC = "no_kc"
    RAINFED = "rainfed"


class WaterBalanceStatus(StrEnum):
    """Plot water balance status for cards and monitoring (docs/04-api.md:66, 75)."""

    OK = "ok"
    WATCH = "watch"
    IRRIGATE = "irrigate"
    STRESS = "stress"


class RainfedAdvice(StrEnum):
    """Agronomic advice codes for rainfed plots in table order (docs/06-diseno-detallado.md §5)."""

    DELAY_SOWING = "delay_sowing"
    RAIN_EXPECTED = "rain_expected"
    CONSERVE_MOISTURE = "conserve_moisture"
    PRIORITIZE_HARVEST = "prioritize_harvest"
    NO_ACTION = "no_action"


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

WATCH_THRESHOLD_RATIO: float = 0.8
"""Ratio of RAW above which depletion triggers 'watch' status before stress/irrigation."""


class StageLike(Protocol):
    """Structural protocol for crop stages (matches farms.domain.models.CropStage)."""

    @property
    def stage(self) -> str: ...

    @property
    def length_days(self) -> int: ...

    @property
    def kc(self) -> float: ...


KcSourceCode = Literal["fao56", "local", "approximate", "none"]
"""Provenance of a crop's Kc values, as the persisted string (docs/03-modelo-datos.md:446).

Mirrors the member values of `farms.domain.models.KcSource`, declared literally
because this domain imports nothing from other modules. The application layer
translates the enum to one of these codes at the call boundary."""


@dataclass(frozen=True, slots=True)
class WaterBalanceDay:
    """Daily root zone water balance for a plot (docs/03:192-205)."""

    plot_id: UUID
    day: date
    etc_mm: float
    effective_rain_mm: float
    irrigation_mm: float
    taw_mm: float
    raw_mm: float
    depletion_model_mm: float
    depletion_mm: float
    soil_moisture_obs_pct: float | None
    assimilation_k: float
    stress_moisture_pct: float


@dataclass(frozen=True, slots=True)
class IrrigationRecommendation:
    """Irrigation recommendation for a plot on a given day (docs/03:206-215).

    The decision `decide_recommendation` produces carries no identity: it is a
    verdict about a plot's numbers, and the caller pairs it with the plot and the
    day when it stores it. `advice` is always advice codes; the repository parses
    stored JSON back into `RainfedAdvice` and drops codes it does not know.
    """

    kind: RecommendationKind
    depth_mm: float | None
    duration_min: int | None
    advice: tuple[RainfedAdvice, ...]
    rationale: dict[str, Any]
    id: UUID | None = None
    plot_id: UUID | None = None
    day: date | None = None


@dataclass(frozen=True, slots=True)
class StoredIrrigationRecommendation:
    """A stored `irrigation_recommendation` row as the read API serves it (docs/03:206-215).

    `plot_id` and `day` are the row's key columns and NOT NULL, so a stored
    recommendation always carries both and readers need no fallback. `id` is
    omitted on purpose: no read path exposes it (docs/04-api.md:110-114).
    """

    plot_id: UUID
    day: date
    kind: RecommendationKind
    depth_mm: float | None
    duration_min: int | None
    advice: tuple[RainfedAdvice, ...]
    rationale: dict[str, Any]


def stage_for_cycle_day(stages: Sequence[StageLike], day_of_cycle: int) -> str:
    """Identify growth stage name for a given 1-based cycle day.

    Stages must be ordered (initial, development, mid, late).
    Days past cycle length return the last stage name ('late').
    """
    if day_of_cycle < 1:
        raise ValueError("day_of_cycle must be >= 1")
    if not stages:
        raise InvalidCropStagesError("stages must not be empty")

    accum_days = 0
    for stage in stages:
        accum_days += stage.length_days
        if day_of_cycle <= accum_days:
            return stage.stage
    return stages[-1].stage


_KNOWN_STAGE_NAMES = frozenset({"initial", "development", "mid", "late"})
"""Canonical stage names this module's Kc lookup understands (mirrors
`farms.domain.models.CROP_STAGES`; `StageLike` is structural, so the name is
not imported)."""


def compute_kc_for_cycle_day(stages: Sequence[StageLike], day_of_cycle: int) -> float:
    """Compute crop coefficient Kc for a 1-based cycle day from ordered stages.

    FAO-56 Chapter 6 single-Kc methodology (Eq. 66; docs/06 §5 table):
    - Initial stage: Kc = Kc_ini
    - Development stage: linear interpolation from Kc_ini to Kc_mid
    - Mid-season stage: Kc = Kc_mid
    - Late-season stage: uses its own Kc (docs/06 §5 table; feature doc Decision)
    - Days past cycle: uses late-season Kc

    Raises:
        InvalidCropStagesError: `stages` is empty, or a stage name is not one of
            `initial`/`development`/`mid`/`late`. `stage_for_cycle_day` identifies
            stages positionally, so a misspelled or localized name would otherwise
            let the two disagree on the stage for the same day instead of failing
            loudly (R3-005). A `ValueError` subclass (R3-broad-valueerror-catch),
            distinct from the plain `ValueError` below for an invalid
            `day_of_cycle`, so a caller can catch stage-data problems narrowly.
    """
    if day_of_cycle < 1:
        raise ValueError("day_of_cycle must be >= 1")
    if not stages:
        raise InvalidCropStagesError("stages must not be empty")

    unknown = {s.stage for s in stages} - _KNOWN_STAGE_NAMES
    if unknown:
        raise InvalidCropStagesError(f"unknown crop stage name(s): {sorted(unknown)}")

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


def compute_observed_depletion(
    fc: float, theta_obs: float, root_depth_m: float, taw_mm: float
) -> float:
    """Observed depletion from sensor soil moisture (docs/06 §5 table).

    Formula: Dr_obs = clamp(1000 * (θFC - θobs) * Zr, 0, TAW). Clamped like
    `compute_model_depletion` (R3-001): a sensor reading above field capacity (heavy
    rain) or below the wilting point would otherwise yield a negative or
    above-TAW depletion that then drives status, the decision, and next day's
    `dr_prev`.
    """
    raw = 1000.0 * (fc - theta_obs) * root_depth_m
    return max(0.0, min(taw_mm, raw))


def assimilate_depletion(dr_model: float, dr_obs: float, k: float, taw_mm: float) -> float:
    """Weighted assimilation of sensor depletion into model depletion (ADR-0022; docs/06 §5).

    Formula: Dr = clamp(Dr_model + K * (Dr_obs - Dr_model), 0, TAW) (R3-001).
    """
    raw = dr_model + k * (dr_obs - dr_model)
    return max(0.0, min(taw_mm, raw))


def is_sensor_depth_representative(
    sensor_depths_cm: Sequence[float],
    root_depth_cm: float,
    tolerance_ratio: float = ZR_HALF_TOLERANCE_RATIO,
) -> bool:
    """Check if sensor installation depth represents the crop root zone (docs/06 §5).

    Both `sensor_depths_cm` and `root_depth_cm` are centimetres (R3-002): every
    other domain function takes Zr in metres (`root_depth_m`), so mixing units
    here would silently return False and drop K to 0.

    Rules:
    - Exactly one sensor: must be near Zr/2 within tolerance (tolerance_ratio * root_depth).
    - Exactly two sensors: both within root zone (0 < depth <= root_depth) at different depths.
    - Otherwise (0 or 3+ sensors): not representative.
    """
    if root_depth_cm <= 0:
        return False

    if len(sensor_depths_cm) == 1:
        d = sensor_depths_cm[0]
        target = root_depth_cm / 2.0
        tolerance = tolerance_ratio * root_depth_cm
        return 0.0 < d <= root_depth_cm and abs(d - target) <= tolerance

    if len(sensor_depths_cm) == 2:
        d1, d2 = sensor_depths_cm[0], sensor_depths_cm[1]
        return 0.0 < d1 <= root_depth_cm and 0.0 < d2 <= root_depth_cm and d1 != d2

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


def compute_water_balance_status(dr: float, raw: float, is_rainfed: bool) -> WaterBalanceStatus:
    """Compute 4-tier water balance status: ok | watch | irrigate | stress (docs/04:66, 75).

    Rules:
    - RAW <= 0 (degenerate soil, e.g. θFC == θWP, or TAW = 0 from incomplete soil
      data): 'ok'. There is no positive depletion threshold to compare Dr against,
      so this reads as no stress rather than an incoherent 'irrigate'/'stress'
      (D5, feature doc `techcamp-v2-e6-followups.md`; R3-003).
    - Dr < 0.8 * RAW: 'ok'
    - 0.8 * RAW <= Dr < RAW: 'watch'
    - Dr >= RAW, plot with an irrigation system: 'irrigate' (docs/06 §5 flowchart)
    - Dr > RAW, rainfed plot: 'stress', because water stress is strictly above RAW
      (ADR-0022; docs/04:75)
    - Dr == RAW, rainfed plot: 'watch'. The threshold is not crossed yet and a
      rainfed plot never reports 'irrigate' (docs/04:75).
    """
    if raw <= 0:
        return WaterBalanceStatus.OK
    if dr < raw:
        if dr < WATCH_THRESHOLD_RATIO * raw:
            return WaterBalanceStatus.OK
        return WaterBalanceStatus.WATCH
    if is_rainfed:
        return WaterBalanceStatus.WATCH if dr == raw else WaterBalanceStatus.STRESS
    return WaterBalanceStatus.IRRIGATE


def evaluate_rainfed_advice(
    has_active_cycle: bool,
    dr: float,
    raw: float,
    forecast_rain_7d_mm: float,
    forecast_et0_7d_mm: float,
    stage: str,
) -> tuple[RainfedAdvice, ...]:
    """Evaluate advice codes for rainfed plots in table order (docs/06 §5 table).

    Table order:
    1. delay_sowing: no active cycle and 7d rain < 7d ET0.
    2. rain_expected: active cycle, Dr >= RAW and 7d rain >= Dr.
    3. conserve_moisture: active cycle, Dr >= RAW and 7d rain < Dr.
    4. prioritize_harvest: conserve_moisture in stage 'late'.
    5. no_action: active cycle, Dr < RAW, or RAW <= 0 (degenerate soil: no positive
       threshold to be at or above, D5, R3-003).
    """
    if not has_active_cycle:
        if forecast_rain_7d_mm < forecast_et0_7d_mm:
            return (RainfedAdvice.DELAY_SOWING,)
        return ()

    advice: list[RainfedAdvice] = []
    if raw > 0 and dr >= raw:
        if forecast_rain_7d_mm >= dr:
            advice.append(RainfedAdvice.RAIN_EXPECTED)
        else:
            advice.append(RainfedAdvice.CONSERVE_MOISTURE)
            if stage == "late":
                advice.append(RainfedAdvice.PRIORITIZE_HARVEST)
    else:
        advice.append(RainfedAdvice.NO_ACTION)

    return tuple(advice)


def decide_recommendation(
    has_active_cycle: bool,
    is_rainfed: bool,
    kc_source: KcSourceCode,
    dr: float,
    raw: float,
    irrigation_efficiency: float | None,
    area_m2: float,
    system_flow_lph: float | None,
    forecast_rain_48h_mm: float,
    forecast_rain_7d_mm: float,
    forecast_et0_7d_mm: float,
    stage: str,
    rationale_context: dict[str, Any] | None = None,
) -> IrrigationRecommendation | None:
    """Execute the daily irrigation recommendation decision flowchart (docs/06 §5).

    Returns:
        IrrigationRecommendation with kind, depth_mm, duration_min, advice, and rationale,
        or None when a plot with an irrigation system has no active crop cycle.
    """
    ctx = rationale_context or {}

    k_val = ctx.get("k", K_ASSIMILATION_NONE)
    rationale = {
        "et0_mm": ctx.get("et0_mm"),
        "kc": ctx.get("kc"),
        "kc_source": kc_source,
        "kc_approximate": kc_source == "approximate",
        "p": ctx.get("p"),
        "raw_mm": raw if has_active_cycle else None,
        "taw_mm": ctx.get("taw_mm"),
        "depletion_model_mm": ctx.get("dr_model"),
        "depletion_mm": dr if has_active_cycle else None,
        "k": k_val,
        "without_sensor": k_val == K_ASSIMILATION_NONE,
        "forecast_rain_48h_mm": forecast_rain_48h_mm,
        "forecast_rain_7d_mm": forecast_rain_7d_mm,
        "forecast_et0_7d_mm": forecast_et0_7d_mm,
    }
    if "low_confidence" in ctx:
        rationale["low_confidence"] = ctx["low_confidence"]
    if "forecast_missing" in ctx:
        rationale["forecast_missing"] = ctx["forecast_missing"]
    if "missing_observed_weather" in ctx:
        rationale["missing_observed_weather"] = ctx["missing_observed_weather"]

    # Branch: no active cycle
    if not has_active_cycle:
        if not is_rainfed:
            return None
        advice = evaluate_rainfed_advice(
            has_active_cycle=False,
            dr=dr,
            raw=raw,
            forecast_rain_7d_mm=forecast_rain_7d_mm,
            forecast_et0_7d_mm=forecast_et0_7d_mm,
            stage=stage,
        )
        return IrrigationRecommendation(
            kind=RecommendationKind.RAINFED,
            depth_mm=None,
            duration_min=None,
            advice=advice,
            rationale=rationale,
        )

    # Branch: missing validated Kc
    if kc_source == "none":
        return IrrigationRecommendation(
            kind=RecommendationKind.NO_KC,
            depth_mm=None,
            duration_min=None,
            advice=(),
            rationale=rationale,
        )

    # Branch: rainfed plot
    if is_rainfed:
        advice = evaluate_rainfed_advice(
            has_active_cycle=True,
            dr=dr,
            raw=raw,
            forecast_rain_7d_mm=forecast_rain_7d_mm,
            forecast_et0_7d_mm=forecast_et0_7d_mm,
            stage=stage,
        )
        return IrrigationRecommendation(
            kind=RecommendationKind.RAINFED,
            depth_mm=None,
            duration_min=None,
            advice=advice,
            rationale=rationale,
        )

    # Branch: plot with an irrigation system
    if dr < raw or raw <= 0:
        # RAW <= 0 (degenerate soil): no positive threshold to be at or above, so
        # never postpone/irrigate an irrigated plot on that data (D5, R3-003).
        return IrrigationRecommendation(
            kind=RecommendationKind.NOT_NEEDED,
            depth_mm=None,
            duration_min=None,
            advice=(),
            rationale=rationale,
        )

    if forecast_rain_48h_mm >= dr:
        return IrrigationRecommendation(
            kind=RecommendationKind.POSTPONE,
            depth_mm=None,
            duration_min=None,
            advice=(),
            rationale=rationale,
        )

    efficiency = (
        irrigation_efficiency
        if irrigation_efficiency is not None and irrigation_efficiency > 0
        else 1.0
    )
    depth_mm = round(dr / efficiency, 2)

    duration_min: int | None = None
    if system_flow_lph and system_flow_lph > 0 and area_m2 > 0:
        # 1 mm on 1 m2 = 1 L
        duration_min = round(depth_mm * area_m2 / system_flow_lph * 60.0)

    return IrrigationRecommendation(
        kind=RecommendationKind.IRRIGATE,
        depth_mm=depth_mm,
        duration_min=duration_min,
        advice=(),
        rationale=rationale,
    )
