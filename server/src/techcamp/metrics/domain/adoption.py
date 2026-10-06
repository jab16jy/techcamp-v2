"""The digital adoption index's four components and their weighted mean
(docs/11-metricas.md §2, D-T0.3 to D-T0.6; ADR-0024).

Pure math: no I/O, no clock, no database. The month, the plot's irrigation
system and every piece of evidence arrive as arguments, so the same function
answers the monthly job, a re-run and a test without any of them touching the
outside world (docs/05-arquitectura.md §Reglas).

`Decimal` throughout and no rounding anywhere: a component is a ratio of counts
and the index a ratio of ratios, and `Numeric` has no scale to lose digits to.
Quantizing would invent a precision rule no document states.

**A component with no evidence is `None`, never `0` and never `1`**
(docs/11:57, D-T0.3). `0` says "the producer did not do it", which is a
measurement; `None` says "nothing was asked of them". The index then spreads
100 points evenly over the components that do have a denominator, so a null
never costs the producer points they could not earn.

The domain takes its own evidence value types rather than the read-model rows of
`application/ports.py`: `domain` imports nothing (docs/05 §Reglas), so the use
case maps one to the other. The two carry the same fields on purpose — the views
already aggregated, this only divides.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

_ONE = Decimal(1)
_HUNDRED = Decimal(100)
_QUARTER = Decimal("0.25")
"""`±25 %` of the recommended depth (docs/11:59)."""

IRRIGATE_RECOMMENDATION_KINDS = frozenset({"irrigate"})
"""The kinds that ask for an applied depth (docs/11:59, D-T0.5)."""

NON_IRRIGATING_RECOMMENDATION_KINDS = frozenset({"postpone", "not_needed"})
"""The kinds followed by *not* irrigating (docs/11:59, D-T0.5).

`no_kc` and `rainfed` are deliberately absent: they carry no recommendation, so
they never enter the denominator (docs/11:59).
"""


@dataclass(frozen=True, slots=True)
class NodeEvidence:
    """One node's monitoring evidence for a month (D-T0.4).

    `claimed_seconds` is how long the node was claimed inside the month, and
    `received_readings` counts every reading whatever its `quality`: the
    component measures that the plot is being measured, and an out-of-range
    value is already covered by the node alerts (docs/11:58).
    """

    interval_s: int
    claimed_seconds: int
    received_readings: int


@dataclass(frozen=True, slots=True)
class RecommendationDay:
    """One day that carried a recommendation and what was applied on it
    (D-T0.5).

    `irrigation_mm` is `None`, not `0`, when no irrigation was recorded:
    "did not irrigate" and "applied no millimetres" are different facts, and a
    `postpone` day is followed exactly when the former holds.
    """

    kind: str
    depth_mm: Decimal | None
    """Null for every kind but `irrigate` (docs/03-modelo-datos.md:211)."""
    irrigation_mm: Decimal | None


@dataclass(frozen=True, slots=True)
class AdoptionComponents:
    """The four components of one plot-month, each `None` when it has no
    denominator (D-T0.3).

    `record_keeping` is never `None` in practice: "semanas del mes" is calendar
    math and a month always has some, so a silent month scores `0` rather than
    dropping out of the average (docs/11:57).
    """

    monitoring: Decimal | None
    record_keeping: Decimal | None
    decision: Decimal | None
    risk_management: Decimal | None


@dataclass(frozen=True, slots=True)
class PlotMonthlyMetric:
    """The adoption index of one plot for one calendar month, ready to store
    (docs/03-modelo-datos.md:438, D-T0.2).

    `month` is the first day of the month in America/Bogota, which is what makes
    `(plot_id, month)` the upsert key (D-T0.2).
    """

    plot_id: UUID
    org_id: UUID
    month: date
    components: AdoptionComponents
    digital_adoption_index: Decimal | None
    computed_at: datetime


def month_bounds(month: date) -> tuple[date, date]:
    """The first and last day of `month`'s calendar month, in local days.

    `month` is already the first day; the last day comes from the calendar, so a
    caller never builds "last day" as `first + 30 days` and misses February's
    length (D-T0.7).
    """
    last_day = calendar.monthrange(month.year, month.month)[1]
    return month, date(month.year, month.month, last_day)


def weeks_in_month(month: date) -> int:
    """How many ISO weeks **overlap** the month (Monday start).

    The convention is T3's, not this module's invention: `logbook_weeks`
    documents weeks overlapping the month, where a week straddling the first or
    the last appears in both months with only that month's entries counted, so
    the ratio can never exceed 1 (docs/11 §2). The same convention on both sides
    of the division is what keeps `record_keeping` bounded.

    It is plain calendar math, not evidence, which is why it lives here and not
    in a view: the numerator needs a database and this does not.
    """
    _, last_day = month_bounds(month)
    first_monday = month - timedelta(days=month.weekday())
    last_monday = last_day - timedelta(days=last_day.weekday())
    return (last_monday - first_monday).days // 7 + 1


def monitoring_component(nodes: list[NodeEvidence] | tuple[NodeEvidence, ...]) -> Decimal | None:
    """Readings received over readings expected in the month, capped at 1
    (D-T0.4).

    Expected readings per node are the seconds it was claimed inside the month
    divided by its `interval_s`, so a node claimed mid-month is not charged for
    time it never had. Both totals are summed across nodes **before** dividing:
    one loud node cannot offset a silent one by averaging ratios.

    A node whose `interval_s` is zero or negative defines no expected count at
    all, so it is dropped from **both** sides of the ratio rather than divided
    by (#251). Dropping its readings too is the point: keeping a numerator
    without its denominator would let a misconfigured node inflate the
    component, and an inverted denominator would hand back a negative one that
    the table's CHECK rejects outright. What is left is missing evidence, and
    missing evidence is `None` (docs/11:57, D-T0.3) — never `0`, which would
    read as a plot that failed to report.

    `None` when nothing was expected — no claimed node at all, no node with a
    usable interval, or a node whose claimed seconds inside the month are zero.
    """
    sound = [node for node in nodes if node.interval_s > 0]
    expected = sum(
        (Decimal(node.claimed_seconds) / Decimal(node.interval_s) for node in sound),
        start=Decimal(0),
    )
    if expected == 0:
        return None
    received = sum((Decimal(node.received_readings) for node in sound), start=Decimal(0))
    return min(_ONE, received / expected)


def record_keeping_component(weeks_with_entries: int, month: date) -> Decimal | None:
    """Weeks of the month holding at least one logbook entry, over the weeks of
    the month (docs/11:48, D-T0.3).

    `None` is unreachable for a real month — `weeks_in_month` is at least 4 — and
    the signature keeps it only so the function cannot be handed a month that is
    not the first of one. A month with no entries scores `0`: the denominator
    exists, so there is evidence of not recording (docs/11:57).
    """
    total_weeks = weeks_in_month(month)
    if total_weeks == 0:
        return None
    return min(_ONE, Decimal(weeks_with_entries) / Decimal(total_weeks))


def _irrigate_day_followed(day: RecommendationDay) -> bool:
    """Whether an `irrigate` day applied its recommended depth (D-T0.5).

    Within ±25 % of `depth_mm`, inclusive at both edges: 7.5 mm against a
    recommendation of 10 mm is on the boundary and counts as followed. A day with
    no `depth_mm` or no recorded irrigation is not followed.
    """
    if day.depth_mm is None or day.irrigation_mm is None:
        return False
    return abs(day.irrigation_mm - day.depth_mm) <= day.depth_mm * _QUARTER


def _non_irrigating_day_followed(day: RecommendationDay) -> bool:
    """Whether a `postpone` or `not_needed` day left the plot unwatered (D-T0.5).

    Any recorded millimetres break it, however few.
    """
    return day.irrigation_mm is None


def decision_component(
    days: list[RecommendationDay] | tuple[RecommendationDay, ...],
    *,
    rainfed: bool,
) -> Decimal | None:
    """Days whose recommendation was followed, over the days that carried one
    (D-T0.5).

    Only `irrigate`, `postpone` and `not_needed` enter the denominator;
    `no_kc` and `rainfed` carry no recommendation and are skipped rather than
    counted as not followed (docs/11:59).

    `None` in a rainfed plot: there is no applied depth to follow, so the
    component does not apply (docs/11:52). Also `None` when no day carried a
    recommendation that counts.
    """
    if rainfed:
        return None
    counting = [day for day in days if _day_counts(day.kind)]
    if not counting:
        return None
    followed = 0
    for day in counting:
        if day.kind in IRRIGATE_RECOMMENDATION_KINDS:
            followed += _irrigate_day_followed(day)
        else:
            followed += _non_irrigating_day_followed(day)
    return Decimal(followed) / Decimal(len(counting))


def _day_counts(kind: str) -> bool:
    """Whether a recommendation kind enters the `decision` denominator."""
    return kind in IRRIGATE_RECOMMENDATION_KINDS or kind in NON_IRRIGATING_RECOMMENDATION_KINDS


def risk_management_component(alerts_opened: int, timely_actions: int) -> Decimal | None:
    """Plot alerts acted on in time, over the plot's alerts of the month
    (D-T0.6).

    `None` when no plot alert was opened: there was nothing to act on, which is
    missing evidence rather than a plot that ignored its alerts (docs/11:57).
    Node alerts never reach the numerator or the denominator — they go to the
    technician (docs/11 §2).

    Whether an action was *timely* is the read model's answer, not this
    function's: the logbook records dates rather than hours, so the 48-hour
    window is compared on local days (D-T0.6).
    """
    if alerts_opened == 0:
        return None
    return min(_ONE, Decimal(timely_actions) / Decimal(alerts_opened))


def digital_adoption_index(components: AdoptionComponents) -> Decimal | None:
    """100 points split evenly over the components that have a denominator
    (D-T0.3).

    `None` when all four are `None` — a plot with no evidence at all has no
    index, and storing `0` would read as "adopted nothing" (docs/03:438). The
    rainfed case (three components, 100/3 each) is the same rule with one null
    (docs/11:52).
    """
    present = [
        value
        for value in (
            components.monitoring,
            components.record_keeping,
            components.decision,
            components.risk_management,
        )
        if value is not None
    ]
    if not present:
        return None
    return _HUNDRED * sum(present, start=Decimal(0)) / Decimal(len(present))
