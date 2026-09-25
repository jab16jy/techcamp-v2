"""HTTP flow for the node simulator: dev OTP login, node claim, and ensuring
each sensor has a calibration (docs/06-diseno-detallado.md §10). Talks to a
live `api` process with `httpx` (already a dependency) over the same routes
T3 built (`techcamp/telemetry/adapters/api/router.py`) and the seminar-only
dev login (docs/04-api.md:173-174, ADR-0021)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx

from techcamp.telemetry.domain.models import CalibrationKind, CalibrationMethod


@dataclass(frozen=True, slots=True)
class ClaimedNode:
    node_id: UUID
    mqtt_username: str
    mqtt_password: str
    sensors: list[dict[str, Any]]
    """Raw `SensorView` JSON objects (id, node_id, channel_key, metric,
    depth_cm, unit) — kept as dicts, the simulator has no need for its own
    sensor model beyond `channel_key`/`unit`/`id`."""


async def request_otp(client: httpx.AsyncClient, phone: str) -> None:
    """docs/04-api.md:173: the code is printed to the `api` process console,
    not returned here — see `techcamp.simulator.__main__` for how the CLI
    obtains it."""
    response = await client.post("/dev/auth/otp", json={"phone": phone})
    response.raise_for_status()


async def verify_otp(client: httpx.AsyncClient, phone: str, code: str) -> str:
    """docs/04-api.md:174. Returns the bearer access token."""
    response = await client.post("/dev/auth/otp/verify", json={"phone": phone, "code": code})
    response.raise_for_status()
    return str(response.json()["access_token"])


async def claim_node(
    client: httpx.AsyncClient, *, token: str, claim_code: str, plot_id: UUID
) -> ClaimedNode:
    """`POST /nodes:claim` then `GET /nodes/{id}/sensors`
    (docs/06-diseno-detallado.md §2, §10)."""
    headers = {"Authorization": f"Bearer {token}"}
    claim_response = await client.post(
        "/nodes:claim",
        json={"claim_code": claim_code, "plot_id": str(plot_id)},
        headers=headers,
    )
    claim_response.raise_for_status()
    body = claim_response.json()
    node_id = UUID(body["id"])

    sensors_response = await client.get(f"/nodes/{node_id}/sensors", headers=headers)
    sensors_response.raise_for_status()

    return ClaimedNode(
        node_id=node_id,
        mqtt_username=body["mqtt"]["username"],
        mqtt_password=body["mqtt"]["password"],
        sensors=sensors_response.json(),
    )


def _calibration_payload(sensor: dict[str, Any], *, valid_from: datetime) -> dict[str, Any]:
    """docs/03-modelo-datos.md:463-467. A `%` sensor (soil moisture) gets the
    doc's own two-point example; any other unit gets an identity linear
    calibration — no other unit/range convention is documented, so this
    doesn't invent one (T8 gap, see the task report)."""
    if sensor["unit"] == "%":
        method: CalibrationMethod = CalibrationMethod.TWO_POINT
        params: dict[str, Any] = {"raw_dry": 2900, "raw_wet": 1300, "vwc_dry": 5, "vwc_wet": 45}
    else:
        method = CalibrationMethod.LINEAR
        params = {"scale": 1.0, "offset": 0.0}
    return {
        "method": method.value,
        "kind": CalibrationKind.FIELD.value,
        "params": params,
        "valid_from": valid_from.isoformat(),
    }


async def ensure_calibrations(
    client: httpx.AsyncClient, *, token: str, node: ClaimedNode, valid_from: datetime
) -> None:
    """POST one calibration per sensor so backfilled readings land calibrated
    (docs/06 §10: "cada nodo simulado tiene su calibración")."""
    headers = {"Authorization": f"Bearer {token}"}
    for sensor in node.sensors:
        response = await client.post(
            f"/sensors/{sensor['id']}/calibrations",
            json=_calibration_payload(sensor, valid_from=valid_from),
            headers=headers,
        )
        response.raise_for_status()
