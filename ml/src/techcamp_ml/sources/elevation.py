"""Elevation at the seat and at four neighbours 1 km away, the slope input.

The output columns are exactly what `techcamp.risk.domain.features.Neighbours` takes,
so the shared feature module never reshapes a row (docs/08 §M2 "Features"). The
metres-to-degrees conversion uses the seat's own latitude: longitude degrees shrink
towards the poles, so the same 1000 m is a smaller step in longitude than in latitude.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

import pandas as pd

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
NEIGHBOUR_SPACING_M = 1000.0
METRES_PER_DEGREE_LAT = 111_320.0
MAX_COORDINATES_PER_REQUEST = 100
"""The elevation API answers at most 100 coordinates per request."""
POINT = tuple[str, float, float]
NEIGHBOUR_DIRECTIONS = ("east", "west", "north", "south")
COLUMNS = [
    "code",
    "lat",
    "lon",
    "elevation_m",
    "east_m",
    "west_m",
    "north_m",
    "south_m",
    "spacing_m",
]


def neighbour_offsets(lat: float, *, spacing_m: float = NEIGHBOUR_SPACING_M) -> dict[str, float]:
    """The four neighbour offsets in degrees at `lat`."""
    d_lat = spacing_m / METRES_PER_DEGREE_LAT
    d_lon = spacing_m / (METRES_PER_DEGREE_LAT * math.cos(math.radians(lat)))
    return {"east": d_lon, "west": -d_lon, "north": d_lat, "south": -d_lat}


def elevation_points(frame: pd.DataFrame, *, spacing_m: float = NEIGHBOUR_SPACING_M) -> list[POINT]:
    """Every coordinate the elevation API has to answer: the seat plus four neighbours."""
    points: list[POINT] = []
    for code, lat, lon in zip(frame["code"], frame["lat"], frame["lon"], strict=True):
        offsets = neighbour_offsets(float(lat), spacing_m=spacing_m)
        steps = {
            "east": (0.0, offsets["east"]),
            "west": (0.0, offsets["west"]),
            "north": (offsets["north"], 0.0),
            "south": (offsets["south"], 0.0),
        }
        points.append((str(code), float(lat), float(lon)))
        points.extend(
            (f"{code}.{direction}", float(lat) + d_lat, float(lon) + d_lon)
            for direction, (d_lat, d_lon) in steps.items()
        )
    return points


def parse_elevation(payload: bytes, points: Sequence[POINT]) -> pd.DataFrame:
    """One row per seat, with its own elevation and the four neighbour elevations."""
    elevations: list[float] = json.loads(payload)["elevation"]
    if len(elevations) != len(points):
        raise ValueError(
            f"elevation response holds {len(elevations)} values for {len(points)} codes"
        )
    rows: list[dict[str, float | str]] = []
    for index in range(0, len(points), len(NEIGHBOUR_DIRECTIONS) + 1):
        code, lat, lon = points[index]
        centre, *around = elevations[index : index + len(NEIGHBOUR_DIRECTIONS) + 1]
        row: dict[str, float | str] = {
            "code": code,
            "lat": lat,
            "lon": lon,
            "elevation_m": centre,
            "spacing_m": NEIGHBOUR_SPACING_M,
        }
        row.update(
            {
                f"{direction}_m": value
                for direction, value in zip(NEIGHBOUR_DIRECTIONS, around, strict=True)
            }
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)
