"""FastAPI app entrypoint for the `api` process (ADR-0002)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from techcamp.farms.adapters.api.router import router as farms_router
from techcamp.identity.adapters.api.router import router as identity_router
from techcamp.shared.config import is_seminar_profile
from techcamp.shared.errors import register_error_handlers
from techcamp.telemetry.adapters.api.router import router as telemetry_router
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub
from techcamp.weather.adapters.api.router import router as weather_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """One `LISTEN plot_events` connection for the process lifetime
    (docs/04-api.md:180-189, ADR-0015), fanned out to SSE clients."""
    hub = PlotEventsHub()
    await hub.start()
    app.state.plot_events_hub = hub
    try:
        yield
    finally:
        await hub.stop()


app = FastAPI(title="TechCamp v2", lifespan=lifespan)
register_error_handlers(app)

# The REST API is served under /api/v1 (docs/04-api.md: "Versionado: por ruta").
app.include_router(identity_router, prefix="/api/v1")
app.include_router(farms_router, prefix="/api/v1")
app.include_router(telemetry_router, prefix="/api/v1")
app.include_router(weather_router, prefix="/api/v1")

if is_seminar_profile():
    # /dev routes only exist in the seminar profile (ADR-0021); still part of
    # the versioned REST API, so they get the same /api/v1 prefix.
    from techcamp.identity.adapters.api.dev_auth import router as dev_auth_router
    from techcamp.weather.adapters.api.dev_jobs import router as dev_weather_jobs_router

    app.include_router(dev_auth_router, prefix="/api/v1")
    app.include_router(dev_weather_jobs_router, prefix="/api/v1")


@app.get("/health")
def health() -> dict[str, str]:
    # Unversioned: an operational probe, not a resource of the REST API
    # (docs/04-api.md's "Operación" section lists /healthz/readyz/metrics
    # separately from the /api/v1 endpoint list). Nothing in infra/compose.yaml
    # or server/Dockerfile defines a container healthcheck that pins this path,
    # so moving it carries no infra risk either way; kept unprefixed since it
    # isn't a versioned resource.
    return {"status": "ok"}
