from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from techcamp.shared.dates import BOGOTA_TZ, local_today


def test_bogota_tz_is_america_bogota() -> None:
    assert BOGOTA_TZ == ZoneInfo("America/Bogota")
    assert str(BOGOTA_TZ) == "America/Bogota"


def test_local_today_early_utc_instant_is_previous_bogota_date() -> None:
    """An instant between 00:00 and 05:00 UTC is 19:00-23:59 the previous day in
    America/Bogota (UTC-5, no DST), so it must map to the previous Bogota date.
    """
    now = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)  # 2026-09-26 22:00 Bogota
    assert local_today(now) == date(2026, 9, 26)


def test_local_today_naive_input_is_treated_as_utc() -> None:
    """A naive `now` (no tzinfo) is read as UTC, matching how the application layer
    stores and passes instants (docs/06 §5).
    """
    naive_now = datetime(2026, 9, 27, 3, 0)  # no tzinfo -> treated as UTC
    assert local_today(naive_now) == date(2026, 9, 26)

    # A naive instant late enough in UTC stays on the same Bogota date.
    naive_now_late = datetime(2026, 9, 27, 20, 0)  # 15:00 Bogota, same day
    assert local_today(naive_now_late) == date(2026, 9, 27)


def test_local_today_default_none_uses_current_time() -> None:
    """When now is omitted or None, local_today returns the current America/Bogota date."""
    expected = datetime.now(BOGOTA_TZ).date()
    assert local_today() == expected
    assert local_today(None) == expected
