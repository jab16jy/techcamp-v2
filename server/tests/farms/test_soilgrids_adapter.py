"""ISRIC SoilGrids adapter tests (T5): an injected `httpx.MockTransport`
stands in for the network, per this task's instruction — no real HTTP call
in tests, and both the production and seminar code paths share this parsing
logic (`techcamp/farms/adapters/soilgrids.py`).
"""

from __future__ import annotations

import httpx
import pytest

from techcamp.farms.adapters.soilgrids import (
    SEMINAR_FIXTURE_RESPONSE,
    IsricSoilGridsAdapter,
    seminar_soilgrids_adapter,
)
from techcamp.farms.domain.errors import SoilGridsUnavailableError
from techcamp.farms.domain.models import SoilGridsSample

pytestmark = pytest.mark.anyio


async def test_fetch_sample_decodes_the_recorded_fixture() -> None:
    adapter = seminar_soilgrids_adapter()

    sample = await adapter.fetch_sample(lon=-74.095, lat=10.905)

    assert sample.ph == pytest.approx(6.5)
    assert sample.organic_carbon_pct == pytest.approx(1.5)
    assert sample.sand_pct == pytest.approx(40.0)
    assert sample.silt_pct == pytest.approx(40.0)
    assert sample.clay_pct == pytest.approx(20.0)
    assert sample.field_capacity_pct == pytest.approx(25.0)
    assert sample.wilting_point_pct == pytest.approx(12.0)


async def test_fetch_sample_ignores_query_coordinates_for_the_recorded_fixture() -> None:
    """The seminar fixture is deterministic and offline (ADR-0021): it
    doesn't vary with the plot's actual centroid."""
    adapter = seminar_soilgrids_adapter()

    sample = await adapter.fetch_sample(lon=1.0, lat=2.0)

    assert sample.ph == pytest.approx(6.5)


async def test_fetch_sample_treats_a_missing_property_as_none_not_a_failure() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"properties": {"layers": []}})

    adapter = IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))

    sample = await adapter.fetch_sample(lon=-74.0, lat=10.0)

    assert sample == SoilGridsSample(
        ph=None,
        organic_carbon_pct=None,
        sand_pct=None,
        silt_pct=None,
        clay_pct=None,
        field_capacity_pct=None,
        wilting_point_pct=None,
    )


async def test_fetch_sample_raises_with_upstream_status_on_a_non_200_response() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    adapter = IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(SoilGridsUnavailableError) as exc_info:
        await adapter.fetch_sample(lon=-74.0, lat=10.0)

    assert exc_info.value.upstream_status == 503


async def test_fetch_sample_raises_with_no_upstream_status_on_a_connection_failure() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(SoilGridsUnavailableError) as exc_info:
        await adapter.fetch_sample(lon=-74.0, lat=10.0)

    assert exc_info.value.upstream_status is None


async def test_fetch_sample_raises_on_a_malformed_response_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    adapter = IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(SoilGridsUnavailableError):
        await adapter.fetch_sample(lon=-74.0, lat=10.0)


_MALFORMED_BODIES: dict[str, object] = {
    "null layers": {"properties": {"layers": None}},
    "layers is a dict, not a list": {"properties": {"layers": {"name": "soc"}}},
    "layer entry is a string": {"properties": {"layers": ["not-a-layer-dict"]}},
    "layer entry is a number": {"properties": {"layers": [1]}},
    "null unit_measure": {
        "properties": {
            "layers": [
                {
                    "name": "soc",
                    "unit_measure": None,
                    "depths": [{"label": "0-5cm", "values": {"mean": 150}}],
                }
            ]
        }
    },
    "null depths": {
        "properties": {
            "layers": [{"name": "soc", "unit_measure": {"d_factor": 10}, "depths": None}]
        }
    },
    "depth entry is a string": {
        "properties": {
            "layers": [{"name": "soc", "unit_measure": {"d_factor": 10}, "depths": ["0-5cm"]}]
        }
    },
    "null values": {
        "properties": {
            "layers": [
                {
                    "name": "soc",
                    "unit_measure": {"d_factor": 10},
                    "depths": [{"label": "0-5cm", "values": None}],
                }
            ]
        }
    },
    "non-numeric mean": {
        "properties": {
            "layers": [
                {
                    "name": "soc",
                    "unit_measure": {"d_factor": 10},
                    "depths": [{"label": "0-5cm", "values": {"mean": "not-a-number"}}],
                }
            ]
        }
    },
    "non-numeric d_factor": {
        "properties": {
            "layers": [
                {
                    "name": "soc",
                    "unit_measure": {"d_factor": "not-a-number"},
                    "depths": [{"label": "0-5cm", "values": {"mean": 150}}],
                }
            ]
        }
    },
}


@pytest.mark.parametrize("body", _MALFORMED_BODIES.values(), ids=list(_MALFORMED_BODIES))
async def test_fetch_sample_raises_soil_grids_unavailable_on_every_malformed_shape(
    body: object,
) -> None:
    """GitHub issue #21 round 7: a malformed 200 body must raise
    `SoilGridsUnavailableError` (-> 502), never an uncaught `TypeError`/
    `AttributeError`/`ValueError` from deep inside `_extract_conventional`
    (-> a raw 500)."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    adapter = IsricSoilGridsAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(SoilGridsUnavailableError) as exc_info:
        await adapter.fetch_sample(lon=-74.0, lat=10.0)

    assert exc_info.value.upstream_status == 200


def test_seminar_fixture_matches_the_documented_v2_schema() -> None:
    """The fixture wasn't captured live (`/properties/query` returned 503
    during this task): pin its documented shape so a refactor can't drift
    it silently."""
    layer_names = {layer["name"] for layer in SEMINAR_FIXTURE_RESPONSE["properties"]["layers"]}
    assert layer_names == {"phh2o", "soc", "sand", "silt", "clay", "wv0033", "wv1500"}
    for layer in SEMINAR_FIXTURE_RESPONSE["properties"]["layers"]:
        assert layer["unit_measure"]["d_factor"] == 10
        assert layer["depths"][0]["label"] == "0-5cm"
