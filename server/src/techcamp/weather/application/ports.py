"""Repository ports the weather application layer depends on (never on adapters).

Hexagonal layering per ADR-0002 and ADR-0003: application defines the
interfaces it needs, adapters implement them.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from techcamp.weather.domain.models import WeatherDay


class WeatherRepository(Protocol):
    async def list_daily(self, cell_id: int, from_day: date, to_day: date) -> list[WeatherDay]: ...
