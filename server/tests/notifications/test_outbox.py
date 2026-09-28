"""Notification due time and outbox planning (docs/06-diseno-detallado.md §4; ADR-0016; D5, D6)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from techcamp.notifications.application import plan_notifications
from techcamp.notifications.domain.models import Channel, next_attempt_at

_BOGOTA = ZoneInfo("America/Bogota")


def _bogota(year: int, month: int, day: int, hour: int) -> datetime:
    """An instant expressed in farm local time (docs/06 §4 hours are Bogotá)."""
    return datetime(year, month, day, hour, tzinfo=_BOGOTA).astimezone(UTC)


def test_critical_row_is_due_now_even_at_night() -> None:
    """Critical notifications ignore quiet hours (docs/06 §4, D6)."""
    at = _bogota(2026, 9, 26, 22)
    assert next_attempt_at(critical=True, now=at) == at


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        pytest.param(_bogota(2026, 9, 26, 20), _bogota(2026, 9, 27, 5), id="20:00-late-evening"),
        pytest.param(_bogota(2026, 9, 26, 23), _bogota(2026, 9, 27, 5), id="23:00-night"),
        pytest.param(_bogota(2026, 9, 27, 2), _bogota(2026, 9, 27, 5), id="02:00-early-morning"),
    ],
)
def test_non_critical_row_inside_quiet_hours_waits_for_0500(
    at: datetime, expected: datetime
) -> None:
    """20:00–05:00 Bogotá holds every non-critical row until 05:00 (D6)."""
    assert next_attempt_at(critical=False, now=at) == expected


@pytest.mark.parametrize(
    "at",
    [
        pytest.param(_bogota(2026, 9, 26, 5), id="05:00-quiet-hours-end"),
        pytest.param(_bogota(2026, 9, 26, 10), id="10:00-morning"),
        pytest.param(_bogota(2026, 9, 26, 19), id="19:00-before-quiet-hours"),
    ],
)
def test_non_critical_row_outside_quiet_hours_is_due_now(at: datetime) -> None:
    assert next_attempt_at(critical=False, now=at) == at


def test_non_critical_row_joins_the_pending_group_time() -> None:
    """A row created next to a pending one rides that row's due time (D6)."""
    group_time = _bogota(2026, 9, 27, 5)
    assert next_attempt_at(critical=False, now=_bogota(2026, 9, 26, 21), group_time=group_time) == (
        group_time
    )


def test_plan_notifications_writes_one_push_row_per_recipient() -> None:
    at = _bogota(2026, 9, 26, 10)
    recipients = [uuid4(), uuid4()]

    drafts = plan_notifications(recipients, critical=False, now=at)

    assert [draft.user_id for draft in drafts] == recipients
    assert {draft.channel for draft in drafts} == {Channel.PUSH}
    assert {draft.next_attempt_at for draft in drafts} == {at}


def test_plan_notifications_gives_each_recipient_its_own_group_time() -> None:
    at = _bogota(2026, 9, 26, 10)
    grouped, alone = uuid4(), uuid4()
    first_row = _bogota(2026, 9, 26, 10)

    drafts = plan_notifications(
        [grouped, alone], critical=False, now=at, group_times={grouped: first_row}
    )
    by_user = {draft.user_id: draft.next_attempt_at for draft in drafts}

    assert by_user[grouped] == first_row
    assert by_user[alone] == at


def test_plan_notifications_without_recipients_plans_nothing() -> None:
    """`info` is the caller's decision not to plan rows (D5)."""
    assert plan_notifications([], critical=False, now=_bogota(2026, 9, 26, 10)) == []
