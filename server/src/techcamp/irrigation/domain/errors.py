"""Irrigation domain errors. Pure, no I/O."""

from __future__ import annotations

from datetime import date
from uuid import UUID


class RecommendationNotFoundError(Exception):
    """Raised when an irrigation recommendation does not exist for a plot on a day."""

    def __init__(self, plot_id: UUID, day: date) -> None:
        self.plot_id = plot_id
        self.day = day
        super().__init__(f"Recommendation for plot {plot_id} on {day} not found")


class InvalidDateRangeError(Exception):
    """Raised when a date query range is invalid (`from > to` or exceeding 366 days)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
