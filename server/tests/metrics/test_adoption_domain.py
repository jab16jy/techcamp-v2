"""The adoption index's four components and their weighted index (E11 T5).

Pure math, no I/O and no database (docs/11 §2, D-T0.3 to D-T0.6). Every function
here takes the evidence T3's `MetricsSourceRepository` returns — the same frozen
dataclasses — so a change to a view shows up here as a failing expectation rather
than as a silently different number in the stored row.

The load-bearing cases are the ones a plausible rewrite gets wrong: a component
with no evidence must be `None` and not `0`, because `0` would claim the producer
did nothing where the truth is that nothing was asked of them (docs/11:57,
D-T0.3).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from techcamp.metrics.domain.adoption import (
    AdoptionComponents,
    NodeEvidence,
    RecommendationDay,
    decision_component,
    digital_adoption_index,
    monitoring_component,
    record_keeping_component,
    risk_management_component,
    weeks_in_month,
)

MONTH = date(2026, 9, 1)
"""September 2026, the same closed month `tests/metrics/conftest.py` freezes."""

PLOT_ID = uuid4()


def _node(*, interval_s: int = 300, claimed_seconds: int, received: int) -> NodeEvidence:
    return NodeEvidence(
        interval_s=interval_s, claimed_seconds=claimed_seconds, received_readings=received
    )


def _decision(
    day: date,
    kind: str,
    *,
    depth_mm: Decimal | None = None,
    irrigation_mm: Decimal | None = None,
) -> RecommendationDay:
    return RecommendationDay(kind=kind, depth_mm=depth_mm, irrigation_mm=irrigation_mm)


# --- monitoring (D-T0.4) -------------------------------------------------


def test_monitoring_is_received_over_expected_and_caps_at_one() -> None:
    # 30 days claimed at 300 s = 8640 expected; 4320 received = half.
    node = _node(interval_s=300, claimed_seconds=30 * 24 * 3600, received=4320)

    assert monitoring_component([node]) == Decimal("0.5")


def test_monitoring_sums_expected_and_received_across_nodes_before_dividing() -> None:
    # Two nodes, each claimed for half the month: 4320 expected each. Received
    # 4320 + 0. The mean of the two ratios would also be 0.5, so the case that
    # separates sum-then-divide from divide-then-average is one silent node
    # against a node that received twice its expected count.
    loud = _node(interval_s=300, claimed_seconds=4320 * 300, received=8640)
    silent = _node(interval_s=300, claimed_seconds=4320 * 300, received=0)

    # 8640 received over 8640 expected. Averaging the two ratios instead would
    # give (1 + 0) / 2 = 0.5, which reads the silent node as half a success.
    assert monitoring_component([silent, loud]) == Decimal(1)


def test_monitoring_caps_above_one_instead_of_exceeding_the_range() -> None:
    node = _node(interval_s=300, claimed_seconds=4320 * 300, received=9999)

    assert monitoring_component([node]) == Decimal(1)


def test_monitoring_is_none_for_a_node_with_a_zero_interval() -> None:
    # `interval_s <= 0` cannot define expected readings, so the node carries no
    # evidence: a division by zero is not a component of 0 % (#251).
    node = _node(interval_s=0, claimed_seconds=4320 * 300, received=4320)

    assert monitoring_component([node]) is None


def test_monitoring_is_none_for_a_node_with_a_negative_interval() -> None:
    # A negative interval inverts the denominator and would hand back a negative
    # component; the CHECK forbids it and the plot is not to blame for it (#251).
    node = _node(interval_s=-300, claimed_seconds=4320 * 300, received=4320)

    assert monitoring_component([node]) is None


def test_monitoring_scores_the_sound_nodes_and_ignores_an_invalid_one() -> None:
    # One sound node at half, plus a node whose interval is broken and whose
    # readings are numerous. Absent evidence is absent on both sides of the
    # ratio: counting its readings without its denominator would inflate the
    # numerator and cap the component at 1 instead of reporting the half the
    # sound node actually delivered.
    sound = _node(interval_s=300, claimed_seconds=4320 * 300, received=2160)
    broken = _node(interval_s=0, claimed_seconds=4320 * 300, received=4320)

    assert monitoring_component([sound, broken]) == Decimal("0.5")


def test_monitoring_is_none_when_every_node_has_an_invalid_interval() -> None:
    assert (
        monitoring_component(
            [
                _node(interval_s=0, claimed_seconds=4320 * 300, received=100),
                _node(interval_s=-300, claimed_seconds=4320 * 300, received=100),
            ]
        )
        is None
    )


def test_monitoring_is_none_without_a_claimed_node() -> None:
    assert monitoring_component([]) is None


def test_monitoring_is_none_when_a_claimed_node_owes_no_reading() -> None:
    # Claimed for zero seconds inside the month: the denominator is zero, so
    # this is missing evidence rather than 0 % (docs/11:57).
    node = _node(interval_s=300, claimed_seconds=0, received=0)

    assert monitoring_component([node]) is None


def test_monitoring_counts_every_reading_whatever_its_quality() -> None:
    # T3's view counts distinct uplink instants, so an out-of-range reading
    # (value null, quality 2) still counts: the component measures that the
    # plot is being measured (docs/11 §2).
    node = _node(interval_s=300, claimed_seconds=4320 * 300, received=2160)

    assert monitoring_component([node]) == Decimal("0.5")


# --- record_keeping (D-T0.3) ---------------------------------------------


def test_weeks_in_month_counts_iso_weeks_overlapping_the_month() -> None:
    # September 2026 starts on Tuesday and has 30 days: the weeks overlapping
    # it are the one starting Aug 31 plus four whole ones = 5.
    assert weeks_in_month(MONTH) == 5


def test_weeks_in_month_of_a_28_day_month_four_saturday() -> None:
    # February 2026: Feb 1 is a Sunday, so the weeks overlapping it are
    # Jan 26, Feb 2, 9, 16, 23 = 5.
    assert weeks_in_month(date(2026, 2, 1)) == 5


def test_record_keeping_is_weeks_with_entries_over_weeks_of_the_month() -> None:
    # Two of September 2026's five overlapping weeks hold entries.
    assert record_keeping_component(2, MONTH) == Decimal(2) / Decimal(5)


def test_record_keeping_is_zero_in_a_month_without_entries() -> None:
    # The denominator always exists ("semanas del mes" is calendar math, >= 4),
    # so a silent month is 0, never null: docs/11:57 enumerates the three
    # components that can lose their denominator and record_keeping is not one.
    assert record_keeping_component(0, MONTH) == Decimal(0)


def test_record_keeping_is_one_when_every_week_of_the_month_has_an_entry() -> None:
    assert record_keeping_component(5, MONTH) == Decimal(1)


# --- decision (D-T0.5) ----------------------------------------------------


def test_decision_counts_an_irrigate_day_within_twenty_five_percent() -> None:
    days = [
        _decision(date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("12"))
    ]

    assert decision_component(days, rainfed=False) == Decimal(1)


def test_decision_counts_an_irrigate_day_at_the_lower_tolerance_edge() -> None:
    days = [
        _decision(
            date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("7.5")
        )
    ]

    assert decision_component(days, rainfed=False) == Decimal(1)


def test_decision_rejects_an_irrigate_day_outside_the_tolerance() -> None:
    days = [
        _decision(
            date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("7.4")
        )
    ]

    assert decision_component(days, rainfed=False) == Decimal(0)


def test_decision_rejects_an_irrigate_day_with_no_irrigation_recorded() -> None:
    days = [_decision(date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"))]

    assert decision_component(days, rainfed=False) == Decimal(0)


def test_decision_counts_a_postpone_day_without_irrigation() -> None:
    days = [_decision(date(2026, 9, 1), "postpone")]

    assert decision_component(days, rainfed=False) == Decimal(1)


def test_decision_rejects_a_postpone_day_that_irrigated() -> None:
    days = [_decision(date(2026, 9, 1), "postpone", irrigation_mm=Decimal("3"))]

    assert decision_component(days, rainfed=False) == Decimal(0)


def test_decision_counts_a_not_needed_day_without_irrigation() -> None:
    days = [_decision(date(2026, 9, 1), "not_needed")]

    assert decision_component(days, rainfed=False) == Decimal(1)


def test_decision_excludes_no_kc_and_rainfed_days_from_the_denominator() -> None:
    days = [
        _decision(
            date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("10")
        ),
        _decision(date(2026, 9, 2), "no_kc"),
        _decision(date(2026, 9, 3), "rainfed"),
    ]

    # Only the irrigate day is in the denominator, and it was followed.
    assert decision_component(days, rainfed=False) == Decimal(1)


def test_decision_is_none_without_days_that_count() -> None:
    assert decision_component([_decision(date(2026, 9, 1), "no_kc")], rainfed=False) is None


def test_decision_is_none_in_a_rainfed_plot() -> None:
    # A rainfed plot has no applied depth to follow, so the component does not
    # apply at all — not 0, not 1 (docs/11:52).
    days = [
        _decision(date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("10"))
    ]

    assert decision_component(days, rainfed=True) is None


def test_decision_is_a_ratio_of_followed_days() -> None:
    days = [
        _decision(
            date(2026, 9, 1), "irrigate", depth_mm=Decimal("10"), irrigation_mm=Decimal("10")
        ),
        _decision(date(2026, 9, 2), "irrigate", depth_mm=Decimal("10")),
        _decision(date(2026, 9, 3), "postpone"),
    ]

    assert decision_component(days, rainfed=False) == Decimal(2) / Decimal(3)


# --- risk_management (D-T0.6) --------------------------------------------


def test_risk_management_is_timely_actions_over_plot_alerts() -> None:
    assert risk_management_component(alerts_opened=2, timely_actions=1) == Decimal("0.5")


def test_risk_management_is_one_when_every_alert_was_acted_on() -> None:
    assert risk_management_component(alerts_opened=3, timely_actions=3) == Decimal(1)


def test_risk_management_is_none_without_plot_alerts() -> None:
    # Node alerts are already out of T3's view: no plot alert means missing
    # evidence, not a plot that ignored its alerts (docs/11:57).
    assert risk_management_component(alerts_opened=0, timely_actions=0) is None


# --- the index (D-T0.3) ---------------------------------------------------


def test_index_splits_a_hundred_points_evenly_over_four_components() -> None:
    components = AdoptionComponents(
        monitoring=Decimal(1),
        record_keeping=Decimal("0.5"),
        decision=Decimal("0"),
        risk_management=Decimal("0.5"),
    )

    assert digital_adoption_index(components) == Decimal(50)


def test_index_gives_a_rainfed_plot_a_hundred_thirds_weight() -> None:
    # docs/11:52: decision does not apply, so the other three weigh 100/3 each.
    # Holding four equal weights would instead give 25 points to each component
    # for 75 total, losing the 25 points decision would have earned.
    components = AdoptionComponents(
        monitoring=Decimal(1), record_keeping=Decimal(1), decision=None, risk_management=Decimal(0)
    )

    assert digital_adoption_index(components) == Decimal(100) * Decimal(2) / Decimal(3)


def test_index_is_none_when_every_component_is_null() -> None:
    components = AdoptionComponents(
        monitoring=None, record_keeping=None, decision=None, risk_management=None
    )

    assert digital_adoption_index(components) is None


def test_index_is_zero_only_when_a_real_component_is_zero() -> None:
    # A zero component means "did not happen", which is evidence; nulls are the
    # ones that must not drag the index down.
    components = AdoptionComponents(
        monitoring=Decimal(0),
        record_keeping=Decimal(0),
        decision=None,
        risk_management=Decimal(0),
    )

    assert digital_adoption_index(components) == Decimal(0)


def test_index_keeps_the_mean_of_the_non_null_components() -> None:
    components = AdoptionComponents(
        monitoring=None, record_keeping=Decimal(1), decision=None, risk_management=Decimal(0)
    )

    assert digital_adoption_index(components) == Decimal(50)


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        (AdoptionComponents(Decimal(1), Decimal(1), Decimal(1), Decimal(1)), Decimal(100)),
        (AdoptionComponents(Decimal(0), Decimal(0), Decimal(0), Decimal(0)), Decimal(0)),
        (AdoptionComponents(Decimal("0.5"), None, None, None), Decimal(50)),
    ],
)
def test_index_is_bounded_between_zero_and_one_hundred(
    components: AdoptionComponents, expected: Decimal
) -> None:
    index = digital_adoption_index(components)

    assert index == expected
    assert index is not None and Decimal(0) <= index <= Decimal(100)
