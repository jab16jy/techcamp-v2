"""Loading and verifying the served artifact (ADR-0020 paso 10;
docs/03-modelo-datos.md §Integridad del artefacto; docs/adr/0018).

T9 makes the registry serve the artifact `ml/` uploaded: the bytes are read
through an `ArtifactStore` double (never MinIO, never the database — ADR-0002:
object storage is external I/O), verified against the row's `artifact_sha256`,
and only then deserialized into the predictor.

What is pinned here is the integrity rule and the caching shape:

* a sha256 that does not match the row's is refused BEFORE anything is parsed
  (docs/03 §Integridad del artefacto: "un objeto cambiado en el bucket no se
  ejecuta"), and the registry then resolves nothing for that version;
* a row with no `artifact_sha256` is refused too: there is nothing to verify
  against, and skipping the check would make every artifact executable;
* the artifact is fetched once per process no matter how many cells the run
  walks (`PredictorRegistry` caches what the factory builds), because the seminar
  profile has no internet and the bucket is local (ADR-0021);
* only `risk_flood` is registered in this lane: the drought model (SPI-3) is not
  in code, and registering it would claim a predictor that does not exist.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

import pytest

from techcamp.risk.adapters.climatology import ClimatologyPredictor
from techcamp.risk.adapters.registry import (
    _split_uri,
    build_registry,
    load_artifact,
)
from techcamp.risk.application.ports import PredictionOutcome
from techcamp.risk.domain.features import seasonality
from techcamp.risk.domain.models import ModelVersion

_VERSION = "2026-10-02"
_URI = f"s3://ml/models/risk_flood/{_VERSION}/climatology.json"
_CREATED = datetime(2026, 10, 2, tzinfo=UTC)


def _payload(*, version: str = _VERSION, kind: str = "climatology") -> bytes:
    """The artifact body `ml/` publishes: `by_month` keyed by month as a
    string, because that is what JSON objects do."""
    return json.dumps(
        {
            "kind": kind,
            "version": version,
            "train_years": [2019, 2020, 2021, 2022],
            "prevalence": 0.13,
            "by_month": {str(month): 0.2 + month / 100 for month in range(1, 13)},
            "fitted_rows": 1404,
            "fitted_positives": 182,
        }
    ).encode()


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _version(
    *,
    body: bytes,
    sha: str | None,
    name: str = "risk_flood",
    version: str = _VERSION,
    uri: str = _URI,
) -> ModelVersion:
    return ModelVersion(
        id=uuid.UUID(int=1),
        name=name,
        version=version,
        artifact_uri=uri,
        is_baseline=True,
        thresholds={},
        promoted=False,
        created_at=_CREATED,
        artifact_sha256=sha,
    )


class _Store:
    """The `ArtifactStore` double: a dict of uri to bytes that counts the reads,
    so a cache can be observed without MinIO."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.reads: list[str] = []

    def get(self, uri: str) -> bytes:
        self.reads.append(uri)
        return self.objects[uri]


def test_load_artifact_returns_the_bytes_of_a_row_whose_sha_matches() -> None:
    """The positive case: the object in the bucket is the one the row names."""
    body = _payload()
    store = _Store({_URI: body})

    loaded = load_artifact(store, _version(body=body, sha=_sha256(body)))

    assert loaded == body
    assert store.reads == [_URI]


def test_an_artifact_whose_sha_differs_is_refused_and_the_version_resolves_nothing() -> None:
    """The central case (docs/03-modelo-datos.md §Integridad del artefacto: "un
    objeto cambiado en el bucket no se ejecuta").

    The registry resolves nothing for that version, so the daily job reports "no
    predictor registered" and writes no prediction, instead of answering with
    frequencies that no train produced.
    """
    store = _Store({_URI: b'{"kind": "climatology", "prevalence": 1.0}'})
    version = _version(body=b"{}", sha=_sha256(b"{}"))

    registry = build_registry(store)

    assert load_artifact(store, version) is None
    assert registry.resolve(version) is None


def test_a_row_without_a_sha_is_refused_too() -> None:
    """`artifact_sha256` is `null` on a row that was never verified: there is
    nothing to check the bytes against, and accepting them would make every
    object in the bucket executable."""
    body = _payload()
    store = _Store({_URI: body})
    version = _version(body=body, sha=None)

    assert load_artifact(store, version) is None
    assert build_registry(store).resolve(version) is None


def test_the_registry_serves_the_registered_flood_version() -> None:
    """`build_registry` registers the `risk_flood` factory, so the served
    baseline row resolves to the predictor its own artifact describes."""
    body = _payload()
    store = _Store({_URI: body})

    predictor = build_registry(store).resolve(_version(body=body, sha=_sha256(body)))

    assert isinstance(predictor, ClimatologyPredictor)
    assert predictor.version == _VERSION
    assert predictor.by_month[10] == pytest.approx(0.3)
    month_sin, month_cos = seasonality(10)
    outcome = predictor.predict(
        _version(body=body, sha=_sha256(body)), {"month_sin": month_sin, "month_cos": month_cos}
    )
    assert isinstance(outcome, PredictionOutcome)
    assert outcome.probability == pytest.approx(0.3)


def test_the_registry_resolves_no_drought_predictor() -> None:
    """SPI-3 is not in code in this lane, so nothing answers for
    `risk_drought`: the job reports it and writes no prediction
    (docs/06-diseno-detallado.md §8 "Sin modelo promovido")."""
    body = _payload()
    store = _Store({_URI: body})

    registry = build_registry(store)

    assert registry.resolve(_version(body=body, sha=_sha256(body), name="risk_drought")) is None


def test_another_flood_version_resolves_because_the_factory_reads_the_row() -> None:
    """The factory is given the row, not a version fixed in code: whatever
    `model_version` serves gets its own artifact (docs/03 §`model_version`).

    The second read is the one this test asks for; a version fixed in the
    factory would either answer with the first row's artifact or with a hash of
    a key the row does not name.
    """
    first, second = _payload(), _payload(version="2026-11-01")
    store = _Store({_URI: first, "s3://ml/models/risk_flood/2026-11-01/climatology.json": second})

    registry = build_registry(store)
    first_predictor = registry.resolve(_version(body=first, sha=_sha256(first)))
    second_predictor = registry.resolve(
        _version(
            body=second,
            sha=_sha256(second),
            version="2026-11-01",
            uri="s3://ml/models/risk_flood/2026-11-01/climatology.json",
        )
    )

    assert isinstance(first_predictor, ClimatologyPredictor)
    assert isinstance(second_predictor, ClimatologyPredictor)
    assert first_predictor is not second_predictor
    assert second_predictor.version == "2026-11-01"


def test_the_artifact_is_read_once_however_often_the_version_resolves() -> None:
    """One artifact per process: `PredictorRegistry` caches what the factory
    builds, so a run walking every cell fetches it once (ADR-0021, seminar
    profile)."""
    body = _payload()
    store = _Store({_URI: body})
    registry = build_registry(store)
    version = _version(body=body, sha=_sha256(body))

    first = registry.resolve(version)
    second = registry.resolve(version)

    assert first is second
    assert store.reads == [_URI]


def test_an_artifact_whose_payload_is_not_a_climatology_is_refused() -> None:
    """The reader requires `kind == "climatology"`: bytes that verify their
    sha256 but name another kind are still not this predictor's artifact, and
    the registry resolves nothing for the row."""
    body = _payload(kind="heuristic")
    store = _Store({_URI: body})

    assert build_registry(store).resolve(_version(body=body, sha=_sha256(body))) is None


def test_an_artifact_that_is_not_json_is_refused() -> None:
    """Bytes that verify their sha256 and are still not this model's artifact
    are refused whole, never read with defaults filled in."""
    body = b"not json at all"
    store = _Store({_URI: body})

    assert build_registry(store).resolve(_version(body=body, sha=_sha256(body))) is None


def test_a_payload_that_names_another_version_is_refused() -> None:
    """The payload carries the version string of the row it belongs to: an
    artifact of another version would answer with frequencies fitted for a
    version whose thresholds and data split are not the ones being served
    (docs/08-ml.md §M2 "Severidad")."""
    body = _payload(version="2026-09-01")
    store = _Store({_URI: body})

    assert build_registry(store).resolve(_version(body=body, sha=_sha256(body))) is None


def test_the_bucket_of_an_uri_is_the_one_it_names_or_the_configured_one() -> None:
    """`s3_ml_bucket()` is a deployment fact (ADR-0018 keeps the ML bucket apart
    from the photo bucket), so a bare key is read from that bucket, while the
    registered `s3://<bucket>/...` form is read from the bucket it names."""
    assert _split_uri(_URI, default_bucket="ml") == (
        "ml",
        f"models/risk_flood/{_VERSION}/climatology.json",
    )
    assert _split_uri("models/risk_flood/x/climatology.json", default_bucket="ml") == (
        "ml",
        "models/risk_flood/x/climatology.json",
    )
    assert _split_uri("s3://other-bucket/models/flood.json", default_bucket="ml") == (
        "other-bucket",
        "models/flood.json",
    )


def test_an_uri_that_names_no_object_key_is_refused() -> None:
    """A bucket with no key is not an artifact; reading it would ask the store
    for a listing and take whatever it returned as the model's bytes."""
    with pytest.raises(ValueError, match="names no object key"):
        _split_uri("s3://ml", default_bucket="ml")


def test_a_factory_that_cannot_answer_caches_nothing() -> None:
    """An object that is not in the bucket is an operational failure, not a
    predictor that answers `None`: the store's error reaches the job, which
    retries it (ADR-0012), and because nothing was cached an artifact that
    lands later is picked up instead of being refused for the life of the
    process (`PredictorRegistry.register_factory`)."""
    body = _payload()
    store = _Store({})
    registry = build_registry(store)
    version = _version(body=body, sha=_sha256(body))

    with pytest.raises(KeyError):
        registry.resolve(version)

    store.objects[_URI] = body

    assert isinstance(registry.resolve(version), ClimatologyPredictor)


# --- what the worker actually calls (`risk/adapters/jobs.py::_predictors`) ---


class _CountingStore:
    """A store that records every `get`, so "fetched once per process" is
    observed rather than asserted from the code."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    def get(self, uri: str) -> bytes:
        self.reads.append(uri)
        return self.objects[uri]


def test_the_worker_builds_its_registry_without_touching_object_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`jobs._predictors()` is what the daily task calls, and it must not reach
    MinIO while it is being built: it runs inside `predict_active_cells`, and the
    artifact is only named by the `model_version` row that run resolves per event.
    A registry that fetched at build time would do I/O before the first
    transaction closes and would break the tests that monkeypatch it
    (#240 R3-global-seam-leak)."""
    import techcamp.risk.adapters.jobs as jobs_module

    store = _CountingStore({})
    monkeypatch.setattr(jobs_module, "s3_artifact_store", lambda: store)

    jobs_module._predictors.cache_clear()
    try:
        # Built and asserted unused on purpose: the claim under test is that
        # BUILDING it reaches no object storage, so nothing is read here at all.
        jobs_module._predictors()
    finally:
        jobs_module._predictors.cache_clear()

    assert store.reads == []


def test_the_worker_registry_serves_the_flood_version_it_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end of the chain T9 closes: the registry the job holds resolves the
    registered `risk_flood` version into a predictor that answers a probability,
    and the artifact it needed is read exactly once for two resolutions
    (docs/06-diseno-detallado.md §8; docs/08-ml.md §M2 "Línea base servida")."""
    import techcamp.risk.adapters.jobs as jobs_module

    body = _payload()
    store = _CountingStore({_URI: body})
    monkeypatch.setattr(jobs_module, "s3_artifact_store", lambda: store)

    jobs_module._predictors.cache_clear()
    try:
        registry = jobs_module._predictors()
        version = _version(body=body, sha=_sha256(body))

        first = registry.resolve(version)
        second = registry.resolve(version)
    finally:
        jobs_module._predictors.cache_clear()

    assert isinstance(first, ClimatologyPredictor)
    assert second is first
    assert store.reads == [_URI]


def test_the_worker_registry_resolves_no_drought_predictor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative of the lane: drought has no model in code (SPI-3 does not
    exist yet), so the worker's registry resolves nothing for it and the job logs
    the skip instead of inventing a probability (docs/06-diseno-detallado.md §8
    "Sin modelo promovido")."""
    import techcamp.risk.adapters.jobs as jobs_module

    store = _CountingStore({})
    monkeypatch.setattr(jobs_module, "s3_artifact_store", lambda: store)

    jobs_module._predictors.cache_clear()
    try:
        registry = jobs_module._predictors()
        drought = _version(name="risk_drought", body=_payload(), sha="0" * 64)
    finally:
        jobs_module._predictors.cache_clear()

    assert registry.resolve(drought) is None
    assert store.reads == []
