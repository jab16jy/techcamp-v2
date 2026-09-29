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
    (prime256v1) private key, as `py_vapid` writes it. `Vapid.from_string`
    accepts it (non-32-byte payload routes to `from_der`), pinned by
    `test_a_base64_der_vapid_key_from_the_config_path_signs`. The public half is
    derived from it, never configured separately, so the two can never drift
    apart in the server's own configuration.
    """
    return os.environ.get("TECHCAMP_VAPID_PRIVATE_KEY") or None


def vapid_subject() -> str:
    """The VAPID `sub` claim: how a push service reaches the operator about a
    delivery it could not make. RFC 8292 wants a `mailto:` or an `https:` URI."""
    return os.environ.get("TECHCAMP_VAPID_SUBJECT", "mailto:notificaciones@techcamp.local")


def s3_public_url() -> str:
    """The object storage URL the BROWSER uploads to (ADR-0018, D8).

    Not the in-network one: the presigned URL is signed for whoever uses it, and
    that is the farmer's phone. In the seminar profile that is the MinIO
    container published on localhost (ADR-0021).
    """
    return os.environ.get("TECHCAMP_S3_PUBLIC_URL", "http://localhost:9000")


def s3_bucket() -> str:
    """The bucket holding logbook photos, separate from model artifacts and
    Postgres backups, which share the same service (ADR-0018)."""
    return os.environ.get("TECHCAMP_S3_BUCKET", "logbook-photos")


def s3_access_key() -> str | None:
    """The access key that signs photo uploads, or `None` when there is none.

    The seminar default is the MinIO root user already committed in
    `infra/compose.yaml` for a local emulator nobody authenticates (ADR-0021);
    in production there is deliberately no default, because a committed default
    would be a live credential in the repository (docs/09 §Seguridad, "Secretos").
    An unset key in production is a channel nobody can upload through, which the
    presign dependency reports as 503 rather than inventing credentials.
    """
    default = "techcamp" if is_seminar_profile() else None
    return os.environ.get("TECHCAMP_S3_ACCESS_KEY") or default


def s3_secret_key() -> str | None:
    """The secret half of `s3_access_key()`, with the same profile rule."""
    default = "techcamp123" if is_seminar_profile() else None
    return os.environ.get("TECHCAMP_S3_SECRET_KEY") or default


def s3_region() -> str:
    """The region the signature is made for. SigV4 signs a region even when the
    store is a local emulator, and `us-east-1` is the one every S3-compatible
    provider accepts as its default (ADR-0018)."""
    return os.environ.get("TECHCAMP_S3_REGION", "us-east-1")
