"""Environment-driven settings (ADR-0021: one env var switches the execution profile)."""

from __future__ import annotations

import os


def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://techcamp:techcamp@localhost:5432/techcamp",
    )


def psycopg_conninfo() -> str:
    """`database_url()` without the `+asyncpg` SQLAlchemy driver suffix:
    procrastinate's `PsycopgConnector` (ADR-0012) speaks plain libpq, not a
    SQLAlchemy URL."""
    return database_url().replace("postgresql+asyncpg://", "postgresql://", 1)


def is_seminar_profile() -> bool:
    return os.environ.get("TECHCAMP_PROFILE", "seminar") != "production"


def jwt_issuer() -> str:
    return os.environ.get("TECHCAMP_JWT_ISSUER", "https://seminar.techcamp.local")


def jwt_audience() -> str:
    return os.environ.get("TECHCAMP_JWT_AUDIENCE", "techcamp-api")


def open_meteo_api_key() -> str | None:
    return os.environ.get("OPEN_METEO_API_KEY")
