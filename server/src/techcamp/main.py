"""FastAPI app entrypoint for the `api` process (ADR-0002)."""

from fastapi import FastAPI

from techcamp.farms.adapters.api.router import router as farms_router
from techcamp.identity.adapters.api.router import router as identity_router
from techcamp.shared.config import is_seminar_profile
from techcamp.shared.errors import register_error_handlers

app = FastAPI(title="TechCamp v2")
register_error_handlers(app)
app.include_router(identity_router)
app.include_router(farms_router)

if is_seminar_profile():
    # /dev routes only exist in the seminar profile (ADR-0021).
    from techcamp.identity.adapters.api.dev_auth import router as dev_auth_router

    app.include_router(dev_auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
