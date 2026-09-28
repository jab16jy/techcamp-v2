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


class InvalidCropStagesError(ValueError):
    """Raised by `stage_for_cycle_day`/`compute_kc_for_cycle_day` when `stages` is
    empty or contains a name outside initial/development/mid/late (R3-005;
    R3-broad-valueerror-catch).

    A `ValueError` subclass, so a caller checking for stage-data problems
    specifically (`run_daily_balance`, to skip just that plot) can catch this
    type alone instead of every `ValueError` the same functions can raise for
    an unrelated reason (`day_of_cycle < 1`), and a bare `except ValueError`
    (or `pytest.raises(ValueError, ...)`) still catches it unchanged.
    """
