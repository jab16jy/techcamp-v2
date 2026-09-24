"""GeoJSON <-> WKT conversion for the farms API (docs/04-api.md).

The domain and repositories only ever see WKT text (farms/domain/models.py);
this is the one place that turns a client's GeoJSON payload into WKT for
writes, and a stored WKT string back into GeoJSON for responses. No shapely
dependency: PostGIS's `ST_AsText` output for a POINT/POLYGON is a small,
fixed grammar that a couple of string splits parse directly (ponytail).
"""

from __future__ import annotations

Position = tuple[float, float]


def point_to_wkt(coordinates: Position) -> str:
    """GeoJSON Point coordinates to EWKT, for inserts (SRID 4326, docs/03)."""
    x, y = coordinates
    return f"SRID=4326;POINT({x} {y})"


def polygon_to_wkt(rings: list[list[Position]]) -> str:
    """GeoJSON Polygon coordinates to EWKT, for inserts (SRID 4326, docs/03)."""
    rings_wkt = ",".join("(" + ",".join(f"{x} {y}" for x, y in ring) + ")" for ring in rings)
    return f"SRID=4326;POLYGON({rings_wkt})"


def wkt_to_point(wkt: str) -> Position:
    """Plain WKT (as returned by `ST_AsText`) to GeoJSON Point coordinates."""
    x, y = wkt.removeprefix("POINT(").removesuffix(")").split()
    return (float(x), float(y))


def wkt_to_polygon(wkt: str) -> list[list[Position]]:
    """Plain WKT (as returned by `ST_AsText`) to GeoJSON Polygon coordinates."""
    body = wkt.removeprefix("POLYGON(")[1:-2]
    return [[_parse_position(pair) for pair in ring.split(",")] for ring in body.split("),(")]


def _parse_position(pair: str) -> Position:
    x, y = pair.split()
    return (float(x), float(y))
