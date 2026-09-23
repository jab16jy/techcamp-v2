"""FastAPI app entrypoint for the `api` process (ADR-0002)."""

from fastapi import FastAPI

from techcamp.shared.config import is_seminar_profile
from techcamp.shared.errors import register_error_handlers

app = FastAPI(title="TechCamp v2")
register_error_handlers(app)

if is_seminar_profile():
    # /dev routes only exist in the seminar profile (ADR-0021).
    from techcamp.identity.adapters.api.dev_auth import router as dev_auth_router

    app.include_router(dev_auth_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
