"""The served artifact: fetched from object storage, verified against the row,
and built into the predictor that answers for it (ADR-0020 paso 10;
docs/06-diseno-detallado.md §8; docs/03-modelo-datos.md §`model_version` y
§Integridad del artefacto; docs/adr/0018).

`ml/` writes the artifact under `models/risk_flood/<version>/climatology.json`
in the ML bucket and the registration row that names it; this module is what
serving does with that pair. Three rules, all of them the docs' and none of them
convenient:

* **Verify before parsing.** `artifact_sha256` is checked against the bytes the
  bucket holds, and a mismatch is refused WITHOUT deserializing them
  (docs/03 §Integridad del artefacto: "un objeto cambiado en el bucket no se
  ejecuta"). A row with no `artifact_sha256` is refused too: there is nothing to
  verify against, and skipping the check would make every object in the bucket
  executable.
* **The endpoint is the internal one.** Inside the compose network
  `localhost:9000` is the container itself, not MinIO, so a worker reads through
  `s3_internal_url()` and not the public URL the browser uploads photos to
  (ADR-0018, ADR-0021, `shared/config.py::s3_internal_url`). Addressing is path
  style and the region is signed in, exactly as
  `logbook/adapters/attachments.py::Boto3Presigner` does it.
* **The artifact is loaded once, on first resolve.** `build_registry` performs no
  I/O at all: `PredictorRegistry` asks a registered factory the first time it
  resolves a version and caches what the factory built, so a run walking every
  active cell fetches each artifact once per process
  (`risk/application/ports.py::PredictorRegistry`, ADR-0012 for the retries).

Only `risk_flood` is registered in this lane. Drought (SPI-3) has no model in
code yet, so registering a factory for it would claim a predictor that does not
exist; the event resolves to `None` and the job reports it
(docs/06-diseno-detallado.md §8 "Sin modelo promovido").

The registry carries no `org_id`: `model_version` is global by design
(docs/03-modelo-datos.md §`org_id`: a prediction is a `weather_cell`, shared
reference data) and the isolation lives in `GET /plots/{plot_id}/risk`
(docs/09-cuellos-de-botella.md §Seguridad).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from functools import lru_cache
from typing import Any, Protocol

import boto3
from botocore.config import Config

from techcamp.risk.adapters.climatology import ClimatologyPredictor
from techcamp.risk.application.ports import Predictor, PredictorFactory, PredictorRegistry
from techcamp.risk.domain.models import EventType, ModelVersion
from techcamp.shared.config import (
    s3_access_key,
    s3_internal_url,
    s3_ml_bucket,
    s3_region,
    s3_secret_key,
)

logger = logging.getLogger(__name__)


class ArtifactStore(Protocol):
    """The bytes of one artifact, addressed by the `model_version.artifact_uri`
    that names it (`s3://<bucket>/models/risk_flood/<version>/climatology.json`).

    A protocol because object storage is external I/O and therefore has a test
    double (ADR-0002): `server/tests/risk/test_registry.py` reads from a dict,
    never from MinIO, and no test of the serving path needs the bucket running
    to pin the integrity rule.
    """

    def get(self, uri: str) -> bytes:
        """The exact bytes stored at `uri`.

        A missing object raises, rather than returning an empty body: an artifact
        that is not there is an operational failure the job's retry answers
        (ADR-0012), and empty bytes are what a corrupted or emptied object looks
        like, which the sha256 check is there to catch.
        """
        ...


class S3ArtifactStore:
    """Any S3-compatible store through boto3 (ADR-0018; ADR-0021), reading the
    ML bucket.

    The endpoint is the INTERNAL one (`s3_internal_url()`): a worker resolves
    artifacts over the compose network, where the public URL the browser uploads
    photos to would point at the container itself
    (`shared/config.py::s3_internal_url`). Path style and a signed region are
    the two settings `logbook/adapters/attachments.py` needs for the same
    service, kept here rather than imported: a module may not reach another
    module's `adapters` (docs/05-arquitectura.md §Solo la fachada pública).

    Every argument defaults to the configuration, and the client is built once
    per store: one client per process is what `lru_cache` on `s3_artifact_store`
    gives the worker, the same reason the Open-Meteo adapter is one per process
    (its circuit breaker keeps its state in the instance).
    """

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        bucket: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ) -> None:
        self._bucket = bucket or s3_ml_bucket()
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or s3_internal_url(),
            region_name=region or s3_region(),
            aws_access_key_id=access_key or s3_access_key(),
            aws_secret_access_key=secret_key or s3_secret_key(),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def get(self, uri: str) -> bytes:
        """The bytes of `uri`, whose bucket is the one it names or the configured
        ML bucket when it names none."""
        bucket, key = _split_uri(uri, default_bucket=self._bucket)
        response: dict[str, Any] = self._client.get_object(Bucket=bucket, Key=key)
        return bytes(response["Body"].read())


@lru_cache(maxsize=1)
def s3_artifact_store() -> S3ArtifactStore:
    """The one store this process reads artifacts through.

    One per process, like `_predictors()` in `risk/adapters/jobs.py`: the client
    keeps its connection pool, and the seminar profile has no internet, so the
    artifact comes from the local bucket (ADR-0021).
    """
    return S3ArtifactStore()


def load_artifact(store: ArtifactStore, version: ModelVersion) -> bytes | None:
    """The verified bytes of `version`'s artifact, or `None` when they are not
    those the row registered (docs/03-modelo-datos.md §Integridad del artefacto).

    `None` covers the two refusals and nothing else: a row with no
    `artifact_sha256` to check against, and bytes whose sha256 is not the
    registered one. A store failure is NOT caught here — an object missing from
    the bucket is an operational failure the job's retry answers (ADR-0012), and
    swallowing it would report a version as having no predictor, which is a
    claim the database contradicts.

    Comparison is in constant time and on the lowercase hexdigests, so the
    registered hash is never compared as something else and a stored hash in
    uppercase still verifies.
    """
    registered = (version.artifact_sha256 or "").strip().lower()
    if not registered:
        logger.warning(
            "risk: %s@%s registers no artifact_sha256, so its artifact cannot be verified "
            "and is not loaded",
            version.name,
            version.version,
        )
        return None

    body = store.get(version.artifact_uri)
    digest = hashlib.sha256(body).hexdigest()
    if not hmac.compare_digest(digest, registered):
        logger.warning(
            "risk: refusing the artifact of %s@%s, its sha256 %s is not the registered %s",
            version.name,
            version.version,
            digest,
            registered,
        )
        return None
    return body


def build_registry(store: ArtifactStore) -> PredictorRegistry:
    """The registry the risk job serves from, with the `risk_flood` factory
    registered and nothing else (docs/06-diseno-detallado.md §8; ADR-0020 paso 10).

    No I/O happens here: the artifact is fetched the first time a version is
    resolved and cached by `PredictorRegistry` afterwards, so building the
    registry is safe at import time and a bucket that is down delays the run
    instead of breaking the process.

    The factory is keyed on the event NAME only, and it is given the row: every
    registered `risk_flood` version resolves through it, with its own artifact,
    its own `artifact_sha256` and its own thresholds. Nothing about a version is
    fixed in this code, which is what lets the next registration be served by
    re-resolving rather than by a new release of the server.
    """
    registry = PredictorRegistry()
    registry.register_factory(EventType.FLOOD.version_name, _flood_factory(store))
    return registry


def _flood_factory(store: ArtifactStore) -> PredictorFactory:
    """The factory of every `risk_flood` version, built from its own row.

    `None` when the row's artifact cannot be served, and nothing is cached by
    the registry in that case, so a version whose artifact is refused now and
    registered properly after a re-registration is picked up instead of being
    unanswered for the life of the process
    (`PredictorRegistry.register_factory`).

    A store failure is NOT one of those refusals and propagates: an object
    missing from the bucket is an operational failure the job's retry answers
    (ADR-0012), while a version that verifies but is not this model's artifact
    is a registration fact no retry will change.
    """

    def factory(version: ModelVersion) -> Predictor | None:
        body = load_artifact(store, version)
        if body is None:
            return None
        try:
            predictor = ClimatologyPredictor.from_payload(json.loads(body))
        except ValueError as error:
            # `json.JSONDecodeError` and `UnicodeDecodeError` are both
            # `ValueError`s: bytes that verify their sha256 and are not this
            # model's artifact are refused whole, never read with defaults.
            logger.warning(
                "risk: the artifact of %s@%s is not a served climatology (%s)",
                version.name,
                version.version,
                error,
            )
            return None
        if predictor.version != version.version:
            logger.warning(
                "risk: refusing the artifact of %s@%s, it was fitted for version %s",
                version.name,
                version.version,
                predictor.version,
            )
            return None
        return predictor

    return factory


def _split_uri(uri: str, *, default_bucket: str) -> tuple[str, str]:
    """`(bucket, key)` of `uri`, the bucket being the configured ML bucket when
    the uri names none.

    The registered form is `s3://<bucket>/models/risk_flood/<version>/...`
    (docs/03-modelo-datos.md §`model_version`), where the bucket is a
    deployment fact of the service (ADR-0018 keeps the ML bucket apart from the
    photo bucket, `s3_ml_bucket()`). A uri WITHOUT the scheme is a bare key and
    is read from that bucket: it is the only reading under which "names no
    bucket" has an answer, because with the scheme every first segment would be
    read as a bucket name.
    """
    if uri.startswith("s3://"):
        bucket, _, key = uri[len("s3://") :].partition("/")
        if not key:
            raise ValueError(f"the artifact uri {uri!r} names no object key")
        return bucket or default_bucket, key
    if not uri.lstrip("/"):
        raise ValueError(f"the artifact uri {uri!r} names no object key")
    return default_bucket, uri.lstrip("/")
