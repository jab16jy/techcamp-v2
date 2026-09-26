"""Pure weather domain rules: 0.1° cell rounding and the stale rule
(docs/00-glosario.md:40, docs/06-diseno-detallado.md §6).

No database and no clock: `cell_for` is pure arithmetic and `is_stale` takes
`now` as an argument, so every case is decided by the value under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from techcamp.weather.domain.models import STALE_AFTER, cell_for, is_stale

_NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def test_cell_for_rounds_to_the_tenth_degree() -> None:
    assert cell_for(10.94, -74.14) == (Decimal("10.9"), Decimal("-74.1"))


def test_cell_for_keeps_a_point_that_already_sits_on_the_grid() -> None:
    assert cell_for(10.9, -74.1) == (Decimal("10.9"), Decimal("-74.1"))


def test_cell_for_keeps_every_plot_of_one_cell_on_the_same_grid_point() -> None:
    """The cell is the Open-Meteo cache (docs/09-cuellos-de-botella.md:39), so
    two plots inside the same 0.1° square must resolve to the same cell."""
    assert cell_for(10.91, -74.14) == cell_for(10.94, -74.11) == (Decimal("10.9"), Decimal("-74.1"))


def test_cell_for_rounds_a_half_away_from_zero() -> None:
    """A point exactly on a cell border is decided the same way in both
    hemispheres and never depends on the binary representation of the float,
    which `round()` would: `round(-74.15, 1)` is `-74.1` on one build and
    `-74.2` on another."""
    assert cell_for(10.95, -74.15) == (Decimal("11.0"), Decimal("-74.2"))


def test_cell_for_rounds_a_negative_longitude_towards_its_own_side() -> None:
    """Colombian Caribbean longitudes are negative, so both directions of the
    rounding have to hold below the equator."""
    assert cell_for(10.9, -74.16)[1] == Decimal("-74.2")
    assert cell_for(10.9, -74.14)[1] == Decimal("-74.1")


def test_cell_for_returns_the_same_cell_for_a_decimal_and_its_float() -> None:
    """The coordinates reach a `numeric` column under `UNIQUE(lat, lon)`, and a
    float bound there arrives as its full binary expansion: a cell resolved
    from a float must be the very same cell as one resolved from a `Decimal`,
    or the cache row splits in two."""
    assert cell_for(Decimal("10.94"), Decimal("-74.14")) == cell_for(10.94, -74.14)


def test_is_stale_is_false_within_six_hours_of_the_last_fetch() -> None:
    assert is_stale(_NOW - timedelta(hours=5), _NOW) is False
    assert is_stale(_NOW - STALE_AFTER, _NOW) is False


def test_is_stale_is_true_past_six_hours() -> None:
    """Six hours is two missed 3 h refreshes (docs/06-diseno-detallado.md §6),
    the point where the cell is served marked `stale` instead of trusted."""
    assert is_stale(_NOW - STALE_AFTER - timedelta(seconds=1), _NOW) is True
    assert is_stale(_NOW - timedelta(hours=30), _NOW) is True


def test_stale_after_is_six_hours() -> None:
    assert STALE_AFTER == timedelta(hours=6)
