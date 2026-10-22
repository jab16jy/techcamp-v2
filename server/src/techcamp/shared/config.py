"""Environment-driven settings (ADR-0021: one env var switches the execution profile)."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


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


LLM_API_KEY_ENV = "TECHCAMP_LLM_API_KEY"
LLM_BASE_URL_ENV = "TECHCAMP_LLM_BASE_URL"
LLM_MODEL_ENV = "TECHCAMP_LLM_MODEL"

LLM_BASE_URL_DEFAULT = "https://api.deepseek.com"
"""ADR-0026's provisional provider: DeepSeek's OpenAI-compatible endpoint, pinned
for the cost formula and the streaming shape. The real prices of `deepseek-chat`
are T8's, which is why the model name is the only other thing configured here."""

LLM_MODEL_DEFAULT = "deepseek-chat"


def llm_api_key() -> str | None:
    """The provider key, or `None` when there is none.

    No default in either profile, on purpose. A committed default for a provider
    credential would be a live key in the repository (docs/09 §Seguridad,
    "Secretos"), and the seminar profile does not need one: it answers with the
    local stand-in of `assistant.adapters.api.deps`. An unset key in production is
    what the assistant dependency reports as `503`.
    """
    return os.environ.get(LLM_API_KEY_ENV) or None


_default_base_url_host = urlsplit(LLM_BASE_URL_DEFAULT).hostname
if _default_base_url_host is None:  # pragma: no cover - the default is a literal URL
    raise ValueError(f"{LLM_BASE_URL_ENV}'s default must name a host: {LLM_BASE_URL_DEFAULT!r}")

LLM_ALLOWED_BASE_URL_HOSTS: frozenset[str] = frozenset({_default_base_url_host})
"""The hosts `TECHCAMP_LLM_BASE_URL` may name, because the provider **credential**
is sent to whatever host that variable points at (R1-llm-base-url-credential-egress-
unvalidated, docs/09 §Seguridad "Secretos").

Derived from `LLM_BASE_URL_DEFAULT` so the default and the allowlist cannot drift
apart. An allowlist in code and not in the environment: a host that receives the key
is a code change somebody reviews, never a second variable that silently redirects
the credential to a host of its own. The default is the only entry because ADR-0026
pins exactly one provider; a gateway in front of it is a new entry here, with its own
review."""


def llm_base_url() -> str:
    """The provider's endpoint, or a `ValueError` when the key may not be sent there.

    The check is here, at the single reader of the variable, so every caller inherits
    it — including `DeepSeekLLMClient`, which reads this per call. An `http://` URL
    would put the bearer key in clear text on the wire and a host outside
    `LLM_ALLOWED_BASE_URL_HOSTS` would hand the provider's credential to somebody
    else, so both are configuration errors and never a best-effort request.
    """
    raw = os.environ.get(LLM_BASE_URL_ENV, LLM_BASE_URL_DEFAULT)
    _validate_llm_base_url(raw)
    return raw


def _validate_llm_base_url(raw: str) -> None:
    """Raise `ValueError` unless `raw` is an `https` URL to an allowed host.

    Fail-closed on anything unparsable, on a non-`https` scheme, on credentials
    embedded in the URL, and on any host outside the allowlist. The host is compared
    exactly (case-insensitively): a suffix check would accept
    `api.deepseek.com.example`, which is the shape an exfiltration attempt takes.
    """
    try:
        parts = urlsplit(raw)
        host = parts.hostname
    except ValueError as error:  # a malformed port, brackets, a broken netloc
        raise ValueError(f"{LLM_BASE_URL_ENV} is not a URL: {raw!r}") from error
    if parts.scheme != "https":
        raise ValueError(f"{LLM_BASE_URL_ENV} must be an https URL: {raw!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{LLM_BASE_URL_ENV} must not carry credentials: {raw!r}")
    if host is None or host.lower() not in LLM_ALLOWED_BASE_URL_HOSTS:
        raise ValueError(
            f"{LLM_BASE_URL_ENV} must point at a provider the key may be sent to "
            f"({', '.join(sorted(LLM_ALLOWED_BASE_URL_HOSTS))}): {raw!r}"
        )


def llm_model() -> str:
    return os.environ.get(LLM_MODEL_ENV, LLM_MODEL_DEFAULT)


LLM_BUDGET_CAP_COP_ENV = "TECHCAMP_LLM_BUDGET_CAP_COP"
"""The month's budget in COP for the profile that measures money (D-T6.1).

The figure is compared against `SUM(message.cost_cop)` over the local month
(docs/11:132), so it is in COP and whoever configures it owns the USD→COP
conversion, exactly like whoever fills the price card of
`assistant.adapters.api.pricing`."""


def llm_budget_cap_cop() -> Decimal | None:
    """The month's cap in COP, or `None` when the profile does not measure money.

    The profile decides, never the variable alone. `seminar` is the profile that
    does not measure money: its answer is `ModelPrice.local` — zero COP — and it
    never reaches a provider (ADR-0021), so the monthly sum is structurally zero
    and there is no money to cap. `None` says exactly that, and never a converted
    USD 5: the USD 5 total of docs/04:22 and ADR-0021:30 is the provider account's
    ceiling, where money exists, and no documented rate converts it.

    `production` measures money, and the figure is the owner's future amount
    (ADR-0007:17). Unset is `Decimal("0")`, the sense `limits.budget_exhausted`
    already gives a zero cap: a profile that measures money and has no cap stops
    answering with the model instead of answering for free. A value that is not a
    non-negative finite decimal is a configuration error, never a silent zero — and
    so is a variable that is *set* and reads blank: unset is a deployment that
    never configured a cap, while an empty one is an operator mid-edit or a compose
    file that interpolated nothing, and answering that with `0` stops the assistant
    for a month with nothing saying why.
    """
    if is_seminar_profile():
        return None
    raw = os.environ.get(LLM_BUDGET_CAP_COP_ENV)
    if raw is None:
        return Decimal(0)
    if not raw.strip():
        raise ValueError(
            f"{LLM_BUDGET_CAP_COP_ENV} is set but has no figure: "
            f"{raw!r}; unset it for no cap, or set a number of COP"
        )
    try:
        cap = Decimal(raw.strip())
    except InvalidOperation as error:
        raise ValueError(f"{LLM_BUDGET_CAP_COP_ENV} is not a number of COP: {raw!r}") from error
    if not cap.is_finite() or cap < 0:
        raise ValueError(
            f"{LLM_BUDGET_CAP_COP_ENV} must be a non-negative finite number of COP: {raw!r}"
        )
    return cap


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


def s3_internal_url() -> str:
    """The object storage endpoint reached from INSIDE the compose network.

    The browser uploads through `s3_public_url()` because the presigned URL is
    signed for whoever uses it, and that is the farmer's phone (ADR-0018). A
    worker that reads a stored artifact is not that caller: inside a container
    `localhost:9000` is the container itself, not MinIO, so the same public
    URL would point the daily job at nothing. This is the service name on the
    seminar network, where MinIO is an emulator nobody reaches from the outside
    (ADR-0021).

    The public URL is not wrong here, it is unreachable: publishing is for the
    farmer, resolution is for the job. Credentials are shared
    (`s3_access_key()`), only the endpoint differs.
    """
    return os.environ.get("TECHCAMP_S3_INTERNAL_URL", "http://minio:9000")


def s3_ml_bucket() -> str:
    """The bucket holding ML datasets and model artifacts (docs/08-ml.md).

    Separate from the logbook photo bucket on purpose: ADR-0018 keeps photos,
    model artifacts and Postgres backups on the same object storage service but
    in different buckets, so a retention or cleanup rule over one never reaches
    another. Under `models/<name>/<version>/` and `datasets/<name>/<hash>/` a
    released artifact is addressed by version, and the `ml/` project on the
    developer's host uploads there through the public URL (ADR-0021).

    `ml-artifacts`, not the `ml` of docs/08-ml.md: an S3 bucket name is 3 to 63
    characters and the store refuses a shorter one (`InvalidBucketName`, checked
    against the seminar MinIO), so the two-character name could not hold the
    objects that doc describes. Both the doc and this default were changed
    together (D-T9.8).
    """
    return os.environ.get("TECHCAMP_S3_ML_BUCKET", "ml-artifacts")


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
