"""ISRIC SoilGrids v2.0 REST adapter (RF-03; docs/04-api.md:50; ADR-0021).

One adapter class serves both real implementations the port needs
(ADR-0002: a port for external I/O needing a test double): a live network
call for production, and an injected `httpx.MockTransport` for the seminar
profile's recorded fixture and for this module's own tests. Both paths run
the identical response-parsing code (ponytail: reuse over a second class).

Verified against docs.isric.org/rest.isric.org (2026-09-23):
- `/properties/query` takes `lon`, `lat`, repeated `property` and `depth`
  query params, and `value` (`mean`, `Q0.05`, `Q0.5`, `Q0.95`).
- Six standard depths (`0-5cm` … `100-200cm`); this adapter reads only
  `0-5cm` (topsoil) — a simplification: FAO-56 water balance cares about
  the root zone, not just the topsoil, but depth-weighting across the six
  layers is out of scope for this task.
  ponytail: topsoil-only, depth-weight across `Zr` if root-zone accuracy
  matters later.
- Each property's `properties.layers[].unit_measure.d_factor` converts the
  integer mapped value to the documented conventional unit (e.g. `sand`:
  g/kg mapped, d_factor 10, target g/100g = %; `phh2o`: pH×10 mapped,
  d_factor 10, target pH; `soc`: dg/kg mapped, d_factor 10, target g/kg).
  This adapter reads `d_factor` from the response itself rather than
  hardcoding it, so it stays correct if ISRIC changes a factor.
- The live `/properties/query` endpoint returned `503 Service Unavailable`
  when this task tried to fetch a real response (2026-09-23), so the
  seminar/test fixture below is built from the documented schema, not a
  captured live response — disclosed per this task's instructions.
"""

from __future__ import annotations

from typing import Any

import httpx

from techcamp.farms.domain.errors import SoilGridsUnavailableError
from techcamp.farms.domain.models import SoilGridsSample

_DEFAULT_BASE_URL = "https://rest.isric.org/soilgrids/v2.0"
_TIMEOUT_SECONDS = 10.0
_DEPTH_LABEL = "0-5cm"

SOILGRIDS_PROPERTIES: tuple[str, ...] = (
    "phh2o",
    "soc",
    "sand",
    "silt",
    "clay",
    "wv0033",
    "wv1500",
)


def _extract_conventional(
    layers: list[dict[str, Any]], name: str, depth_label: str
) -> float | None:
    """The response's own `mean / d_factor` for one property/depth, or
    `None` when SoilGrids has no prediction there (a normal outcome, not
    a failure — e.g. open water or masked pixels)."""
    for layer in layers:
        if layer.get("name") != name:
            continue
        d_factor = layer.get("unit_measure", {}).get("d_factor")
        for depth in layer.get("depths", []):
            if depth.get("label") != depth_label:
                continue
            mean = depth.get("values", {}).get("mean")
            if mean is None or not d_factor:
                return None
            return float(mean) / float(d_factor)
    return None


class IsricSoilGridsAdapter:
    """Real ISRIC SoilGrids v2.0 adapter. Pass `transport` to intercept the
    HTTP call (the seminar fixture below, or a test's own `MockTransport`);
    omit it for a real network call."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._base_url = base_url
        self._timeout = timeout

    async def fetch_sample(self, lon: float, lat: float) -> SoilGridsSample:
        async with httpx.AsyncClient(
            transport=self._transport, base_url=self._base_url, timeout=self._timeout
        ) as client:
            try:
                response = await client.get(
                    "/properties/query",
                    params={
                        "lon": lon,
                        "lat": lat,
                        "property": list(SOILGRIDS_PROPERTIES),
                        "depth": [_DEPTH_LABEL],
                        "value": ["mean"],
                    },
                )
            except httpx.RequestError as exc:
                raise SoilGridsUnavailableError(upstream_status=None, detail=str(exc)) from exc

        if response.status_code != 200:
            raise SoilGridsUnavailableError(
                upstream_status=response.status_code,
                detail=f"SoilGrids returned status {response.status_code}",
            )
        # Single parsing boundary (GitHub issue #21 round 7): every malformed
        # 200 body — null or wrongly shaped `layers`, layer entries,
        # `unit_measure`, `depths`, `values`, or a non-numeric value — must
        # become a 502, never an uncaught 500 from a `.get`/`float()` call
        # deep inside `_extract_conventional`. One try/except around the
        # whole parse, not a per-field guard.
        try:
            layers = response.json()["properties"]["layers"]
            soc_g_per_kg = _extract_conventional(layers, "soc", _DEPTH_LABEL)
            sample = SoilGridsSample(
                ph=_extract_conventional(layers, "phh2o", _DEPTH_LABEL),
                organic_carbon_pct=(soc_g_per_kg / 10 if soc_g_per_kg is not None else None),
                sand_pct=_extract_conventional(layers, "sand", _DEPTH_LABEL),
                silt_pct=_extract_conventional(layers, "silt", _DEPTH_LABEL),
                clay_pct=_extract_conventional(layers, "clay", _DEPTH_LABEL),
                field_capacity_pct=_extract_conventional(layers, "wv0033", _DEPTH_LABEL),
                wilting_point_pct=_extract_conventional(layers, "wv1500", _DEPTH_LABEL),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise SoilGridsUnavailableError(
                upstream_status=response.status_code, detail="malformed SoilGrids response"
            ) from exc
        return sample


def _fixture_layer(name: str, *, mean: float, d_factor: float = 10) -> dict[str, Any]:
    return {
        "name": name,
        "unit_measure": {"d_factor": d_factor},
        "depths": [
            {
                "label": _DEPTH_LABEL,
                "range": {"top_depth": 0, "bottom_depth": 5, "unit_depth": "cm"},
                "values": {"mean": mean},
            }
        ],
    }


SEMINAR_FIXTURE_RESPONSE: dict[str, Any] = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-74.095, 10.905]},
    "query_time_seconds": 0.05,
    "properties": {
        "layers": [
            _fixture_layer("phh2o", mean=65),  # pH 6.5
            _fixture_layer("soc", mean=150),  # 15 g/kg -> 1.5% organic carbon
            _fixture_layer("sand", mean=400),  # 40%
            _fixture_layer("silt", mean=400),  # 40%
            _fixture_layer("clay", mean=200),  # 20% -> sand/silt/clay classify as "loam"
            _fixture_layer("wv0033", mean=250),  # 25% θFC
            _fixture_layer("wv1500", mean=120),  # 12% θWP
        ]
    },
}
"""Built from the documented v2.0 response schema (docs.isric.org), not a
captured live response: `/properties/query` returned 503 when this task
tried to fetch one (2026-09-23). Values chosen so the derived texture is
"loam" and θFC/θWP (25%/12%) land close to this repo's own FAO-56 Table 19
loam means (25.0/12.0), a plausible Caribbean-lowland loam soil for the
demo — not a real ISRIC prediction for these coordinates."""


def seminar_soilgrids_adapter() -> IsricSoilGridsAdapter:
    """ADR-0021: the seminar profile serves this recorded, offline,
    deterministic response instead of a live ISRIC call, like weather's
    recorded fixtures."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SEMINAR_FIXTURE_RESPONSE)

    return IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))
