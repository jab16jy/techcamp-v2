"""WKT <-> GeoJSON round trip (docs/04-api.md: boundary/location as GeoJSON).

Pure functions, no I/O: PostGIS's ST_AsText grammar is small enough to parse
without a shapely dependency (ponytail).
"""

from techcamp.farms.adapters.geojson import (
    point_to_wkt,
    polygon_to_wkt,
    wkt_to_point,
    wkt_to_polygon,
)

_POINT_COORDS = (-74.1, 10.9)
_POLYGON_COORDS = [
    [(-74.10, 10.90), (-74.10, 10.91), (-74.09, 10.91), (-74.09, 10.90), (-74.10, 10.90)]
]


def test_point_to_wkt_is_ewkt_with_srid_4326() -> None:
    assert point_to_wkt(_POINT_COORDS) == "SRID=4326;POINT(-74.1 10.9)"


def test_polygon_to_wkt_is_ewkt_with_srid_4326() -> None:
    wkt = polygon_to_wkt(_POLYGON_COORDS)
    assert wkt.startswith("SRID=4326;POLYGON((")
    assert wkt.endswith("))")


def test_point_round_trips_through_plain_wkt() -> None:
    plain_wkt = "POINT(-74.1 10.9)"
    assert wkt_to_point(plain_wkt) == _POINT_COORDS


def test_polygon_round_trips_through_plain_wkt() -> None:
    plain_wkt = "POLYGON((-74.1 10.9,-74.1 10.91,-74.09 10.91,-74.09 10.9,-74.1 10.9))"
    assert wkt_to_polygon(plain_wkt) == [
        [(-74.1, 10.9), (-74.1, 10.91), (-74.09, 10.91), (-74.09, 10.9), (-74.1, 10.9)]
    ]
