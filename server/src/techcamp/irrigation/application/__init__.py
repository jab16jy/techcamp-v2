"""Irrigation application facade: public queries and use cases (docs/05; D-T0.1)."""

from techcamp.irrigation.application.query_irrigation import (
    PlotWaterBalanceDay,
    crop_stage_for_day,
    query_plot_recommendation,
    query_plot_water_balance,
)
from techcamp.irrigation.application.water_stress import representative_soil_moisture_sensors
from techcamp.irrigation.domain.errors import RecommendationNotFoundError
from techcamp.irrigation.domain.models import StoredIrrigationRecommendation

__all__ = [
    "PlotWaterBalanceDay",
    "RecommendationNotFoundError",
    "StoredIrrigationRecommendation",
    "crop_stage_for_day",
    "query_plot_recommendation",
    "query_plot_water_balance",
    "representative_soil_moisture_sensors",
]
