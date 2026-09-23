"""FastAPI app entrypoint for the `api` process (ADR-0002)."""

from fastapi import FastAPI

app = FastAPI(title="TechCamp v2")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
