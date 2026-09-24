"""Domain-level farm/plot failures. Pure, no I/O."""

from __future__ import annotations


class RainfedPlotHasIrrigationError(Exception):
    """Raised when a rainfed (`irrigation_system = none`) plot carries efficiency or flow.

    ADR-0023: `none` is a rainfed plot, with no efficiency or flow. The database
    `CHECK` constraint mirrors this rule as a second line of defense.
    """

    def __init__(self, efficiency: float | None, flow_lph: float | None) -> None:
        self.efficiency = efficiency
        self.flow_lph = flow_lph
        super().__init__("A rainfed plot (irrigation_system='none') cannot have efficiency or flow")
