"""Simulator application facade: the use cases the seminar tooling runs.

E16 T3: `load_weather_fixture`, the scenario's recorded weather landing in
`weather_daily` (docs/04-api.md:263; D-T16.2).
"""

from techcamp.simulator.application.load_weather import (
    WeatherFixtureWriter,
    load_weather_fixture,
)

__all__ = [
    "WeatherFixtureWriter",
    "load_weather_fixture",
]
