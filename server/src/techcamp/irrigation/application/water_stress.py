"""The evidence the `water_stress` rule is decided on (docs/06 §3 "Reglas de
fábrica" and §5, ADR-0022; D25, D26, D27, D29).

Two reads, both owned here because the docs answer both questions ONCE:

- `representative_soil_moisture_sensors` is the `K > 0` sensor ("sensor
  representativo": a `field` calibration, a representative depth and a valid
  reading in the window). The daily balance assimilates it and, per docs/06 §5,
  it is the only sensor that feeds the `water_stress` rule over readings, so both
  consumers call this one function instead of answering an agronomic question
  twice (D26).
- `balance_stress_evidence` is the plot's own θ_estrés (`stress_moisture_pct`) and
  the daily balances as the balance branch of the rule reads them (D27).

It lives in `irrigation` because the table and the agronomic rules are E6's; a
caller in another module (docs/05's `alerts --> irrigation`, D25) reads them
through this package and never through `irrigation.domain`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from techcamp.irrigation.application.ports import WaterBalanceRepository
from techcamp.irrigation.domain.models import is_sensor_depth_representative
from techcamp.shared.dates import BOGOTA_TZ
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)
from techcamp.telemetry.domain.models import CalibrationKind

BALANCE_WINDOW_DAYS = 7
"""How far back a `water_stress` decision looks for daily balances.

The 48 h critical horizon (docs/06 §3) plus the clear run that resolves an open
alert, with room for a day the 04:30 balance job did not run. A θ_estrés older
than a week belongs to a crop stage the plot has left (docs/06 §5 recalculates it
every day), so a stale one is not evidence of stress today.
"""

_MAX_NODES_PER_PLOT = 500
"""ponytail: one `list_for_org` page covers every node on a plot at this
project's scale (the same constant as `telemetry.application.query_readings` and
`alerts.application.evaluate_readings`)."""


@dataclass(frozen=True, slots=True)
class RepresentativeSensor:
    """One `K > 0` sensor of the plot and its mean over the window."""

    sensor_id: int
    depth_cm: float
    mean_moisture_pct: float


@dataclass(frozen=True, slots=True)
class BalanceStress:
    """What the balance rows of one plot say about its water stress.

    `stress_moisture_pct` is the θ_estrés of the newest balance day: the moisture
    at which `Dr = RAW` (docs/06 §5, ADR-0022), and the threshold the reading
    branch of `water_stress` compares the representative sensor against.

    `margin_samples` is the daily series the balance branch decides on: one
    `(instant, margin)` per balance day, where the margin is `(Dr / RAW) - 1`. It
    is relative on purpose: `p` moves with ETc every day, so `RAW` is a per-day
    quantity and a frozen threshold would compare a day's depletion against
    another day's threshold. A day with `RAW <= 0` (degenerate soil, no positive
    threshold to cross) contributes no sample at all (D27, E6's D5).
    """

    stress_moisture_pct: float
    margin_samples: tuple[tuple[datetime, float], ...]


async def representative_soil_moisture_sensors(
    *,
    org_id: UUID,
    plot_id: UUID,
    root_depth_cm: float | None,
    start: datetime,
    end: datetime,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
) -> list[RepresentativeSensor]:
    """The plot's `K > 0` soil-moisture sensors, with their mean over `[start, end)`.

    The `K > 0` conditions of docs/06 §5 / ADR-0022, in the order the docs give
    them: a `field` calibration in effect at the window's end, at least one valid
    reading in the window (out-of-range readings excluded, docs/06 §1), and a
    representative depth for the plot's root zone (one sensor near Zr/2, or two
    at different depths inside it).

    `end` is the anchor of both the calibration lookup and the window, so the
    daily balance passes the end of local day D−1 (its D2/D3 anchor, which makes
    a rerun deterministic) and the reading rule passes its own decision instant.
    The caller owns the window: a mean per day and a mean per evaluation are
    different numbers, and the rule that needs one must not be given the other.
    """
    if root_depth_cm is None or root_depth_cm <= 0:
        return []
    candidates: list[RepresentativeSensor] = []
    for node in await nodes.list_for_org(org_id, plot_id=plot_id, limit=_MAX_NODES_PER_PLOT):
        for sensor in await sensors.list_for_node(node.id, org_id):
            if sensor.metric != "soil_moisture" or sensor.depth_cm is None:
                continue
            calibration = await calibrations.get_latest_valid_at(sensor.id, org_id, at=end)
            if calibration is None or calibration.kind != CalibrationKind.FIELD:
                continue
            valid_points = await readings.query_valid_raw(sensor.id, org_id, start=start, end=end)
            values = [point.value for point in valid_points if not math.isnan(point.value)]
            if not values:
                continue
            candidates.append(
                RepresentativeSensor(
                    sensor_id=sensor.id,
                    depth_cm=float(sensor.depth_cm),
                    mean_moisture_pct=sum(values) / len(values),
                )
            )
    if len(candidates) not in (1, 2):
        # docs/06 §5: one sensor near Zr/2, or the average of two at different
        # depths. Three or more are not a rule the docs give.
        return []
    if not is_sensor_depth_representative([c.depth_cm for c in candidates], root_depth_cm):
        return []
    return candidates


async def balance_stress_evidence(
    balances: WaterBalanceRepository,
    *,
    org_id: UUID,
    plot_id: UUID,
    to_day: date,
) -> BalanceStress | None:
    """The plot's balance evidence for `water_stress`, or `None` without rows.

    `to_day` is the day the run for D decided, so the newest row is the one for
    D−1 (docs/06 §5: the run for day D writes the balance row for D−1; Q2). Each
    balance day is anchored at the instant its local day ENDED, E6's D2 anchor: a
    balance for D−1 describes the day that closed at local midnight of D, which
    keeps the daily series comparable with a run time after that midnight.

    `None` when the plot has no balance row in the window: the 04:30 job skips a
    plot with incomplete soil data (docs/06 §5), and a plot without a θ_estrés has
    no threshold to decide against.
    """
    rows = await balances.list_for_plot(
        plot_id, org_id, to_day - timedelta(days=BALANCE_WINDOW_DAYS), to_day
    )
    if not rows:
        return None
    samples = tuple(
        (_local_day_end(row.day), row.depletion_mm / row.raw_mm - 1.0)
        for row in rows
        if row.raw_mm > 0
    )
    return BalanceStress(stress_moisture_pct=rows[-1].stress_moisture_pct, margin_samples=samples)


def _local_day_end(day: date) -> datetime:
    """The instant a local day ended: local midnight of the day AFTER `day`."""
    return datetime(day.year, day.month, day.day, tzinfo=BOGOTA_TZ).astimezone(UTC) + timedelta(
        days=1
    )
