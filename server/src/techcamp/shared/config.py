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


def vapid_private_key() -> str | None:
    """The server's half of the VAPID key pair, or `None` when there is none.

    docs/04:143 leaves the pair to the server's configuration and requires it to
    be the same pair the browser bundle was built with, which reaches the client
    as the build-time `VITE_VAPID_PUBLIC_KEY` (D32). There is deliberately no
    default: a committed default would be a private key in the repository, and a
    generated one could never match a bundle built without it. An unset key
    leaves the `push` channel unregistered instead — the same soft failure the
    web client already has when its variable is unset, and the same shape as D31's
    rule for a channel nobody can send yet.

    The value is what `pywebpush` signs with: the base64 DER of the EC2
    (prime256v1) private key, as `py_vapid` writes it. The public half is
    derived from it, never configured separately, so the two can never drift
    apart in the server's own configuration.
    """
    return os.environ.get("TECHCAMP_VAPID_PRIVATE_KEY") or None


def vapid_subject() -> str:
    """The VAPID `sub` claim: how a push service reaches the operator about a
    delivery it could not make. RFC 8292 wants a `mailto:` or an `https:` URI."""
    return os.environ.get("TECHCAMP_VAPID_SUBJECT", "mailto:notificaciones@techcamp.local")
