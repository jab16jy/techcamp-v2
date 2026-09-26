"""`GET /api/v1/stream?farm_id=` (docs/04-api.md:180-189, ADR-0015, E4 T6).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a farm in another
organization responds 404, never 403. Events are delivered end to end
through a real `pg_notify('plot_events', ...)`, the same channel
`SqlAlchemyPlotEventsNotifier` (T4) publishes on.

The 200 cases run against a real `uvicorn` server (not `TestClient`/
`ASGITransport`): both only return a response after the whole ASGI app
coroutine finishes, so they can't stream an open-ended SSE body — they'd
hang forever waiting for our never-ending generator to complete. A real
socket streams incrementally, so the test can read a few lines and move on.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import asyncpg
import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow
from techcamp.main import app
from techcamp.shared.config import database_url
from techcamp.shared.db import engine
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters import sse_hub
from techcamp.telemetry.adapters.api.router import stream_events
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub

from .test_api import _auth, _member
from .test_sse_stream import _reading_payload

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"


async def _make_farm(db_session: AsyncSession, org_id: object, *, name: str = "Finca") -> UUID:
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name=name, municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    return farm_id


def _dsn() -> str:
    return database_url().replace("postgresql+asyncpg://", "postgresql://")


async def _notify(payload: dict[str, object]) -> None:
    conn = await asyncpg.connect(dsn=_dsn())
    try:
        await conn.execute("SELECT pg_notify('plot_events', $1)", json.dumps(payload))
    finally:
        await conn.close()


@pytest.fixture
async def live_base_url() -> AsyncIterator[str]:
    """A real `uvicorn` server for the app on a free port (see module
    docstring: `TestClient`/`ASGITransport` can't stream an open-ended SSE
    body)."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    # `server.started` only says the app booted: the hub connects in the
    # background (R3-lifespan-coupling), so a `pg_notify` sent right after
    # would be lost if `LISTEN plot_events` isn't registered yet (#38).
    hub = cast(PlotEventsHub, app.state.plot_events_hub)
    deadline = asyncio.get_event_loop().time() + 10.0
    while not hub.is_listening:
        assert asyncio.get_event_loop().time() < deadline, "the hub never registered LISTEN"
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def _read_until_data(response: httpx.Response) -> list[str]:
    lines: list[str] = []
    async for line in response.aiter_lines():
        lines.append(line)
        if line.startswith("data:"):
            break
    return lines


async def test_stream_404_for_farm_in_another_org(db_session: AsyncSession) -> None:
    _org_a, _user_a, token = await _member(db_session, role="owner")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Otra finca")
    foreign_farm_id = await _make_farm(db_session, org_b)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/stream", params={"farm_id": str(foreign_farm_id)}, headers=_auth(token)
        )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_stream_delivers_reading_event_for_the_subscribed_farm(
    db_session: AsyncSession, live_base_url: str
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    farm_id = await _make_farm(db_session, org_id)
    plot_id = uuid7()

    async with httpx.AsyncClient(base_url=live_base_url, timeout=10.0) as client:
        # `Last-Event-ID` is accepted and ignored (E4 T6 gap: docs are silent
        # on replay, so none is built).
        async with client.stream(
            "GET",
            "/api/v1/stream",
            params={"farm_id": str(farm_id)},
            headers={**_auth(token), "Last-Event-ID": "41"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"

            await _notify(
                {
                    "type": "reading",
                    "org_id": str(org_id),
                    "farm_id": str(farm_id),
                    "plot_id": str(plot_id),
                    "metric": "soil_moisture",
                    "value": 18.2,
                    "at": "2026-03-01T12:00:00+00:00",
                }
            )

            lines = await _read_until_data(response)

    # Not `id: 1`: `plot_events` is one shared Postgres channel, so a
    # concurrently running test's NOTIFY can bump this hub's counter first
    # (R3-id-assertion-shared-channel). Monotonic increase is covered at the
    # hub level by `test_hub_event_id_is_monotonic_per_process`.
    assert any(line.startswith("id: ") for line in lines)
    assert "event: reading" in lines
    data = json.loads(next(line for line in lines if line.startswith("data:"))[len("data: ") :])
    assert data == {
        "plot_id": str(plot_id),
        "metric": "soil_moisture",
        "value": 18.2,
        "at": "2026-03-01T12:00:00+00:00",
    }


async def test_stream_filters_out_events_for_other_farms(
    db_session: AsyncSession, live_base_url: str
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    farm_id = await _make_farm(db_session, org_id, name="Mine")
    other_farm_id = await _make_farm(db_session, org_id, name="Not mine")

    async with httpx.AsyncClient(base_url=live_base_url, timeout=10.0) as client:
        async with client.stream(
            "GET", "/api/v1/stream", params={"farm_id": str(farm_id)}, headers=_auth(token)
        ) as response:
            await _notify(
                {
                    "type": "reading",
                    "org_id": str(org_id),
                    "farm_id": str(other_farm_id),
                    "plot_id": str(uuid7()),
                    "metric": "soil_moisture",
                    "value": 1.0,
                    "at": "2026-03-01T12:00:00+00:00",
                }
            )
            await _notify(
                {
                    "type": "node.status",
                    "org_id": str(org_id),
                    "farm_id": str(farm_id),
                    "node_id": str(uuid7()),
                    "status": "offline",
                    "at": "2026-03-01T12:00:00+00:00",
                }
            )

            lines = await _read_until_data(response)

    assert "event: node.status" in lines
    data = json.loads(next(line for line in lines if line.startswith("data:"))[len("data: ") :])
    assert data["status"] == "offline"  # the other farm's reading never arrives first


async def test_stream_releases_db_session_before_streaming(
    db_session: AsyncSession, live_base_url: str
) -> None:
    """R3-stream-holds-db-session: a yield-scoped `SessionDep` stays checked
    out for FastAPI's dependency teardown, which for a `StreamingResponse`
    only runs after the whole (never-ending) response finishes. The access
    check must instead run in its own short-lived session, closed before the
    stream starts, so open streams never starve REST endpoints of pool
    connections."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    farm_id = await _make_farm(db_session, org_id)

    checked_out = 0

    def _on_checkout(*_args: object) -> None:
        nonlocal checked_out
        checked_out += 1

    def _on_checkin(*_args: object) -> None:
        nonlocal checked_out
        checked_out -= 1

    event.listen(engine.sync_engine, "checkout", _on_checkout)
    event.listen(engine.sync_engine, "checkin", _on_checkin)
    try:
        async with httpx.AsyncClient(base_url=live_base_url, timeout=10.0) as client:
            async with client.stream(
                "GET", "/api/v1/stream", params={"farm_id": str(farm_id)}, headers=_auth(token)
            ) as response:
                assert response.status_code == 200
                await asyncio.sleep(0.05)  # let dependency teardown run
                assert checked_out == 0, "the stream must not hold a checked-out db session"

                rest_response = await client.get(
                    "/api/v1/farms", params={"org_id": str(org_id)}, headers=_auth(token)
                )
                assert rest_response.status_code == 200
    finally:
        event.remove(engine.sync_engine, "checkout", _on_checkout)
        event.remove(engine.sync_engine, "checkin", _on_checkin)


def test_app_boots_when_listener_connect_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """R3-lifespan-coupling: the API must boot even when the `LISTEN`
    connection can't be established yet; the connect/retry runs in the
    background instead of failing `lifespan()`."""

    async def _always_fails(*, dsn: str) -> asyncpg.Connection[object]:
        raise OSError("connection refused")

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _always_fails)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


async def test_stream_route_subscribes_only_while_its_body_runs(
    db_session: AsyncSession,
) -> None:
    """#38: the route used to subscribe before returning the response, so a
    body that never starts (client gone, proxy dropped the response) left the
    subscription behind — only the generator's `finally` unsubscribes.

    The route function is called directly because a `StreamingResponse` body
    that never ends can't be consumed through `TestClient` (module docstring).
    """
    org_id, user_id, _token = await _member(db_session, role="owner")
    farm_id = await _make_farm(db_session, org_id)
    hub = PlotEventsHub()

    response = await stream_events(user_id=user_id, hub=hub, farm_id=farm_id)

    assert hub.subscriber_count == 0, "an unstarted body must not hold a subscription"

    body = cast(AsyncIterator[bytes], response.body_iterator)
    first = asyncio.ensure_future(body.__anext__())
    deadline = asyncio.get_event_loop().time() + 5.0
    while hub.subscriber_count == 0:
        assert asyncio.get_event_loop().time() < deadline, "the body never subscribed"
        await asyncio.sleep(0.01)
    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=uuid7()))

    chunk = await asyncio.wait_for(first, timeout=5.0)
    assert b"event: reading" in chunk

    await body.aclose()
    assert hub.subscriber_count == 0, "a client that leaves must release its subscription"
