"""Elevation: the point plus four neighbours that feed `risk.domain.features.Neighbours`."""

import math
from collections.abc import Callable

import pytest
from techcamp.risk.domain.features import Neighbours, slope_degrees

from techcamp_ml.sources.elevation import (
    METRES_PER_DEGREE_LAT,
    NEIGHBOUR_SPACING_M,
    elevation_points,
    neighbour_offsets,
    parse_elevation,
)

POINTS = [
    ("08001", 10.977961, -74.815546),
    ("20045", 9.704404, -73.278707),
]


def test_neighbour_offsets_convert_metres_to_degrees_at_that_latitude() -> None:
    offsets = neighbour_offsets(10.977961, spacing_m=NEIGHBOUR_SPACING_M)

    assert offsets["north"] == pytest.approx(NEIGHBOUR_SPACING_M / METRES_PER_DEGREE_LAT)
    assert offsets["south"] == pytest.approx(-NEIGHBOUR_SPACING_M / METRES_PER_DEGREE_LAT)
    # Longitude degrees shrink with the cosine of the latitude, so the two steps differ.
    expected_lon = NEIGHBOUR_SPACING_M / (METRES_PER_DEGREE_LAT * math.cos(math.radians(10.977961)))
    assert offsets["east"] == pytest.approx(expected_lon)
    assert offsets["east"] != pytest.approx(offsets["north"])
    assert offsets["west"] == pytest.approx(-expected_lon)
    # Past the tropics the same 1000 m is a much wider step in longitude.
    assert neighbour_offsets(60.0)["east"] == pytest.approx(
        NEIGHBOUR_SPACING_M / (METRES_PER_DEGREE_LAT * 0.5)
    )


def test_elevation_points_asks_for_the_seat_and_its_four_neighbours() -> None:
    import pandas as pd

    points = elevation_points(pd.DataFrame(POINTS, columns=["code", "lat", "lon"]))

    assert len(points) == 10, "2 seats x (centre + 4 neighbours)"
    assert points[0] == ("08001", 10.977961, -74.815546)
    labels = [code for code, _, _ in points[1:5]]
    assert labels == ["08001.east", "08001.west", "08001.north", "08001.south"]
    east = next(point for point in points if point[0] == "08001.east")
    assert east[2] > points[0][2], "east moves longitude"
    assert east[1] == pytest.approx(points[0][1]), "east does not move latitude"


def test_parse_elevation_emits_exactly_what_neighbours_consumes(
    fixture: Callable[[str], bytes],
) -> None:
    import pandas as pd

    points = elevation_points(pd.DataFrame(POINTS, columns=["code", "lat", "lon"]))
    frame = parse_elevation(fixture("open_meteo_elevation.json"), points)

    assert len(frame) == 2
    assert list(frame.columns) == [
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
    row = frame.iloc[0]
    assert row["elevation_m"] == pytest.approx(104.0)
    assert row["east_m"] == pytest.approx(76.0)
    assert row["south_m"] == pytest.approx(56.0)
    assert row["spacing_m"] == pytest.approx(1000.0)
    # The feature module takes the row as-is: no reshaping between the two sides.
    neighbours = Neighbours(
        east=row["east_m"],
        west=row["west_m"],
        north=row["north_m"],
        south=row["south_m"],
        spacing_m=row["spacing_m"],
    )
    assert 0.0 <= slope_degrees(neighbours) < 90.0


def test_parse_elevation_rejects_a_response_that_is_not_one_value_per_coordinate(
    fixture: Callable[[str], bytes],
) -> None:
    import pandas as pd

    points = elevation_points(pd.DataFrame(POINTS, columns=["code", "lat", "lon"]))
    with pytest.raises(ValueError, match="11 codes"):
        parse_elevation(fixture("open_meteo_elevation.json"), [*points, points[0]])
